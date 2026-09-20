"""
把你日常 Edge 的登录态复制到本项目的独立 profile 目录。

为什么需要它：Chromium 在「默认用户数据目录」下会强制忽略 --remote-debugging-port，
而 Selenium 必须靠这个端口工作，所以没法直接自动化默认 profile。
解决办法是把登录相关的小文件复制到新目录 —— 关键是 Local State，
里面存着解密 Cookie 用的密钥（绑定 Windows 用户，不绑定目录路径），
所以换目录后 Edge 照样能解开这些 Cookie，BOSS 的登录态也就跟着过来了。
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")
DST = os.path.join(BASE, "edge_profile")

# 只需要这几个文件，总共几 MB；IndexedDB(30MB) 与登录无关，跳过
ITEMS = [
    ("Local State", "Local State"),
    ("Default/Network/Cookies", "Default/Network/Cookies"),
    ("Default/Preferences", "Default/Preferences"),
    ("Default/Local Storage/leveldb", "Default/Local Storage/leveldb"),
    ("Default/Session Storage", "Default/Session Storage"),
]


def main():
    if not os.path.isdir(SRC):
        print("找不到 Edge 用户数据目录:", SRC)
        return 1

    total = 0
    copied = 0
    for rel_src, rel_dst in ITEMS:
        s = os.path.join(SRC, rel_src)
        d = os.path.join(DST, rel_dst)
        if not os.path.exists(s):
            print("  跳过（不存在）:", rel_src)
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.isdir(s):
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        sz = os.path.getsize(d) if os.path.isfile(d) else sum(
            os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)
        total += sz
        copied += 1
        print(f"  已复制 {rel_src}  ({sz/1024:.0f} KB)")

    print(f"\n共 {copied} 项，{total/1024:.0f} KB -> {DST}")
    print("下次用 BROWSER=edge + EDGE_USE_DEFAULT_PROFILE=0 启动即可带上登录态。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
