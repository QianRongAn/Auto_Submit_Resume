"""
Boss 直聘（zhipin.com）自动浏览 / 登录 / 打招呼 的 RPA 部分。

相比原版的改动：
1. 删掉了与 RPA 无关的 langchain / HuggingFace 导入（原版在模块顶层就实例化了
   embeddings，导入即触发几百 MB 模型下载，且这里根本用不到）。
2. chromedriver 路径改为相对本文件解析，不再写死绝对路径。
3. 原版大量使用 2023 年的绝对 XPATH，页面一改版就全崩。这里改成
   「多套候选选择器 + 逐个尝试」，任一命中即可，抗改版能力强很多。
4. 登录改为自动检测 + 手动兜底，扫码时间不再受 60 秒硬超时限制。
"""

import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
    ElementClickInterceptedException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

driver = None  # 全局 WebDriver 实例
attached_manually = False  # True = 接入的是用户手动开的浏览器，脚本不要关它


# --------------------------------------------------------------------------
# 通用小工具
# --------------------------------------------------------------------------
def _first(driver, selectors, by=By.XPATH, timeout=8, clickable=False):
    """按顺序尝试多个选择器，返回第一个命中的元素；全部失败返回 None。"""
    for sel in selectors:
        try:
            cond = EC.element_to_be_clickable((by, sel)) if clickable \
                else EC.presence_of_element_located((by, sel))
            return WebDriverWait(driver, timeout).until(cond)
        except (TimeoutException, NoSuchElementException):
            continue
    return None


def _first_text(driver, selectors, by=By.XPATH, timeout=8):
    el = _first(driver, selectors, by=by, timeout=timeout)
    return el.text.strip() if el else None


def get_driver():
    global driver
    return driver


def disable_local_proxy():
    """
    把系统代理清掉，否则 Selenium 连 127.0.0.1 上的 chromedriver 也会被代理拦截，
    表现为所有命令都报 "unhandled request"（开着 Clash / VPN 时尤其常见）。
    """
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"


def _driver_path():
    """定位 chromedriver：优先仓库内 drivers 目录，其次系统 PATH。"""
    local = os.path.join(BASE_DIR, "drivers", "chromedriver.exe")
    if os.path.exists(local):
        return local
    return None  # 交给 Selenium Manager 自动处理


def _edge_driver_path():
    """
    定位 msedgedriver。国内网络基本下不到（微软的两个源一个连不上、一个 409），
    所以优先用手动放进 drivers/ 的那个。
    """
    for name in ("msedgedriver.exe", "edgedriver.exe"):
        p = os.path.join(BASE_DIR, "drivers", name)
        if os.path.exists(p):
            return p
    return None  # 交给 Selenium Manager 自动处理


def _profile_dir():
    """开启后登录态会保存在本地，第二次运行就不用再扫码了。"""
    if (os.getenv("PERSIST_PROFILE") or "1") == "1":
        d = os.path.join(BASE_DIR, "chrome_profile")
        os.makedirs(d, exist_ok=True)
        return d
    return None


# 固定调试端口：脚本自己启动的浏览器也带上它，之后每次运行都先试着接入，
# 从而永远复用同一个窗口，不再反复开关浏览器。
REUSE_PORT = int(os.getenv("REUSE_PORT") or "9333")


def attach_to_existing(port=9222, browser="chrome"):
    """
    接入一个已经开着的浏览器（用 --remote-debugging-port 启动的那个）。

    复用已有窗口有两个好处：一是不会再反复弹新窗口打扰人，二是窗口里保留着
    人工登录 / 过验证的状态，风控更难触发。
    """
    global driver, attached_manually
    disable_local_proxy()
    if browser == "edge":
        opts = EdgeOptions()
        opts.debugger_address = f"127.0.0.1:{port}"
        driver = webdriver.Edge(options=opts)
    else:
        options = Options()
        options.debugger_address = f"127.0.0.1:{port}"
        driver = webdriver.Chrome(options=options)
    attached_manually = True
    print(f"[浏览器] 已接入已打开的窗口（调试端口 {port}）")
    return driver


def browser_alive(port=9222, timeout=2):
    """调试端口上有没有活着的浏览器。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout):
            return True
    except Exception:
        return False


def _same_page(cur, url):
    """当前页和目标页是不是同一页（忽略 BOSS 自己追加的 _security_check 等参数）。"""
    try:
        from urllib.parse import urlparse, parse_qs
        a, b = urlparse(cur or ""), urlparse(url or "")
        if a.netloc != b.netloc or a.path.rstrip("/") != b.path.rstrip("/"):
            return False
        qa = {k for k in parse_qs(a.query) if not k.startswith("_")}
        qb = {k for k in parse_qs(b.query) if not k.startswith("_")}
        return qa == qb
    except Exception:
        return False


def open_browser_with_options(url, browser="chrome", debug_port=None, reuse=True):
    global driver
    disable_local_proxy()

    # 已经有窗口在跑就直接接管，不再新开一个（避免窗口反复弹出、也避免重复刷新）
    if reuse and browser_alive(REUSE_PORT):
        try:
            d = attach_to_existing(REUSE_PORT, browser=browser)
            if _same_page(d.current_url, url):
                print(f"[浏览器] 已在 {url}，不重复加载")
                _hide_automation(d)
                return d
            d.get(url)
            print(f"[浏览器] 已切换到 {url}")
            _hide_automation(d)
            return d
        except Exception as e:
            print(f"[浏览器] 复用已有窗口失败（{str(e)[:80]}），改为重新启动")
            driver = None

    if browser == "chrome":
        options = Options()
        options.add_experimental_option("detach", True)
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        # 降低被识别为自动化的概率
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument("--disable-blink-features=AutomationControlled")
        # 强制直连：绕过系统代理。开着 Clash/VPN 时代理往往会把请求 302 到验证页，
        # 或者代理客户端已关闭导致整个网页打不开
        options.add_argument("--no-proxy-server")
        prof = _profile_dir()
        if prof:
            options.add_argument(f"--user-data-dir={prof}")
            print(f"[浏览器] 复用登录态目录：{prof}")
        if debug_port:
            # 留个调试端口，方便随时查看浏览器状态 / 人工接管
            options.add_argument(f"--remote-debugging-port={debug_port}")

        path = _driver_path()
        driver = webdriver.Chrome(service=Service(path) if path else None, options=options)
    elif browser == "edge":
        path = _edge_driver_path()

        def _edge_opts(user_data_dir, with_port=True):
            o = EdgeOptions()
            # 注意：Edge 上不要加 experimental "detach"，它会让 DevToolsActivePort 生成失败
            o.add_experimental_option("excludeSwitches", ["enable-automation"])
            o.add_argument("--disable-blink-features=AutomationControlled")
            o.add_argument("--no-proxy-server")
            # 下面这几个是 DevToolsActivePort 能否生成的关键
            o.add_argument("--no-sandbox")
            o.add_argument("--disable-dev-shm-usage")
            o.add_argument("--disable-gpu")
            o.add_argument("--no-first-run")
            o.add_argument("--no-default-browser-check")
            # 固定端口（不能写 0，随机端口会导致下次运行无法接入、只能新开窗口）。
            # 默认 profile（日常 Edge 的目录）下绝不加端口 —— Chromium 会干扰端口生成，
            # 之前反复报 DevToolsActivePort 就是它；半自动模式也不需要接入复用
            if with_port:
                o.add_argument(f"--remote-debugging-port={REUSE_PORT}")
            o.add_argument("--window-size=1600,950")
            o.add_argument(f"--user-data-dir={user_data_dir}")
            o.add_argument("--profile-directory=Default")
            return o

        if (os.getenv("EDGE_USE_DEFAULT_PROFILE") or "0") == "1":
            # 直接用你日常 Edge 的登录态（不是无头，会弹出一个真实新窗口）。
            # 前提：日常 Edge 的窗口要先关掉，否则目录被占用会启动失败
            ud = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                              "Microsoft", "Edge", "User Data")
            print(f"[浏览器] 使用日常 Edge 登录态：{ud}")
            try:
                driver = webdriver.Edge(service=EdgeService(path) if path else None,
                                        options=_edge_opts(ud, with_port=False))
            except WebDriverException as e:
                # 默认目录被占用（日常 Edge 还开着 / Startup Boost 常驻）→ 回退复制版
                print(f"[浏览器] 日常目录启动失败（{str(e)[:80]}）")
                print("[浏览器] 自动回退到 edge_profile/ 复制版登录态")
                print("[浏览器] 想用日常目录请先退出所有 Edge 窗口（含托盘后台）再重跑")
                prof = os.path.join(BASE_DIR, "edge_profile")
                os.makedirs(prof, exist_ok=True)
                driver = webdriver.Edge(service=EdgeService(path) if path else None,
                                        options=_edge_opts(prof, with_port=False))
        else:
            prof = os.path.join(BASE_DIR, "edge_profile")
            os.makedirs(prof, exist_ok=True)
            print(f"[浏览器] Edge 独立登录态目录：{prof}")
            driver = webdriver.Edge(service=EdgeService(path) if path else None,
                                    options=_edge_opts(prof))
    else:
        raise ValueError(f"不支持的浏览器类型：{browser}")

    # maximize_window() 在部分 Chrome/driver 组合下会抛 unhandled request，失败不能影响主流程
    try:
        driver.maximize_window()
    except WebDriverException:
        try:
            driver.set_window_size(1600, 950)
        except WebDriverException:
            pass

    _hide_automation(driver)

    driver.get(url)

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    # Edge 首次启动（尤其新 profile）会把当前标签顶成 edge://newtab，
    # 导致 driver 停在新标签页上、目标页根本没打开。这里强制纠偏。
    _ensure_on_target(driver, url)
    print(f"[浏览器] 已打开 {url}")
    return driver


def _ensure_on_target(driver, url):
    """确保当前焦点在目标站点上，而不是 NTP / about:blank。"""
    host = ""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.split(":")[0]
    except Exception:
        pass

    for attempt in range(3):
        try:
            cur = driver.current_url or ""
        except WebDriverException:
            cur = ""
        if host and host in cur:
            return True
        # 有时目标页在别的标签里，先切过去看看
        try:
            handles = driver.window_handles
            for h in handles:
                driver.switch_to.window(h)
                if host and host in (driver.current_url or ""):
                    return True
            if handles:
                driver.switch_to.window(handles[0])
        except WebDriverException:
            pass
        try:
            driver.get(url)
            time.sleep(3)
        except WebDriverException:
            pass
    try:
        print(f"[浏览器] 注意：当前地址是 {driver.current_url[:80]}")
    except WebDriverException:
        pass
    return False


def _hide_automation(driver):
    """
    抹掉最常被风控抓的自动化指纹。BOSS 直聘的风控会弹「安全验证」，
    这里能把命中率降下来（不能保证 100%，真弹了就只能人工点一下）。
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                window.chrome = window.chrome || {runtime: {}};
                const origQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (p) =>
                    p.name === 'notifications'
                        ? Promise.resolve({state: Notification.permission})
                        : origQuery(p);
            """
        })
    except Exception:
        pass


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
LOGIN_OK_SELECTORS = [
    "//*[@id='header']/div[1]/div[3]/ul/li[2]/a",   # 登录后头部出现的导航
    "//a[contains(@href,'/web/geek/chat')]",        # 「消息」入口
    "//a[contains(@href,'/web/chat/index')]",
    "//*[contains(@class,'nav-figure')]",           # 头像
    "//*[@id='header']//img[contains(@class,'figure')]",
]

# 出现这些说明「还没登录」——用来挡住误判
LOGGED_OUT_SELECTORS = [
    "//a[normalize-space(text())='登录']",
    "//a[normalize-space(text())='注册']",
    "//*[@id='header']/div[1]/div[3]/div/a",
    "//*[contains(@class,'btn-login')]",
]

LOGIN_BTN_SELECTORS = [
    "//*[@id='header']/div[1]/div[3]/div/a",
    "//div[contains(@class,'btns')]//a[contains(text(),'登录')]",
    "//a[contains(text(),'登录')]",
]


NEED_LOGIN_MARKERS = ("passport", "403.html", "verify.html", "/login", "zp/403")

# 出现安全验证时，脚本自己过不去，必须人工点一下
VERIFY_MARKERS = ("verify.html", "zp/verify")


def needs_login(driver):
    """未登录时 Boss 直聘会把 /web/geek/* 重定向到 passport / 403 页。"""
    u = (driver.current_url or "").lower()
    return any(m in u for m in NEED_LOGIN_MARKERS)


def is_on_verify_page(driver):
    u = (driver.current_url or "").lower()
    return any(m in u for m in VERIFY_MARKERS)


def is_logged_in(driver, timeout=3):
    """
    判定是否已登录。

    踩过的坑：首页 / 公开页上有「消息」等通用导航，按元素存在性判断会误判成已登录，
    结果脚本不等扫码就往下跑，一个岗位都抓不到。
    所以这里只认「停在需要登录才能进的页面上且没被踢走」这个强信号。
    """
    u = (driver.current_url or "")
    if needs_login(driver):
        return False
    # /web/geek/*、/web/chat/* 这类页面未登录会被立刻踢到 passport，能停住就是登录了
    return ("/web/geek/" in u) or ("/web/chat/" in u)


def _click_login_entry(driver):
    btn = _first(driver, LOGIN_BTN_SELECTORS, clickable=True, timeout=10)
    if not btn:
        return False
    try:
        btn.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", btn)
    return True


def log_in(target_url=None, auto_wait=300):
    """
    等待用户完成登录（扫码 / 账密）。
    - 若被重定向到 passport/403：直接提示扫码，登录后自动跳回 target_url
    - 否则弹登录框，等登录态出现
    两种方式都留了手动回车兜底，不会超时崩掉。
    """
    global driver

    if is_logged_in(driver):
        print("[登录] 已处于登录状态")
        return True

    # 403 校验页上没有任何登录入口，统一先回到首页（首页右上角一定有「登录」）
    if _click_login_entry(driver):
        print("[登录] 已弹出登录框，请用 Boss 直聘 App 扫码（或账密登录）")
    else:
        print("[登录] 没找到登录按钮，请在浏览器右上角手动点「登录」并扫码")

    # 人工放行通道：自动检测有风险，用户在浏览器里搞定后，
    # 只要在 logs/ 下放一个 CONTINUE 文件，脚本就立刻继续
    go_file = os.path.join(BASE_DIR, "logs", "CONTINUE")

    deadline = time.time() + auto_wait
    start = time.time()
    last_hint = 0
    verify_hinted = False
    while time.time() < deadline:
        if is_logged_in(driver):
            print("[登录] 检测到登录成功")
            break

        if os.path.exists(go_file):
            try:
                os.remove(go_file)
            except OSError:
                pass
            print("[登录] 收到人工放行信号，继续执行")
            break

        if is_on_verify_page(driver) and not verify_hinted:
            verify_hinted = True
            print("[登录] ⚠ 出现 BOSS 直聘「安全验证」，脚本自己过不去")
            print("        >>> 请在浏览器里点「点击按钮进行验证」完成验证，然后登录 <<<")
            print("        >>> 搞定后告诉助手放行 <<<")

        waited = int(time.time() - start)
        if waited - last_hint >= 30:
            last_hint = waited
            print(f"     …等待中（已等 {waited} 秒）"
                  + ("，当前在安全验证页" if is_on_verify_page(driver) else ""))
        time.sleep(2)
    else:
        print(f"[登录] {auto_wait} 秒内未自动检测到登录状态。")
        try:
            input("        若其实已登录成功，直接按【回车】继续；否则扫码后再回车 >>> ")
        except EOFError:
            pass

    # 登录过程中可能被跳转到了首页，回到目标岗位页
    if target_url and target_url not in (driver.current_url or ""):
        print(f"[登录] 回到岗位列表页：{target_url}")
        driver.get(target_url)
        time.sleep(4)
    return True


# --------------------------------------------------------------------------
# 岗位列表 / 详情
# --------------------------------------------------------------------------
JOB_ITEM_SELECTORS_TPL = [
    "//li[contains(@class,'job-card-box')][{i}]",
    "//li[contains(@class,'job-card-wrapper')][{i}]",
    "//div[contains(@class,'job-list-box')]//li[{i}]",
    "//ul[contains(@class,'job-list')]/li[{i}]",
    "//*[@id='wrap']/div[2]/div[2]/div/div/div[1]/ul/li[{i}]",
]

# ---- 新版「/web/geek/jobs」页面（左列表 + 右详情同屏）----
# 页面会改版，所以不写死一个选择器：按优先级逐个探测，命中即用
CARD_CANDIDATES = [
    "//li[contains(@class,'job-card')]",
    "//li[contains(@class,'card-item')]",
    "//li[@ka='search_list_job_main']",
    "//li[@ka='recommend_list_job_main']",
    "//div[contains(@class,'job-card') and .//a]",
    "//a[contains(@href,'/job_detail/')]",
]

DETAIL_PANEL_SELECTORS = [
    "//div[contains(@class,'job-detail')]",
    "//div[contains(@class,'detail-content')]",
    "//div[contains(@class,'job-detail-section')]",
    "//div[contains(@class,'job-sec-text')]",
    "//div[contains(@class,'detail-panel')]",
]


def detect_cards(driver, min_count=2, timeout=8):
    """
    自适应找出当前页面的岗位卡片。返回 (xpath, elements)；找不到返回 (None, [])。
    每次调用都重新探测 —— 页面翻页后旧元素会失效，不能缓存。
    """
    end = time.time() + timeout
    while time.time() < end:
        for xp in CARD_CANDIDATES:
            try:
                els = [e for e in driver.find_elements(By.XPATH, xp)
                       if (e.text or "").strip()]
            except WebDriverException:
                els = []
            if len(els) >= min_count:
                return xp, els
        time.sleep(1)
    return None, []


def read_detail_panel(driver, timeout=8):
    """读取右侧详情面板文本（新版同屏布局）。取命中容器里文本最长的那个。"""
    end = time.time() + timeout
    best = ""
    while time.time() < end:
        for xp in DETAIL_PANEL_SELECTORS:
            try:
                for el in driver.find_elements(By.XPATH, xp):
                    try:
                        t = (el.text or "").strip()
                    except WebDriverException:
                        continue
                    if len(t) > len(best):
                        best = t
            except WebDriverException:
                continue
        if len(best) > 80:
            return best
        time.sleep(1)
    return best


def safety_stop(driver):
    """登录异常 / 安全检测。正常返回 None；需要停下交还人工时返回原因。"""
    try:
        if is_on_verify_page(driver):
            return "弹出安全验证（请你在浏览器里手动完成验证，再回来重新运行本脚本）"
        url = driver.current_url or ""
        if "passport" in url or "security" in url:
            return "被踢到登录/安全页（登录可能失效，请手动重新登录后再运行）"
    except WebDriverException:
        pass
    return None

JOB_DESC_SELECTORS = [
    "//div[contains(@class,'job-sec-text')]",
    "//div[contains(@class,'job-detail-text')]",
    "//div[contains(@class,'detail-bottom-text')]",
    "//*[@id='wrap']/div[2]/div[2]/div/div/div[2]/div/div[2]/p",
]

CONTACT_BTN_SELECTORS = [
    "//a[contains(@class,'op-btn-chat')]",
    "//*[normalize-space(text())='立即沟通']",
    "//*[@id='wrap']/div[2]/div[2]/div/div/div[2]/div/div[1]/div[2]/a[2]",
]

CHAT_INPUT_SELECTORS = [
    "//*[@id='chat-input']",
    "//div[contains(@class,'chat-input')]//div[@contenteditable='true']",
    "//div[@contenteditable='true']",
]


def select_dropdown_option(driver, label):
    """切换「期望职位」筛选。找不到就跳过（不阻断主流程）。"""
    if not label:
        return False

    # 快捷标签（推荐页顶部的职位按钮）
    try:
        for el in driver.find_elements(By.XPATH, "//*[contains(@class,'recommend-job-btn')]"):
            if label in (el.text or ""):
                driver.execute_script("arguments[0].click();", el)
                print(f"[筛选] 点击快捷标签：{label}")
                time.sleep(3)
                return True
    except WebDriverException:
        pass

    # 下拉菜单
    trigger = _first(
        driver,
        [
            "//*[@id='wrap']/div[2]/div[1]/div/div[1]/div",
            "//div[contains(@class,'dropdown-select')]",
            "//div[contains(@class,'condition-item')]",
        ],
        clickable=True,
        timeout=8,
    )
    if not trigger:
        print(f"[筛选] 没找到筛选入口，跳过（将按当前列表顺序投递）")
        return False

    try:
        trigger.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", trigger)

    _first(driver, ["//ul[contains(@class,'dropdown-expect-list')]",
                    "//ul[contains(@class,'dropdown-list')]"], timeout=8)

    option = _first(driver, [f"//li[contains(text(), '{label}')]"], clickable=True, timeout=8)
    if option:
        option.click()
        print(f"[筛选] 已选择：{label}")
        time.sleep(3)
        return True

    print(f"[筛选] 下拉里没有「{label}」，跳过（将按当前列表顺序投递）")
    return False


def get_job_description_by_index(index):
    """点击列表中第 index 个岗位（从 1 开始），返回 (标题, 描述)。失败返回 (None, None)。"""
    global driver

    item = None
    for tpl in JOB_ITEM_SELECTORS_TPL:
        sel = tpl.format(i=index)
        try:
            item = WebDriverWait(driver, 6).until(
                EC.element_to_be_clickable((By.XPATH, sel))
            )
            if item:
                break
        except (TimeoutException, NoSuchElementException):
            continue

    if item is None:
        print(f"[列表] 第 {index} 个岗位不存在，已到列表末尾")
        return None, None

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", item)
        time.sleep(0.5)
        item.click()
    except (ElementClickInterceptedException, WebDriverException):
        try:
            ActionChains(driver).move_to_element(item).click().perform()
        except WebDriverException:
            return None, None

    time.sleep(2)

    title = _first_text(
        driver,
        [
            "//div[contains(@class,'job-detail')]//*[contains(@class,'job-name')]",
            "//*[contains(@class,'job-title')]",
            f"({JOB_ITEM_SELECTORS_TPL[0].format(i=index)})//*[contains(@class,'job-name')]",
        ],
        timeout=6,
    ) or ""

    desc = _first_text(driver, JOB_DESC_SELECTORS, timeout=10)
    if not desc:
        print(f"[列表] 第 {index} 个岗位没有取到描述，跳过")
        return title, None

    return title, desc


def get_contact_button(driver, timeout=6):
    return _first(driver, CONTACT_BTN_SELECTORS, clickable=True, timeout=timeout)


def wait_chat_input(driver, timeout=40):
    return _first(driver, CHAT_INPUT_SELECTORS, clickable=True, timeout=timeout)
