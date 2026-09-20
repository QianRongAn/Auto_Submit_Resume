"""
列表探查器：只导航一次，把你给的页面「如实汇报」，不做任何投递动作。

用法（三选一，优先级从高到低）：
  1) python peek_list.py "https://www.zhipin.com/web/geek/job?query=..."
  2) 把链接写进 logs/url.txt（新建一行粘贴即可）
  3) 都不给 → 读 .env 的 JOB_URL

加 --history 参数：不打开网页，直接从你日常 Edge 的浏览历史里
捞最近访问过的 zhipin.com 链接（前提是 Edge 没锁住历史文件）。

它不会刷新页面、不会点按钮、不会投递。打开后保持浏览器不关，方便你亲眼看。
"""

import os
import sys
import time
import shutil
import sqlite3
import subprocess
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

import finding_jobs
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
BROWSER = (os.getenv("BROWSER") or "edge").lower()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


# ---------------------------------------------------------------------------
# 从日常 Edge 的浏览历史里找最近看过的 BOSS 链接
# ---------------------------------------------------------------------------
def recent_boss_urls(limit=15):
    src = os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History")
    if not os.path.exists(src):
        log(f"找不到 Edge 历史文件：{src}")
        return []
    tmp = os.path.join(LOG_DIR, "_history_copy.db")
    try:
        shutil.copy2(src, tmp)
    except Exception as e:
        log(f"历史文件被占用，读不了（{e}）。可以先关掉 Edge 再试。")
        return []

    rows = []
    try:
        con = sqlite3.connect(tmp)
        cur = con.cursor()
        # Edge 用 WebKit 时间戳：1601-01-01 起的微秒数
        cur.execute(
            "SELECT url, title, last_visit_time FROM urls "
            "WHERE url LIKE '%zhipin.com%' "
            "ORDER BY last_visit_time DESC LIMIT ?", (limit,))
        for url, title, ts in cur.fetchall():
            try:
                when = datetime(1601, 1, 1) + __import__("datetime").timedelta(microseconds=ts)
                when = when.strftime("%m-%d %H:%M")
            except Exception:
                when = "?"
            rows.append((when, title or "", url))
        con.close()
    except Exception as e:
        log(f"读历史失败：{e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return rows


# ---------------------------------------------------------------------------
# 页面里到底有几个岗位卡片
# ---------------------------------------------------------------------------
CARD_PROBES = [
    ("job-card-box", "//li[contains(@class,'job-card-box')]"),
    ("job-card-wrapper", "//li[contains(@class,'job-card-wrapper')]"),
    ("job-card(任意)", "//li[contains(@class,'job-card')]"),
    ("card-item", "//li[contains(@class,'card-item')]"),
    ("div.job-card", "//div[contains(@class,'job-card')]"),
    ("job-list-box li", "//div[contains(@class,'job-list-box')]//li"),
    ("ul.job-list li", "//ul[contains(@class,'job-list')]/li"),
    ("推荐流 rec-job", "//*[contains(@class,'rec-job')]"),
    ("a[href*='/job_detail/']", "//a[contains(@href,'/job_detail/')]"),
    ("a[href*='jobDetail']", "//a[contains(@href,'jobDetail')]"),
]


def probe(driver):
    log("—— 岗位卡片探测 ——")
    best = (0, None, [])
    for name, xp in CARD_PROBES:
        try:
            els = driver.find_elements(By.XPATH, xp)
        except WebDriverException:
            els = []
        n = len(els)
        mark = "  ← 命中" if n else ""
        log(f"  {name:<22} {n:>3} 个{mark}")
        if n > best[0]:
            texts = []
            for el in els[:30]:
                try:
                    t = (el.text or "").replace("\n", " | ").strip()
                except WebDriverException:
                    t = ""
                if t:
                    texts.append(t[:100])
            best = (n, xp, texts)
    return best


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if "--history" in flags:
        log("最近访问过的 BOSS 页面：")
        for when, title, url in recent_boss_urls():
            log(f"  {when}  {title[:30]}")
            log(f"      {url}")
        log("把上面任意一条链接粘给我（或写进 logs/url.txt）即可。")
        return

    url = args[0].strip() if args else ""
    if not url:
        p = os.path.join(LOG_DIR, "url.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                url = f.read().strip()
            log(f"用 logs/url.txt 里的链接：{url}")
    if not url:
        url = os.getenv("JOB_URL")
        log(f"用 .env 的 JOB_URL：{url}")
    if not url:
        log("没有链接可用。请粘一个给我，或写进 logs/url.txt。")
        return

    finding_jobs.disable_local_proxy()
    log(f"打开：{url}（只导航一次，不刷新）")
    finding_jobs.open_browser_with_options(url, BROWSER)
    driver = finding_jobs.get_driver()
    if driver is None:
        log("浏览器没起来，退出")
        return

    time.sleep(8)   # 一次等到渲染完，避免反复刷新

    log(f"落地 URL ：{driver.current_url}")
    log(f"页面标题 ：{driver.title}")
    try:
        log(f"登录态   ：{'已登录' if finding_jobs.is_logged_in(driver) else '未登录/不确定'}")
    except Exception as e:
        log(f"登录态判定失败：{e}")
    try:
        log(f"验证页   ：{'是（需人工过验证）' if finding_jobs.is_on_verify_page(driver) else '否'}")
    except Exception:
        pass

    n, xp, texts = probe(driver)

    if n:
        log(f"\n—— 列表前 {len(texts)} 条 ——")
        for i, t in enumerate(texts, 1):
            log(f"  {i:>2}. {t}")
    else:
        log("\n⚠ 一个卡片都没命中。把页面结构和 HTML 快照存下来给你看：")
        try:
            body = driver.execute_script(
                "return document.body ? document.body.innerText.slice(0,1500) : '';")
            log("页面可见文本开头：")
            for line in (body or "").splitlines()[:25]:
                if line.strip():
                    log("    " + line.strip()[:90])
        except Exception:
            pass

    try:
        html = driver.page_source
        snap = os.path.join(LOG_DIR, "snapshot.html")
        with open(snap, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"\nHTML 快照：{snap}（{len(html)} 字符）")
    except Exception as e:
        log(f"存快照失败：{e}")

    try:
        shot = os.path.join(LOG_DIR, f"peek_{int(time.time())}.png")
        driver.save_screenshot(shot)
        log(f"截图：{shot}")
    except Exception:
        pass

    if n:
        log(f"\n✓ 找到 {n} 个岗位。下一步：把这条链接写进 .env 的 JOB_URL，"
            f"再跑 write_response.py（DRY_RUN=2 演练）。")
    else:
        log("\n下一步：把 snapshot.html 或截图发我，我照着真实 DOM 改选择器。")

    # 保持浏览器打开，等人工确认后再收尾
    go = os.path.join(LOG_DIR, "CONTINUE")
    log("\n浏览器保持打开。确认完毕后在 logs/ 下建一个 CONTINUE 文件，我就收尾。")
    start = time.time()
    while time.time() - start < 900:
        if os.path.exists(go):
            try:
                os.remove(go)
            except OSError:
                pass
            break
        time.sleep(2)
    try:
        driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
