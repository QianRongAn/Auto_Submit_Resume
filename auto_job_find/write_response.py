"""
批量投简历主入口：python write_response.py

流程：读简历 PDF → 打开 Boss 直聘 → 扫码登录 → 逐个岗位抓描述
      → 调大模型写打招呼语 → 点「立即沟通」→ 粘贴发送 → 返回列表继续

所有可调参数都在 .env 里，改参数不用改代码。
"""

import os
import sys
import time
import random
import json
import traceback
from datetime import datetime

# 让 print 实时落盘、并按 UTF-8 输出。否则重定向到文件时是块缓冲，
# 出问题看不到现场，中文还会变成乱码。
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException

import finding_jobs
import resume_ai
from resume_ai import read_resumes, build_client, generate_letter, check_config
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ---------------------------------------------------------------------------
# 运行参数（都可在 .env 中覆盖）
# ---------------------------------------------------------------------------
HOME_URL = "https://www.zhipin.com/"   # 登录入口页
JOB_URL = os.getenv("JOB_URL") or "https://www.zhipin.com/web/geek/job-recommend?ka=header-job-recommend"
BROWSER = (os.getenv("BROWSER") or "chrome").lower()
EXPECT_LABEL = os.getenv("EXPECT_LABEL") or ""      # 期望职位筛选，留空=不筛选
MAX_APPLY = int(os.getenv("MAX_APPLY") or "20")     # 本轮最多投递几个，安全第一
MAX_SCAN = int(os.getenv("MAX_SCAN") or "60")       # 最多扫多少个列表项
# 0 = 真发；1 = 只生成文案不打开聊天框；2 = 演练：打开聊天框、填好文字、截图，但不按回车发送
DRY_RUN = int(os.getenv("DRY_RUN") or "0")
LETTER_LIMIT = int(os.getenv("LETTER_LIMIT") or "300")
# 等待人工完成「安全验证 + 登录」的秒数
LOGIN_WAIT = int(os.getenv("LOGIN_WAIT") or "900")
# 1 = 接入手动启动的浏览器（推荐，能绕开风控）；0 = 脚本自己启动浏览器
ATTACH_MODE = int(os.getenv("ATTACH_MODE") or "1")
ATTACH_PORT = int(os.getenv("ATTACH_PORT") or "9222")
# 1 = 跑完后保持浏览器打开并挂起等待（脚本退出会连带回收浏览器）
HOLD_BROWSER = int(os.getenv("HOLD_BROWSER") or "0")

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f"apply_{datetime.now():%Y%m%d_%H%M%S}.log")


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 聊天框操作
# ---------------------------------------------------------------------------
def send_text_to_chat_box(driver, box, text):
    """往输入框里写文字。contenteditable 的 div 用 clear() 可能报错，所以做多层兜底。"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
    except WebDriverException:
        pass
    try:
        box.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", box)

    try:
        box.clear()
    except Exception:
        pass

    # 逐段发送，长文本一次 send_keys 有时会被截断
    try:
        for i in range(0, len(text), 60):
            box.send_keys(text[i:i + 60])
            time.sleep(0.15)
        return True
    except WebDriverException:
        pass

    # 兜底：直接改 DOM（contenteditable）
    try:
        driver.execute_script(
            "arguments[0].focus();"
            "arguments[0].innerText = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));",
            box, text,
        )
        return True
    except WebDriverException:
        return False


def send_response_and_go_back(driver, response, wait_after=8):
    box = finding_jobs.wait_chat_input(driver, timeout=40)
    if box is None:
        log("  ✗ 聊天输入框没出现，跳过这个岗位")
        return False

    if not send_text_to_chat_box(driver, box, response):
        log("  ✗ 写入聊天框失败，跳过")
        return False

    time.sleep(2)

    # 演练模式：文字已填好，截图存证，到此为止 —— 不按回车
    if DRY_RUN == 2:
        shot = os.path.join(LOG_DIR, f"dryrun_{int(time.time())}.png")
        try:
            driver.save_screenshot(shot)
            log(f"  ◎ 演练模式：文字已填入聊天框，截图 {os.path.basename(shot)}（未发送）")
        except WebDriverException:
            log("  ◎ 演练模式：文字已填入聊天框（截图失败，未发送）")
        time.sleep(2)
        try:
            driver.back()
            time.sleep(3)
        except WebDriverException:
            pass
        return True

    try:
        box.send_keys(Keys.ENTER)
    except WebDriverException:
        driver.execute_script(
            "arguments[0].dispatchEvent(new KeyboardEvent('keydown',"
            "{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true}));", box)
    log("  ✓ 已发送")
    time.sleep(wait_after)

    try:
        driver.back()
        time.sleep(3)
    except WebDriverException:
        pass
    return True


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run():
    problems = check_config()
    if problems:
        log("启动前检查未通过：")
        for p in problems:
            log("  - " + p)
        return

    mode_desc = {0: "真实投递", 1: "试跑（只生成文案）", 2: "演练（填好文字但不发送）"}[DRY_RUN]
    log(f"模型：{resume_ai.OPENAI_MODEL} @ {resume_ai.OPENAI_BASE_URL}")
    log(f"模式：{mode_desc}，上限 {MAX_APPLY} 个")

    resume_text = read_resumes()
    client = build_client()

    # 优先接入「用户手动打开并登录好」的浏览器（start_browser.bat 启动的那个）。
    # 这条路径没有 webdriver 启动痕迹，不会被 BOSS 的风控拦。
    attached = False
    if ATTACH_MODE:
        if finding_jobs.browser_alive(ATTACH_PORT):
            try:
                finding_jobs.attach_to_existing(ATTACH_PORT)
                attached = True
                log(f"当前页面：{finding_jobs.get_driver().current_url}")
                # 手动浏览器可能停在任何地方，统一导航到岗位页；
                # 若还没登录，交给 log_in 等待人工处理
                finding_jobs.log_in(target_url=JOB_URL, auto_wait=LOGIN_WAIT)
            except Exception as e:
                log(f"接入手动浏览器失败（{e}），改为自行启动")
        else:
            log(f"未在端口 {ATTACH_PORT} 找到手动启动的浏览器")
            log("  → 请先双击 start_browser.bat 打开浏览器并登录，然后再运行本脚本")
            log("  → 或把 .env 里的 ATTACH_MODE 改成 0，由脚本自己启动浏览器")

    if not attached:
        # 先开首页：未登录时岗位页会被踢到 403 校验页，而首页右上角一定有登录入口
        finding_jobs.open_browser_with_options(HOME_URL, BROWSER,
                                               debug_port=ATTACH_PORT if ATTACH_MODE else None)
        _keep_only_current(finding_jobs.get_driver())
        finding_jobs.log_in(target_url=JOB_URL, auto_wait=LOGIN_WAIT)

    driver = finding_jobs.get_driver()
    if driver is None:
        log("浏览器未初始化，退出")
        return

    if EXPECT_LABEL:
        finding_jobs.select_dropdown_option(driver, EXPECT_LABEL)

    applied, scanned, index = 0, 0, 1

    while applied < MAX_APPLY and scanned < MAX_SCAN:
        scanned += 1
        try:
            title, desc = finding_jobs.get_job_description_by_index(index)
            if title is None and desc is None:
                log("列表已到底，结束")
                break

            if not desc:
                index += 1
                continue

            btn = finding_jobs.get_contact_button(driver)
            btn_text = (btn.text or "").strip() if btn else ""

            if btn is None:
                log(f"[{index}] {title or '(无标题)'} —— 没找到沟通按钮，跳过")
                index += 1
                continue

            if "立即沟通" not in btn_text:
                log(f"[{index}] {title or '(无标题)'} —— 按钮为「{btn_text}」，已沟通过/不可投，跳过")
                index += 1
                continue

            log(f"[{index}] {title or '(无标题)'} —— 生成打招呼语中…")
            try:
                letter = generate_letter(client, resume_text, desc, LETTER_LIMIT)
            except Exception as e:
                log(f"  ✗ 生成失败：{e}")
                index += 1
                continue

            log(f"  → {letter}")

            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"title": title, "letter": letter, "desc": desc[:200]},
                    ensure_ascii=False) + "\n")

            if DRY_RUN == 1:
                log("  （试跑模式，未打开聊天框也未发送）")
                applied += 1
                index += 1
                continue

            try:
                driver.execute_script("arguments[0].click();", btn)
            except WebDriverException:
                try:
                    btn.click()
                except WebDriverException:
                    log("  ✗ 点击沟通按钮失败，跳过")
                    index += 1
                    continue

            time.sleep(3)
            if send_response_and_go_back(driver, letter):
                applied += 1

            index += 1
            time.sleep(random.uniform(5, 10))   # 随机间隔，别太机械

        except KeyboardInterrupt:
            log("用户中断，结束")
            break
        except Exception as e:
            log(f"处理第 {index} 个岗位时出错：{e}")
            index += 1
            time.sleep(3)

    log(f"完成：本轮共投递 {applied} 个岗位，日志见 {LOG_PATH}")


def _keep_only_current(driver):
    """
    关掉多余标签页。Chrome 首次启动时常会带一个「新标签页」（显示 Google 首页），
    它会盖住我们打开的 BOSS 页面，用户就会看到"回到了 Google"。
    """
    try:
        cur = driver.current_window_handle
        for h in list(driver.window_handles):
            if h != cur:
                driver.switch_to.window(h)
                driver.close()
        driver.switch_to.window(cur)
    except WebDriverException:
        pass


def _wait_continue(timeout, hint=""):
    """挂起等待 logs/CONTINUE 放行信号。"""
    if hint:
        log(hint)
    go = os.path.join(LOG_DIR, "CONTINUE")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(go):
            try:
                os.remove(go)
            except OSError:
                pass
            return True
        time.sleep(2)
    return False


def _close_browser():
    """用了持久化 profile 就必须正常退出，否则下次运行会因 profile 被占用而失败。"""
    # 脚本一退出，它启动的浏览器会被系统一起回收。需要人工介入时先挂起等待。
    if HOLD_BROWSER:
        _wait_continue(900, "浏览器保持打开等待确认；放 logs/CONTINUE 文件让脚本收尾")
    # 接入的是用户手动开的浏览器，脚本没资格替他关掉
    if finding_jobs.attached_manually:
        log("脚本结束（手动启动的浏览器保持打开，登录态已保留）")
        return
    if (os.getenv("PERSIST_PROFILE") or "1") != "1":
        return
    d = finding_jobs.get_driver()
    if d is None:
        return
    try:
        d.quit()
        log("浏览器已关闭（登录态已保存，下次无需再扫码）")
    except WebDriverException:
        pass


if __name__ == "__main__":
    try:
        run()
    except Exception:
        # 不记下来的话，脚本会静默走到收尾流程，完全看不出哪里炸了
        log("运行异常：\n" + traceback.format_exc())
    finally:
        _close_browser()
