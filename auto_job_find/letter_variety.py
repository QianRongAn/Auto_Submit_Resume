"""
话术定制模块：确保每条打招呼语都是「为这个岗位现写的」，绝不复制同一条。

参考 applypilot 的思路：投递质量比数量重要，模板化群发是账号被盯上的第一原因。

三层防重复机制：
1. 角度轮换 —— 6 种切入策略轮着用，避免每条都是「我对贵司岗位很感兴趣」开头
2. 历史避让 —— 把最近发过的话术喂给模型，明确要求换写法、换句式、换重点
3. 相似度终检 —— 生成后和历史话术做 bigram 相似度比对，太像就换角度重写
"""

import re


# ---------------------------------------------------------------------------
# 1. 角度库：每种角度决定这条话术「怎么开场、重点讲什么」
# ---------------------------------------------------------------------------
ANGLES = [
    ("技能直击",
     "直接点名岗位描述里最核心的 1-2 项硬性要求，用简历里对应的具体经历回应，"
     "开头不要客套，第一句就谈技能匹配。"),
    ("成果开场",
     "第一句就用简历里一个最亮眼的量化成果（数字、规模、效率提升）开场，"
     "再顺势说明这个成果如何迁移到这个岗位。"),
    ("业务理解",
     "从岗位所属的业务场景或行业痛点切入，展示你思考过对方要解决什么问题，"
     "再带出自己的匹配技能。"),
    ("跨界迁移",
     "坦诚承认背景不完全对口，但突出可迁移能力与快速学习的历史证据，"
     "给出一个「我曾经在陌生领域快速上手」的具体例子。"),
    ("问题场景",
     "以岗位工作中的一个具体问题或场景开场（比如「你们可能常遇到……」），"
     "展示你提前思考过实际业务，再说明你能带来什么。"),
    ("动机契合",
     "用两句话说明为什么这个岗位与你的职业规划高度契合（要具体到岗位内容，"
     "不能空谈热爱），然后立刻转入匹配的技能证明。"),
]


# ---------------------------------------------------------------------------
# 英文角度库（LinkedIn / 海外岗位 / 博士套磁）
# ---------------------------------------------------------------------------
ANGLES_EN = [
    ("direct skill match",
     "Lead with the single most important technical requirement in the posting and answer it "
     "immediately with one concrete project from the resume. No warm-up sentence."),
    ("quantified result",
     "Open with the most impressive quantified outcome from the resume (scale, effect size, "
     "throughput, publication impact), then connect it to what this role needs."),
    ("domain insight",
     "Open by naming a real technical or scientific challenge implied by the role, show you "
     "understand the problem space, then bring in the matching skill."),
    ("transferable depth",
     "Acknowledge that the background is not a one-to-one match, then prove fast ramp-up with "
     "a specific instance of mastering an unfamiliar domain or method."),
    ("problem-first",
     "Frame the first line around a concrete problem this team likely faces, offer how you "
     "would approach it, then support it with your evidence."),
    ("motivation fit",
     "State in one specific sentence why this exact role fits the candidate's trajectory "
     "(reference the actual work, not generic passion), then move straight to proof."),
]


def get_angles(lang="zh"):
    return ANGLES_EN if (lang or "zh").startswith("en") else ANGLES


def _bigrams(text):
    text = re.sub(r"[\s，。、；：！？.,;:!?\-|/（）()【】\[\]]", "", text or "")
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else {text}


def _word_grams(text, n=2):
    """英文用词级 n-gram：字符级对英文太宽松，容易把不同文章判成相似。"""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < n:
        return set(words)
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _is_mostly_ascii(text):
    t = re.sub(r"\s", "", text or "")
    if not t:
        return True
    ascii_ratio = sum(1 for c in t if ord(c) < 128) / len(t)
    return ascii_ratio > 0.6


def similarity(a, b):
    """
    两段文字的相似度，0~1。中文按字符 bigram，英文按词级 bigram。
    超过 0.45 基本就是换汤不换药。
    """
    if _is_mostly_ascii(a) and _is_mostly_ascii(b):
        ga, gb = _word_grams(a), _word_grams(b)
    else:
        ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    union = len(ga | gb)
    return inter / union if union else 0.0


def clean_letter(text, lang="zh", keep_newlines=False):
    """
    后处理：压平、去 markdown、去西式落款。

    lang="en" 时保留英文的撇号（don't / candidate's），否则会被误删。
    keep_newlines=True 用于套磁邮件这类需要保留段落格式的场景。
    """
    t = (text or "").strip()

    if keep_newlines:
        out, blank = [], 0
        for line in t.splitlines():
            if not line.strip():
                blank += 1
                if blank > 1:
                    continue
            else:
                blank = 0
            out.append(line.rstrip())
        t = "\n".join(out).strip()
    else:
        t = " ".join(t.split())

    if (lang or "zh").startswith("en"):
        # 英文：只清 markdown 记号，保留撇号与句内引号
        for ch in ("**", "##", "`"):
            t = t.replace(ch, "")
        for junk in ("Sincerely,", "Best regards,", "Kind regards,", "Warm regards,"):
            if t.startswith(junk):
                t = t[len(junk):].strip()
    else:
        for ch in ('"', "'", "《", "》", "**", "##"):
            t = t.replace(ch, "")
        for junk in ("真诚的，", "真诚的:", "真诚的 ", "真诚的", "此致", "敬礼"):
            t = t.replace(junk, "")
        t = t.replace("，，", "，").replace("。。", "。")

    return t.strip()


def build_avoid_hint(history_letters, max_history=5, lang="zh"):
    """把最近发过的话术整理成「避让清单」，拼进 prompt。"""
    recent = [h for h in history_letters[-max_history:]]
    if not recent:
        return ""
    lines = "\n".join(f"{i+1}. {h[:80]}" for i, h in enumerate(recent))
    if (lang or "zh").startswith("en"):
        return (
            "[RECENTLY SENT MESSAGES — do NOT reuse their structure, opening, phrasing "
            "or sentence order. Write something structurally different:]\n" + lines
        )
    return (
        "【你最近已经发出去的话术】（下面这些绝对不能雷同，"
        "句式、开头、行文顺序都必须换新的写法）：\n" + lines
    )


def generate_unique_letter(raw_generate, resume_text, job_desc, character_limit,
                           history_letters, max_try=3, log=print, lang="zh",
                           keep_newlines=False, hard_char_limit=None):
    """
    带防重复的话术生成。

    raw_generate: resume_ai.generate_letter 同签名的底层生成函数，
                  额外接受 angle_hint 与 avoid_hint 两个参数。
    lang: "zh" 用中文角度库，"en" 用英文角度库。
    keep_newlines: 保留段落（套磁邮件用）
    hard_char_limit: 硬性字符上限（LinkedIn connection note 是 300，超了发不出去），
                     超限会带着「上一版太长」的提示重写。
    返回 (letter, angle_name)；重试用尽仍不理想则返回最接近要求的那版。
    """
    import random as _rd
    tried, best_similar, best_short = [], None, None
    pool = get_angles(lang)
    candidates = _rd.sample(pool, len(pool))
    extra_hint = ""

    for i in range(min(max_try, len(candidates))):
        angle_name, angle_hint = candidates[i]
        avoid = build_avoid_hint(history_letters, lang=lang)
        if extra_hint:
            avoid = extra_hint + ("\n" + avoid if avoid else "")

        raw = raw_generate(resume_text, job_desc, character_limit,
                           angle_hint=angle_hint, avoid_hint=avoid)
        letter = clean_letter(raw, lang=lang, keep_newlines=keep_newlines)
        if not letter:
            continue

        # 硬字符上限：LinkedIn 的 connection note 超过 300 字符根本发不出去
        if hard_char_limit and len(letter) > hard_char_limit:
            if log:
                log(f"  · 第 {i+1} 版 {len(letter)} 字符，超出 {hard_char_limit} 上限，重写")
            if best_short is None or len(letter) < len(best_short[2]):
                best_short = (len(letter), angle_name, letter)
            extra_hint = (f"Your previous draft was {len(letter)} characters — TOO LONG. "
                          f"You MUST stay strictly under {hard_char_limit} characters this time. "
                          f"Cut aggressively; keep only the single strongest point.")
            continue

        worst = max((similarity(letter, h) for h in history_letters), default=0.0)
        tried.append((worst, angle_name, letter))
        if log:
            log(f"  · 第 {i+1} 版（角度：{angle_name}），{len(letter)} 字符，"
                f"与历史最高相似度 {worst:.2f}")

        if worst < 0.45:
            return letter, angle_name
        if best_similar is None or worst < best_similar[0]:
            best_similar = (worst, angle_name, letter)

    if best_similar:
        if log:
            log(f"  ⚠ 重试 {len(tried)} 次仍偏相似（{best_similar[0]:.2f}），"
                f"取最不像的一版，建议人工把关")
        return best_similar[2], best_similar[1]
    if best_short:
        if log:
            log(f"  ⚠ 始终压不到 {hard_char_limit} 字符以内（最短 {best_short[0]}），"
                f"请手动删减后发送")
        return best_short[2], best_short[1]
    return "", ""
