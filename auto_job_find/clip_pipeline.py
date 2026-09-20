"""
剪贴板管道 —— 你在 BOSS 上复制 JD，它自动把定制话术放回剪贴板。

为什么这么设计：
  全自动 RPA 要脚本驱动浏览器 → 触发风控、反复开关窗口，你已经受够了。
  但这个方案里脚本完全不碰浏览器 —— 它只盯剪贴板。零刷新、零验证、零封号风险。

用法：
  python clip_pipeline.py            # 监听模式（默认，一直挂着）
  python clip_pipeline.py --batch    # 批量处理 pipeline/inbox.md 里攒的 JD

监听模式的操作（熟练后每个岗位约 10 秒）：
  1. 浏览器里点开岗位 → Ctrl+A、Ctrl+C 复制 JD
  2. 脚本自动检测 → 3~5 秒生成定制话术
  3. 话术自动回到剪贴板，并弹提示 → 切回聊天框 Ctrl+V、回车发送

  你不需要复制话术、不需要回到这里粘贴给我，全程只有一个动作：复制 JD。
"""

import os
import re
import sys
import csv
import time
import json
import hashlib
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

import pyperclip

import resume_ai
import letter_variety
from resume_ai import read_resumes, build_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PIPE_DIR = os.path.join(os.path.dirname(BASE_DIR), "pipeline")
os.makedirs(PIPE_DIR, exist_ok=True)

OUTBOX = os.path.join(PIPE_DIR, "outbox.md")
DASHBOARD = os.path.join(PIPE_DIR, "dashboard.csv")
INBOX = os.path.join(PIPE_DIR, "inbox.md")

LETTER_LIMIT = int(os.getenv("LETTER_LIMIT") or "300")

# ---------------------------------------------------------------------------
# 场景配置：决定生成哪种话术
#   zh_job   BOSS 直聘打招呼（中文，压成单行）
#   en_cover LinkedIn Easy Apply 求职信（英文，150-220 词）
#   en_note  LinkedIn 加好友备注（英文，300 字符硬上限，超了发不出去）
#   en_phd   博士申请套磁邮件（英文，保留段落格式）
# ---------------------------------------------------------------------------
SCENES = {
    "zh_job":   dict(lang="zh", limit=300, keep_newlines=False, hard=None,
                     label="BOSS直聘打招呼（中文）"),
    "en_cover": dict(lang="en", limit=200, keep_newlines=False, hard=None,
                     label="LinkedIn 求职信（英文）"),
    "en_note":  dict(lang="en", limit=280, keep_newlines=False, hard=300,
                     label="LinkedIn 加好友备注（英文，300字符硬限）"),
    "en_phd":   dict(lang="en", limit=240, keep_newlines=True, hard=None,
                     label="博士套磁邮件（英文）"),
}


def parse_scene(argv):
    """--scene en_cover / 或 .env 的 LETTER_SCENE，默认 zh_job"""
    for i, a in enumerate(argv):
        if a == "--scene" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--scene="):
            return a.split("=", 1)[1]
        # 直接写场景名也认
        if a in SCENES:
            return a
    return (os.getenv("LETTER_SCENE") or "zh_job").strip()


# 当前场景（由 __main__ 用 parse_scene 覆盖）
CUR_SCENE = (os.getenv("LETTER_SCENE") or "zh_job").strip()
CUR_CONF = SCENES.get(CUR_SCENE, SCENES["zh_job"])


# 判断"这段剪贴板文本是不是岗位 JD"的特征词（中英文都覆盖，领英是英文 JD）
JD_HINTS = ("岗位职责", "任职要求", "职位描述", "工作内容", "岗位要求", "任职资格",
            "招聘", "职责：", "要求：", "我们希望你", "工作职责", "职位要求",
            "岗位描述", "我们希望", "加分项", "学历要求", "经验要求",
            # 英文（LinkedIn / 海外岗位描述）
            "responsibilities", "requirements", "qualifications", "about the role",
            "what you", "we are looking for", "job description", "you will",
            "preferred qualifications", "minimum qualifications", "who you are",
            "about you", "your role", "we offer", "the role")

# 状态：已处理过的文本指纹，避免同一段反复生成
SEEN_PATH = os.path.join(PIPE_DIR, ".seen.json")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def load_seen():
    if os.path.exists(SEEN_PATH):
        try:
            with open(SEEN_PATH, encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen)[-500:], f)   # 只留最近 500 条


def fp(text):
    return hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def looks_like_jd(text):
    t = (text or "").strip()
    if len(t) < 80 or len(t) > 8000:
        return False
    low = t.lower()
    return any(h in t or h.lower() in low for h in JD_HINTS)


def guess_title_company(jd):
    """从 JD 文本里猜职位名和公司名。"""
    lines = [l.strip() for l in jd.splitlines() if l.strip()]
    title = next((l for l in lines[:6] if 2 < len(l) < 30), "")
    company = next((l for l in lines[:12] if any(
        k in l for k in ("公司", "科技", "有限", "集团", "研究院", "实验室", "医院", "大学"))), "")
    return title, company


def load_history_letters():
    """
    从 outbox.md 回溯已生成的话术，用于防重复。
    每条话术位于 '## 时间 ｜ 岗位' 标题行之后、'---' 分隔线之前。
    """
    if not os.path.exists(OUTBOX):
        return []
    letters, buf, in_block = [], [], False
    with open(OUTBOX, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip()
            if s.startswith("## "):
                in_block, buf = True, []
                continue
            if s.strip() == "---":
                if buf:
                    letters.append(" ".join(" ".join(buf).split()))
                in_block, buf = False, []
                continue
            if in_block and s.strip():
                buf.append(s.strip())
    return [l for l in letters if len(l) > 40][-50:]


def make_letter(client, resume_text, jd, history, conf):
    letter, angle = letter_variety.generate_unique_letter(
        lambda r, j, lim, angle_hint=None, avoid_hint=None:
            resume_ai.generate_letter(client, r, j, lim,
                                      angle_hint=angle_hint, avoid_hint=avoid_hint,
                                      scene=CUR_SCENE),
        resume_text, jd, conf["limit"], history, log=None,
        lang=conf["lang"], keep_newlines=conf["keep_newlines"],
        hard_char_limit=conf["hard"])
    return letter, angle


def append_outbox(title, company, letter):
    new = not os.path.exists(OUTBOX)
    with open(OUTBOX, "a", encoding="utf-8") as f:
        if new:
            f.write("# 已生成的话术（最新在最后）\n\n")
        f.write(f"## {datetime.now():%m-%d %H:%M} ｜ {title or '未知岗位'}"
                f"{(' ｜ ' + company) if company else ''}"
                f" ｜ 场景 {CUR_SCENE}\n\n")
        f.write(letter + "\n\n")
        f.write("---\n\n")


def append_dashboard(title, company, letter):
    new = not os.path.exists(DASHBOARD)
    with open(DASHBOARD, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["时间", "场景", "岗位", "公司", "字数/词数", "话术", "状态"])
        w.writerow([f"{datetime.now():%Y-%m-%d %H:%M:%S}", CUR_SCENE, title, company,
                    len(letter), letter, "已生成待发送"])


# ---------------------------------------------------------------------------
# 监听模式
# ---------------------------------------------------------------------------
def watch():
    log(f"场景：{CUR_CONF['label']}")
    problems = resume_ai.check_config()
    if problems:
        log("自检未通过：")
        for p in problems:
            log("  - " + p)
        return
    resume_text = read_resumes(CUR_CONF["lang"])
    client = build_client()
    history = load_history_letters()
    seen = load_seen()

    log("=" * 60)
    log("剪贴板管道已启动。脚本不碰浏览器，不会刷新任何页面。")
    log("用法：在浏览器里点开岗位 → Ctrl+A、Ctrl+C → 回来看这里")
    log("按 Ctrl+C 退出")
    log("=" * 60)

    last_clip = ""
    while True:
        try:
            cur = pyperclip.paste() or ""
        except Exception:
            cur = ""

        if (cur and cur != last_clip and looks_like_jd(cur)
                and fp(cur) not in seen):
            last_clip = cur
            seen.add(fp(cur))
            save_seen(seen)

            title, company = guess_title_company(cur)
            log(f"检测到岗位：{title or '(未识别)'} {('｜' + company) if company else ''}")
            log("  生成中…")
            try:
                letter, angle = make_letter(client, resume_text, cur, history, CUR_CONF)
            except Exception as e:
                log(f"  ✗ 生成失败：{e}")
                continue

            if not letter:
                log("  ✗ 生成结果为空，跳过")
                continue

            append_outbox(title, company, letter)
            append_dashboard(title, company, letter)
            history.append(letter)

            try:
                pyperclip.copy(letter)
                log(f"  ✅ 话术已放回剪贴板（{len(letter)} 字，角度：{angle}）")
                log("  👉 切回浏览器聊天框，Ctrl+V 然后回车发送")
            except Exception as e:
                log(f"  ⚠ 写剪贴板失败（{e}），话术见 pipeline/outbox.md")
            log(f"  正文：{letter}\n")

        time.sleep(1.2)


# ---------------------------------------------------------------------------
# 批量模式：处理 inbox.md 里攒的 JD
# ---------------------------------------------------------------------------
def batch():
    if not os.path.exists(INBOX):
        with open(INBOX, "w", encoding="utf-8") as f:
            f.write("# 把岗位 JD 粘到这里，用一行 --- 分隔多条；然后运行：\n"
                    "# python clip_pipeline.py --batch\n\n")
        log(f"已创建 {INBOX}，请把 JD 粘进去后再运行")
        return

    with open(INBOX, encoding="utf-8") as f:
        raw = f.read()

    blocks = [b.strip() for b in re.split(r"^-{3,}$", raw, flags=re.M) if b.strip()]
    blocks = [b for b in blocks if looks_like_jd(b)]

    if not blocks:
        log(f"{INBOX} 里没找到像 JD 的内容（太短或缺少岗位关键词）")
        return

    log(f"共 {len(blocks)} 个 JD，开始批量生成（场景：{CUR_CONF['label']}）")
    resume_text = read_resumes(CUR_CONF["lang"])
    client = build_client()
    history = load_history_letters()

    for i, jd in enumerate(blocks, 1):
        title, company = guess_title_company(jd)
        log(f"[{i}/{len(blocks)}] {title or '(未识别)'}")
        try:
            letter, angle = make_letter(client, resume_text, jd, history, CUR_CONF)
        except Exception as e:
            log(f"  ✗ 失败：{e}")
            continue
        if not letter:
            continue
        append_outbox(title, company, letter)
        append_dashboard(title, company, letter)
        history.append(letter)
        log(f"  ✅ {len(letter)} 字 ｜ 角度 {angle} ｜ {letter[:60]}…")

    log(f"全部完成，话术见 {OUTBOX}")


if __name__ == "__main__":
    CUR_SCENE = parse_scene(sys.argv)
    if CUR_SCENE not in SCENES:
        print(f"⚠ 未知场景「{CUR_SCENE}」，可选：{', '.join(SCENES)}，已回退到 zh_job")
        CUR_SCENE = "zh_job"
    CUR_CONF = SCENES[CUR_SCENE]

    try:
        if "--batch" in sys.argv:
            batch()
        else:
            watch()
    except KeyboardInterrupt:
        print("\n已退出。")
