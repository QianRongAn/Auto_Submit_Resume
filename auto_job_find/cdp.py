"""
用 CDP（Chrome DevTools Protocol）直接接管一个已经打开的浏览器。

为什么不用 Selenium：
1. 用户平时用的是 Edge（154），国内网络拿不到对应版本的 msedgedriver；
2. chromedriver 版本与 Edge 不匹配，attach 会失败；
3. CDP 不需要任何 driver —— 浏览器只要带 --remote-debugging-port 启动就能接管。

额外好处：浏览器是用户手动启动的，没有 webdriver 指纹，不会触发 BOSS 的风控。
"""

import json
import time
import urllib.request

import websocket


class CDPBrowser:
    def __init__(self, port=9222):
        self.port = port
        self.ws = None
        self._id = 0
        self.target = None

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    @staticmethod
    def alive(port=9222, timeout=3):
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/json/version",
                                         headers={"Host": "localhost"})
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            return False

    def _http(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"Host": "localhost"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)

    def attach(self, url_match="zhipin.com", index=0):
        """接入一个标签页。url_match 为空时取第 index 个。"""
        tabs = [t for t in self._http("/json/list") if t.get("type") == "page"]
        if not tabs:
            raise RuntimeError("浏览器里没有任何标签页")

        tab = None
        if url_match:
            for t in tabs:
                if url_match in (t.get("url") or ""):
                    tab = t
                    break
        if tab is None:
            tab = tabs[min(index, len(tabs) - 1)]

        self.target = tab
        # webSocketDebuggerUrl 里的 host 可能是 localhost，在部分机器上会解析到 IPv6 ::1，
        # 而浏览器只监听 IPv4 127.0.0.1 → ws 握手直接 10061。统一换成 127.0.0.1。
        ws_url = tab["webSocketDebuggerUrl"].replace("://localhost", "://127.0.0.1")
        # 关键：websocket 库在 Windows 上会读「注册表里的系统代理」，把 ws 请求发到代理端口
        # 导致 10061。清环境变量没用，必须显式声明这两个地址不走代理。
        self.ws = websocket.create_connection(
            ws_url, timeout=30,
            http_no_proxy=["127.0.0.1", "localhost"])
        self.send("Page.enable")
        self.send("Runtime.enable")
        return tab

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None

    # ------------------------------------------------------------------
    # 基础命令
    # ------------------------------------------------------------------
    def send(self, method, params=None):
        if self.ws is None:
            raise RuntimeError("还没接入浏览器")
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method} 失败：{msg['error']}")
                return msg.get("result", {})
            # id 不匹配的都是事件通知，忽略

    def evaluate(self, js, timeout=30):
        """执行 JS 并返回可序列化的值。"""
        old = self.ws.gettimeout()
        self.ws.settimeout(timeout)
        try:
            r = self.send("Runtime.evaluate", {
                "expression": js,
                "returnByValue": True,
                "awaitPromise": True,
            })
            return r.get("result", {}).get("value")
        finally:
            self.ws.settimeout(old)

    def navigate(self, url):
        self.send("Page.navigate", {"url": url})

    def current_url(self):
        return self.evaluate("location.href")

    def title(self):
        return self.evaluate("document.title")

    def wait_url_change(self, contains=None, timeout=30):
        """等待 URL 变化 / 包含某个片段。"""
        start = self.current_url()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                u = self.current_url() or ""
            except Exception:
                u = ""
            if contains:
                if contains in u:
                    return u
            elif u != start:
                return u
            time.sleep(1)
        return self.current_url()

    # ------------------------------------------------------------------
    # 页面操作（全部用 JS 完成，避免被遮挡导致点击失败）
    # ------------------------------------------------------------------
    def click(self, xpath, timeout=15):
        """按 XPath 找元素并用原生 click() 点击，返回是否成功。"""
        js = f"""
        (() => {{
            const xp = {json.dumps(xpath)};
            const el = document.evaluate(xp, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (!el) return false;
            el.scrollIntoView({{block:'center'}});
            el.click();
            return true;
        }})()
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.evaluate(js):
                return True
            time.sleep(0.8)
        return False

    def click_one(self, xpaths, timeout=15):
        """依次尝试多个 XPath，任一命中即点击。"""
        for xp in xpaths:
            if self.click(xp, timeout=max(2, timeout // max(1, len(xpaths)))):
                return xp
        return None

    def text(self, xpath):
        js = f"""
        (() => {{
            const el = document.evaluate({json.dumps(xpath)}, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            return el ? (el.innerText || el.textContent || '').trim() : null;
        }})()
        """
        return self.evaluate(js)

    def exists(self, xpath):
        js = f"""
        !!document.evaluate({json.dumps(xpath)}, document, null,
            XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue
        """
        return bool(self.evaluate(js))

    def count(self, xpath):
        js = f"""
        document.evaluate({json.dumps(xpath)}, document, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null).snapshotLength
        """
        return self.evaluate(js) or 0

    def insert_text(self, text):
        """
        往当前焦点元素插入文字（支持中文）。
        Input.insertText 是 CDP 专门为输入法提供的命令，能正确触发
        框架的 input 事件，比 send_keys 更稳。
        """
        self.send("Input.insertText", {"text": text})

    def focus(self, xpath, timeout=15):
        js = f"""
        (() => {{
            const el = document.evaluate({json.dumps(xpath)}, document, null,
                XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
            if (!el) return false;
            el.scrollIntoView({{block:'center'}});
            el.focus();
            return true;
        }})()
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.evaluate(js):
                return True
            time.sleep(0.8)
        return False
