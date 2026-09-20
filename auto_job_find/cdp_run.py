"""
用 CDP 接管 Edge 跑 Boss 直聘投递流程。

必须「启动浏览器」和「连接浏览器」在同一个进程里完成 —— 沙箱会把不同命令的
网络命名空间隔开，跨命令访问 127.0.0.1 端口会连不上（已实测）。

流程：
  1. 启动带调试端口的 Edge（独立 profile，登录态会持久保存）
  2. 打开首页，等你在浏览器里登录
  3. 登录成功后跳到岗位列表页，抓结构、抓岗位
  4. 按 DRY_RUN 档位决定是否真发
"""

import io
import os
import subprocess
import sys
import time
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import cdp
import resume_ai
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))

PORT = int(os.getenv("ATTACH_PORT") or "9222")
PROFILE = os.path.join(BASE, "edge_profile")
JOB_URL = os.getenv("JOB_URL") or "https://www.zhipin.com/web/geek/jobs?city=100010000"
HOME_URL = "https://www.zhipin.com/"
MAX_APPLY = int(os.getenv("MAX_APPLY") or "2")
LETTER_LIMIT = int(os.getenv("LETTER_LIMIT") or "300")
DRY_RUN = int(os.getenv("DRY_RUN") or "2")
LOGIN_WAIT = int(os.getenv("LOGIN_WAIT") or "600")

LOG_DIR = os.path.join(BASE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_PATH = os.path.join(LOG_DIR, f"cdp_{datetime.now():%Y%m%d_%H%M%S}.log")
CONTINUE_FILE = os.path.join(LOG_DIR, "CONTINUE")


def log(msg):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def find_edge():
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              os.path.join(os.environ.get("LOCALAPPDATA", ""),
                           r"Microsoft\Edge\Application\msedge.exe")):
        if p and os.path.exists(p):
            return p
    return None


def start_browser():
    edge = find_edge()
    if not edge:
        log("没找到 Edge")
        return False
    os.makedirs(PROFILE, exist_ok=True)
    log(f"启动 Edge：{edge}")
    subprocess.Popen([
        edge,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        HOME_URL,
    ])
    for _ in range(40):
        time.sleep(1)
        if cdp.CDPBrowser.alive(PORT):
            log(f"调试端口 {PORT} 已就绪")
            return True
    log("调试端口一直没起来")
    return False


JOB_CARD_XPATHS = [
    "//li[contains(@class,'job-card-box')]",
    "//li[contains(@class,'job-card-wrapper')]",
    "//ul[contains(@class,'job-list-box')]/li",
    "//div[contains(@class,'job-list-box')]//li",
]
TITLE_XPATHS = [
    ".//span[contains(@class,'job-name')]",
    ".//a[contains(@class,'job-title')]",
    ".//div[contains(@class,'job-title')]",
]
CHAT_BTN_XPATHS = [
    "//a[contains(@class,'op-btn-chat')]",
    "//button[contains(@class,'op-btn-chat')]",
    "//*[contains(@class,'btn-chat')]",
    "//a[normalize-space(text())='立即沟通']",
]
CHAT_INPUT_XPATHS = [
    "//*[@id='chat-input']",
    "//div[contains(@class,'input-box')]//div[@contenteditable='true']",
    "//div[@contenteditable='true']",
]


def looks_logged_in(b):
    """已登录的强信号：出现头像 / 消息入口，且没有「登录」按钮。"""
    try:
        if b.exists("//a[normalize-space(text())='登录']"):
            return False
        for xp in ("//*[contains(@class,'nav-figure')]",
                   "//a[contains(@href,'/web/geek/chat')]",
                   "//a[contains(@href,'/web/chat/index')]"):
            if b.exists(xp):
                return True
    except Exception:
        pass
    return False


def wait_login(b, timeout=LOGIN_WAIT):
    log("=" * 46)
    log("请在弹出的 Edge 窗口里登录 BOSS 直聘（扫码或账密）")
    log(f"最长等待 {timeout} 秒，登录成功会自动继续")
    log("=" * 46)
    start = time.time()
    last_hint = 0
    while time.time() - start < timeout:
        if looks_logged_in(b):
            log("检测到已登录")
            return True
        if os.path.exists(CONTINUE_FILE):
            log("收到人工放行信号")
            try:
                os.remove(CONTINUE_FILE)
            except OSError:
                pass
            return True
        waited = int(time.time() - start)
        if waited - last_hint >= 30:
            last_hint = waited
            log(f"  …等待登录中（{waited} 秒）")
        time.sleep(2)
    log("等待登录超时")
    return False


def wait_signal(timeout, hint=""):
    """挂起等待 CONTINUE 信号，期间浏览器保持存活。"""
    if hint:
        log(hint)
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(CONTINUE_FILE):
            try:
                os.remove(CONTINUE_FILE)
            except OSError:
                pass
            return True
        time.sleep(2)
    return False


def dump_structure(b):
    log("--- 页面结构探测 ---")
    for xp in JOB_CARD_XPATHS:
        try:
            log(f"  岗位卡片 {b.count(xp)} 个  <- {xp}")
        except Exception as e:
            log(f"  岗位卡片探测失败 {e}")
    try:
        log(f"  沟通按钮 {b.count(CHAT_BTN_XPATHS[0])} 个")
    except Exception:
        pass


def main():
    if not start_browser():
        return 1

    b = cdp.CDPBrowser(PORT)
    try:
        b.attach(url_match="zhipin.com")
    except Exception as e:
        log(f"接入失败：{e}")
        return 1

    log(f"当前页面：{b.current_url()}")
    if "zhipin.com" not in (b.current_url() or ""):
        b.navigate(HOME_URL)
        time.sleep(5)

    if not wait_login(b):
        log("没检测到登录。浏览器保持打开，你可以手动登录后放 CONTINUE 文件继续。")
        if not wait_signal(600):
            return 1

    log(f"打开岗位页：{JOB_URL}")
    b.navigate(JOB_URL)
    time.sleep(8)
    log(f"当前页面：{b.current_url()}")
    dump_structure(b)

    try:
        body = b.evaluate("(document.body?document.body.innerText:'').slice(0,600)")
        log("--- 页面正文前 600 字 ---")
        log(body or "(空)")
    except Exception as e:
        log(f"取正文失败 {e}")

    log("本轮只做结构探测，不投递。确认选择器无误后再放开投递。")
    log("脚本会挂起等待，浏览器保持打开（脚本一退出浏览器就会被系统回收）。")
    wait_signal(900, hint="要结束请直接关闭；要继续投递请放 CONTINUE 文件")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("已中断")
