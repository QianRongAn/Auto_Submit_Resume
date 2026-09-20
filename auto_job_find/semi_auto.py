"""
半自动投递主入口：python semi_auto.py（或双击 start_semi_auto.bat）

和全自动模式的本质区别 —— 每一条话术发送前，都必须由你本人确认：

    1. 脚本打开岗位列表页（你验证过的那个网页）
    2. 逐个岗位：读取 JD → AI 为这个岗位现写定制话术（角度轮换 + 防重复）
    3. 话术打印在终端，你过目：
         y = 发送这一条
         n = 跳过这个岗位
         q = 立刻收工
    4. 只有你按了 y，脚本才会点「立即沟通」并把话术发出去

安全护栏（触发任何一条就立刻停，改为你手动操作）：
    - 弹出安全验证 → 停
    - 登录失效/被踢到登录页 → 停
    - 聊天框打不开（可能被弹验证）→ 跳过该岗位并提醒
    - 当天发送量达到 DAILY_CAP → 停
    - 每两条之间随机等待 SEND_MIN~SEND_MAX 秒
"""

import os
import sys
import json
import time
import random
import traceback
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException, StaleElementReferenceException

import finding_jobs
import resume_ai
import letter_variety
from resume_ai import read_resumes, build_client, check_config
from write_response import send_text_to_chat_box, HOME_URL, LOG_DIR
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

JOB_URL = os.getenv("JOB_URL") or "https://www.zhipin.com/web/geek/jobs?city=100010000"
BROWSER = (os.getenv("BROWSER") or "edge").lower()
LETTER_LIMIT = int(os.getenv("LETTER_LIMIT") or "300")
DAILY_CAP = int(os.getenv("DAILY_CAP") or "20")
SEND_MIN = int(os.getenv("SEND_MIN_INTERVAL") or "20")
SEND_MAX = int(os.getenv("SEND_MAX_INTERVAL") or "60")
INCLUDE_KW = [k.strip() for k in (os.getenv("INCLUDE_KEYWORDS") or "").split(",") if k.strip()]
EXCLUDE_KW = [k.strip() for k in (os.getenv("EXCLUDE_KEYWORDS") or "").split(",") if k.strip()]

APPLIED_PATH = os.path.join(LOG_DIR, "applied.json")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


# ---------------------------------------------------------------------------
# 投递记录（去重 + 每天上限都靠它）
# ---------------------------------------------------------------------------
def load_history():
    if os.path.exists(APPLIED_PATH):
        try:
            with open(APPLIED_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_history(hist):
    with open(APPLIED_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)


def sent_titles_today(hist):
    today = f"{datetime.now():%Y-%m-%d}"
    return {r.get("title", "") for r in hist.get(today, []) if r.get("status") == "sent"}


def sent_count_today(hist):
    today = f"{datetime.now():%Y-%m-%d}"
    return sum(1 for r in hist.get(today, []) if r.get("status") == "sent")


def record(hist, title, company, letter, status):
    today = f"{datetime.now():%Y-%m-%d}"
    hist.setdefault(today, []).append({
        "title": title, "company": company, "letter": letter,
        "status": status, "at": f"{datetime.now():%H:%M:%S}",
    })
    save_history(hist)


# ---------------------------------------------------------------------------
# 页面操作
# ---------------------------------------------------------------------------
def parse_card(el):
    """从卡片文本里猜职位名和公司名（第一行职位、含'公司/科技/有限'的行是公司）。"""
    lines = [l.strip() for l in (el.text or "").splitlines() if l.strip()]
    title = lines[0] if lines else ""
    company = next((l for l in lines[1:] if any(k in l for k in
                   ("公司", "科技", "有限", "集团", "研究院", "工作室"))), "")
    return title, company


def open_job_detail(driver, el):
    """点击列表里的第 i 个卡片，等右侧详情加载。返回详情文本或 ''。"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except WebDriverException:
        pass
    try:
        el.click()
    except WebDriverException:
        try:
            driver.execute_script("arguments[0].click();", el)
        except WebDriverException:
            return ""
    time.sleep(random.uniform(2.5, 4))
    return finding_jobs.read_detail_panel(driver)


def send_one(driver, letter):
    """点「立即沟通」→ 填话术 → 回车发送。返回 True/False。"""
    btn = finding_jobs.get_contact_button(driver)
    if btn is None:
        log("  ✗ 没找到「立即沟通」按钮（可能已沟通过）")
        return False
    try:
        driver.execute_script("arguments[0].click();", btn)
    except WebDriverException:
        btn.click()
    time.sleep(3)

    box = finding_jobs.wait_chat_input(driver, timeout=30)
    if box is None:
        reason = finding_jobs.safety_stop(driver)
        log("  ✗ 聊天框没出现" + (f"：{reason}" if reason else "，跳过这个岗位"))
        return False

    if not send_text_to_chat_box(driver, box, letter):
        log("  ✗ 话术写入聊天框失败，未发送")
        return False
    time.sleep(1.5)
    try:
        box.send_keys(Keys.ENTER)
    except WebDriverException:
        driver.execute_script(
            "arguments[0].dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));", box)
    log("  ✓ 已发送")
    time.sleep(random.uniform(3, 5))
    try:
        driver.back()
        time.sleep(3)
    except WebDriverException:
        pass
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def ask(prompt):
    while True:
        ans = input(prompt).strip().lower()
        if ans in ("y", "n", "q"):
            return ans
        print("  请输入 y（发送）/ n（跳过）/ q（收工）")


def run():
    problems = check_config()
    if problems:
        log("启动前检查未通过：")
        for p in problems:
            log("  - " + p)
        return

    hist = load_history()
    already = sent_count_today(hist)
    log(f"今天已发送 {already}/{DAILY_CAP} 条")
    if already >= DAILY_CAP:
        log("已达今日上限，收工。想继续请调大 .env 的 DAILY_CAP（不建议）")
        return

    resume_text = read_resumes()
    client = build_client()
    history_letters = [r["letter"] for day in hist.values() for r in day
                       if r.get("status") == "sent"]

    log(f"打开岗位列表：{JOB_URL}")
    driver = finding_jobs.open_browser_with_options(JOB_URL, BROWSER, reuse=False)
    if driver is None:
        log("浏览器未启动，退出")
        return

    reason = finding_jobs.safety_stop(driver)
    if reason:
        log(f"⛔ {reason}")
        log("脚本已停止，浏览器保持打开，请手动处理后重跑本脚本")
        return
    if not finding_jobs.is_logged_in(driver):
        log("⛔ 当前未登录。请在弹出的浏览器里登录 BOSS 直聘，然后重跑本脚本")
        return

    index = 0
    sent_this_run = 0
    while sent_this_run + already < DAILY_CAP:
        reason = finding_jobs.safety_stop(driver)
        if reason:
            log(f"⛔ {reason}（已发送 {sent_this_run} 条，浏览器保持打开）")
            return

        xp, cards = finding_jobs.detect_cards(driver)
        if not cards:
            log("列表里没探测到岗位卡片。可能页面还没加载完或已翻到底，脚本停止")
            return
        if index >= len(cards):
            log(f"列表共 {len(cards)} 个岗位已全部过完，收工")
            return

        try:
            card = cards[index]
            title, company = parse_card(card)
        except StaleElementReferenceException:
            index += 1
            continue

        if not title:
            index += 1
            continue
        if title in sent_titles_today(hist):
            log(f"[{index+1}] {title} —— 今天已投过，跳过")
            index += 1
            continue

        detail = open_job_detail(driver, card)
        index += 1
        if not detail or len(detail) < 50:
            log(f"[{index}] {title} —— 详情没读到，跳过")
            continue

        hit_ex = [k for k in EXCLUDE_KW if k in title or k in detail[:500]]
        if hit_ex:
            log(f"[{index}] {title} —— 命中排除词 {'、'.join(hit_ex)}，跳过")
            continue
        if INCLUDE_KW and not any(k in title or k in detail[:500] for k in INCLUDE_KW):
            log(f"[{index}] {title} —— 不含任何目标关键词，跳过")
            continue

        log(f"[{index}] {title}（{company or '公司未知'}）—— 生成定制话术…")
        try:
            letter, angle = letter_variety.generate_unique_letter(
                lambda r, jd, lim, angle_hint=None, avoid_hint=None:
                    resume_ai.generate_letter(client, r, jd, lim,
                                              angle_hint=angle_hint, avoid_hint=avoid_hint),
                resume_text, detail, LETTER_LIMIT, history_letters, log=log)
        except Exception as e:
            log(f"  ✗ 生成失败：{e}")
            continue
        if not letter:
            log("  ✗ 生成结果为空，跳过")
            continue

        print("\n" + "=" * 62)
        print(f"岗位：{title} ｜ {company or '公司未知'}")
        print(f"话术（{len(letter)} 字，角度：{angle}）：")
        print(f"  {letter}")
        print("=" * 62)

        ans = ask("发送这条？ y=发 / n=跳过 / q=收工 > ")
        if ans == "q":
            log("收到，收工")
            return
        if ans == "n":
            record(hist, title, company, letter, "skipped")
            log("已跳过")
        else:
            ok = send_one(driver, letter)
            record(hist, title, company, letter, "sent" if ok else "failed")
            if ok:
                sent_this_run += 1
                history_letters.append(letter)
            left = DAILY_CAP - already - sent_this_run
            log(f"今日剩余额度 {left} 条")

        if sent_this_run + already < DAILY_CAP:
            wait = random.uniform(SEND_MIN, SEND_MAX)
            log(f"歇 {wait:.0f} 秒再继续（避免节奏太机械）…")
            time.sleep(wait)

    log(f"本轮结束：发送 {sent_this_run} 条。投递记录见 {APPLIED_PATH}")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("\n手动中断，收工")
    except Exception:
        log("运行异常：\n" + traceback.format_exc())
    input("\n按回车关闭本窗口（浏览器不会被关闭）...")
