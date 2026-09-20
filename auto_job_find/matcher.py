# -*- coding: utf-8 -*-
"""
岗位筛选打分引擎：替你决定这个岗位值不值得投。

为什么要单开一个模块：
  全自动投递最大的风险不是技术，是「投错」，比如把招呼语发给一个卖保险的岗位，
  既浪费当天配额，也让账号看起来像机器。所以投之前必须先过一道筛。
  筛选规则必须**可解释**：用户要能看到「为什么跳过这个岗位」，否则不敢放手让它跑。

筛选用的方向词、排除词、候选人事实全部来自 matcher_profile.json，
换一个求职方向只需改配置，不必改这个文件里的代码。

判定分三档：
  skip    硬排除命中，或分数低于下限。不投。
  review  边界分（默认 35~59）。规则吃不准，交给用户/模型复核。
  apply   分数达到阈值（默认 60）。投。

打分模型（总分 0~100）：
  核心技能命中（CORE_TERMS）   最多 +78，标题里命中权重 ×2
  职位名强化词（TITLE_BOOST）  命中 +18
  扣分项：
    硬排除词（标题）           直接 0 分，不参与后续
    资历要求过高               最多 -45
    学历要求（要博士）         -18
    地点/签证不匹配            -15 ~ -35
    猎头含糊/外包/驻场         -12
    命中 .env 的 INCLUDE 门槛  没命中 -35（INCLUDE_KEYWORDS 非空时才生效）

LLM 复核：只在 review 档触发，避免每条岗位都花 token。
"""

import io
import os
import re
import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 复用 resume_ai 的 .env 加载（它 import 时就 load_dotenv 了）
try:
    import resume_ai  # noqa: F401
except Exception:
    pass


# ===========================================================================
# 一、配置加载
#
#  所有和个人背景相关的词表都放在配置文件里，代码本身不含任何人的具体方向。
#  换一个求职方向只需改配置，不必读源码，也不必碰这个文件。
#
#  加载顺序：
#    1. matcher_profile.json          你自己的私有配置（已在 .gitignore 中）
#    2. matcher_profile.example.json  仓库自带的示例（数据分析方向）
#    3. 内置兜底                       两个都没有时用一套最小规则，并打印告警
#
#  建自己的配置：复制 matcher_profile.example.json 为 matcher_profile.json 再改。
# ===========================================================================
_PRIVATE_CFG = os.path.join(BASE_DIR, "matcher_profile.json")
_EXAMPLE_CFG = os.path.join(BASE_DIR, "matcher_profile.example.json")

# 两个配置文件都缺失时的兜底提示词，只描述岗位、不假设任何候选人经历
_FALLBACK_PROMPT = """你是求职匹配顾问。判断这个岗位是否值得投递。

【候选人事实】
（未配置。请在 matcher_profile.json 的 PROFILE 与 LLM_PROMPT 里填写。）

【岗位】
标题：{title}
公司：{company}
描述（截断）：
{jd}

只返回 JSON，不要任何解释文字：
{{"fit": 0-100, "veto": true/false, "reason": "20字以内中文理由"}}
veto=true 表示这个岗位根本不该投（方向完全无关、或明显不合适）。
"""


def _load_cfg():
    for path in (_PRIVATE_CFG, _EXAMPLE_CFG):
        if not os.path.exists(path):
            continue
        try:
            with io.open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["_source"] = os.path.basename(path)
            return cfg
        except Exception as e:
            print(f"[matcher] 配置解析失败，跳过 {os.path.basename(path)}：{e}",
                  file=sys.stderr)
    print("[matcher] 没找到 matcher_profile.json，也没找到 matcher_profile.example.json，"
          "当前只用一套最小规则，判定会偏保守。"
          "请复制 matcher_profile.example.json 为 matcher_profile.json 后按需填写。",
          file=sys.stderr)
    return {"_source": "内置兜底"}


CFG = _load_cfg()
CFG_SOURCE = CFG.get("_source", "")


def _tuples(rows):
    """配置里的 [正则, 权重, 名称] 转成元组列表，_hits 靠 tuple 类型做判断。"""
    return [tuple(x) for x in (rows or [])]


PROFILE = CFG.get("PROFILE") or {"degree": "master", "years": 0,
                                 "field": "", "skills": "", "lang": "zh + en"}
CORE_TERMS = _tuples(CFG.get("CORE_TERMS"))
TITLE_BOOST = CFG.get("TITLE_BOOST") or []
EXCLUDE_HARD = CFG.get("EXCLUDE_HARD") or []
EXCLUDE_HARD_EN = CFG.get("EXCLUDE_HARD_EN") or []
EXCLUDE_COMPANY = CFG.get("EXCLUDE_COMPANY") or []
EXCLUDE_SOFT = CFG.get("EXCLUDE_SOFT") or []
AMBIGUOUS_TITLE = CFG.get("AMBIGUOUS_TITLE") or []
SENIORITY_BLOCK = _tuples(CFG.get("SENIORITY_BLOCK"))
SENIORITY_WARN = _tuples(CFG.get("SENIORITY_WARN"))
DEGREE_PHD = CFG.get("DEGREE_PHD") or []
VAGUE_HINTS = CFG.get("VAGUE_HINTS") or []
VISA_NEED = CFG.get("VISA_NEED") or []
VISA_GOOD = CFG.get("VISA_GOOD") or []


def _env(key, default=""):
    return (os.getenv(key) or "").strip() or default


def _env_list(key, default=""):
    raw = _env(key, default)
    return [w.strip() for w in re.split(r"[,，;；\n]", raw) if w.strip()]


# ===========================================================================
# 二、核心方向词（配置项 CORE_TERMS / TITLE_BOOST）
#
#     格式：[正则, 权重, 展示名]
#     权重表示「这个词有多能代表目标求职方向」，命中标题时权重翻倍。
#     TITLE_BOOST 里的词出现在标题中，额外再给一次加分。
#
#     要换求职方向，改 matcher_profile.json 里的 CORE_TERMS 就够，
#     不必动这个文件里的任何代码。
# ===========================================================================

# ===========================================================================
# 三、硬排除：命中即 0 分（配置项 EXCLUDE_HARD / EXCLUDE_HARD_EN /
#     EXCLUDE_COMPANY / AMBIGUOUS_TITLE / EXCLUDE_SOFT）
#
#  ⚠ EXCLUDE_HARD 只查「岗位标题」，EXCLUDE_COMPANY 只查「公司名」，
#    **绝不查 JD 正文，也不查 BOSS 卡片全文**。
#    踩过的坑：BOSS 的岗位卡片尾部挂着一堆福利标签，
#    像「培训」「五险一金」「带薪年假」「团建聚餐」这类字样。一旦拿卡片全文
#    去匹配排除词，这些福利描述就会和排除词撞上，
#    于是一个正经的研发岗被误判成销售类岗位跳过了。
#    标题短、信息密度高，用它判排除最稳。
#
#  ⚠ 这张表因人而异，取决于你要找什么工作。示例配置只放了公认该跳过的类型，
#    你自己的偏好写进 matcher_profile.json，不要改这个文件。
# ===========================================================================


# ===========================================================================
# 四、资历 / 学历 / 地点（配置项 SENIORITY_BLOCK / SENIORITY_WARN /
#     DEGREE_PHD / VAGUE_HINTS / VISA_NEED / VISA_GOOD）
#
#     这些是「相对目标画像而言偏高」的扣分项，不是硬排除，所以走扣分而非归零。
#     签证两项只在 .env 的 NEED_VISA=1 时生效。
# ===========================================================================


def _hits(patterns, text):
    """返回 [(匹配到的原文, ...)]，patterns 可以是字符串列表或 (正则,权重,名) 列表。"""
    out = []
    t = text or ""
    low = t.lower()
    for p in patterns or []:
        if isinstance(p, tuple):
            rx = re.compile(p[0], re.I)
            m = rx.search(low) if p[0].isascii() else rx.search(t)
            if m:
                out.append(p)
        else:
            if p.isascii():
                if p.lower() in low:
                    out.append(p)
            elif p in t:
                out.append(p)
    return out


def _max_required_years(text):
    """从 JD 里抠出「要求 N 年经验」的最大 N。抠不到返回 0。"""
    t = text or ""
    nums = []
    for m in re.finditer(r"(\d{1,2})\s*(?:年|年以[上下]|years?)", t, re.I):
        try:
            nums.append(int(m.group(1)))
        except ValueError:
            pass
    # 「5-8年」「5年以上」这种区间也覆盖了；只取 1~25 的合理值
    nums = [n for n in nums if 1 <= n <= 25]
    return max(nums) if nums else 0


# ===========================================================================
# 五、主打分函数
# ===========================================================================
def score_job(title="", company="", jd="", card_text="", site="", scene=None,
              threshold=None, target_regions=None, need_visa=None):
    """
    给一个岗位打分。

    title/company/jd/card_text: 页面能拿到的信息，越全越准
    site: 'zhipin' | 'linkedin'
    threshold: 达到多少分算 apply（默认取 .env MATCH_THRESHOLD 或 60）

    返回 dict：
      {ok, score, verdict, reasons[], matched[], excluded[], flags[], reason_line}
    """
    threshold = int(threshold or _env("MATCH_THRESHOLD", "60"))
    if target_regions is None:
        target_regions = _env_list("TARGET_REGIONS", "")
    if need_visa is None:
        need_visa = _env("NEED_VISA", "1") == "1"

    title = (title or "").strip()
    company = (company or "").strip()
    jd = (jd or "").strip()
    card_text = (card_text or "").strip()
    # ⚠ 判排除只用 title / company，原因见上面第三、四节的注释（福利标签误杀）。
    full = "\n".join([title, company, card_text, jd])

    reasons, matched, excluded, flags = [], [], [], []
    score = 0

    # ---------- 1. 硬排除（只看标题 / 公司名）----------
    hits_zh = _hits(EXCLUDE_HARD, title)
    hits_en = _hits(EXCLUDE_HARD_EN, title.lower())
    hits_co = _hits(EXCLUDE_COMPANY, company)
    # .env 的 EXCLUDE_KEYWORDS 是临时补充的排除词，和配置里的表同等待遇。
    # 注意它只能「追加」，删词要改 matcher_profile.json。
    hits_env = _hits(_env_list("EXCLUDE_KEYWORDS", ""), title + " " + company)
    if hits_zh or hits_en or hits_co or hits_env:
        bad = (hits_env + hits_zh + hits_en + hits_co)[:4]
        return _pack(0, "skip", [f"命中排除词：{'、'.join(bad)}"], [], bad, flags,
                     "硬排除：" + "、".join(bad))

    if not (title or jd):
        return _pack(0, "skip", ["没读到岗位标题和 JD"], [], [], flags, "信息不足")

    # ---------- 2. 核心技能命中 ----------
    core_total = 0
    for rx, w, name in CORE_TERMS:
        in_title = bool(re.search(rx, title, re.I))
        in_jd = bool(re.search(rx, full, re.I))
        if not in_jd:
            continue
        ww = w * 2 if in_title else w
        # 同一个方向词最多贡献一份，避免 JD 里反复出现把分数刷上天
        core_total += ww
        matched.append(f"{name}（{'标题' if in_title else 'JD'}）")

    # 标题里直接点名方向的，额外加权
    has_boost = any(re.search(p, title, re.I) for p in TITLE_BOOST)
    if has_boost:
        core_total += 18
        flags.append("职位名即目标方向")

    score += min(core_total, 78)
    if matched:
        reasons.append("技能命中：" + "、".join(matched[:6]))
    else:
        reasons.append("技能命中：无（没有出现任何目标方向词）")

    # ---------- 3. 扣分 ----------
    for rx, pen, label in SENIORITY_BLOCK:
        if re.search(rx, title, re.I) or re.search(rx, full, re.I):
            score -= pen
            excluded.append(label)
            break
    for rx, pen, label in SENIORITY_WARN:
        if re.search(rx, title, re.I):
            score -= pen
            excluded.append(label)
            break

    req_years = _max_required_years(jd[:2500] or full)
    if req_years:
        if req_years >= 8:
            score -= 45
            excluded.append(f"要求 {req_years} 年经验（本人约 {PROFILE['years']} 年）")
        elif req_years >= 5:
            score -= 28
            excluded.append(f"要求 {req_years} 年经验，偏高于本人")
        elif req_years >= 3:
            score -= 12
            excluded.append(f"要求 {req_years} 年经验")

    if any(re.search(p, full, re.I) for p in DEGREE_PHD):
        score -= 18
        excluded.append("要求博士（本人硕士，走企业线）")

    vague = _hits(VAGUE_HINTS, title + " " + company)
    if vague:
        score -= 12
        excluded.append("含「" + vague[0] + "」字样，岗位信息可能不实")

    soft = _hits(EXCLUDE_SOFT, title.lower())
    if soft:
        score -= 10
        excluded.append("实习/兼职/应届类岗位：" + soft[0])

    amb = _hits(AMBIGUOUS_TITLE, title)
    if amb:
        score -= 15
        excluded.append("标题方向存疑：" + amb[0])

    # ---------- 4. 地点 / 签证（主要针对 LinkedIn 海外岗）----------
    if target_regions:
        loc_text = " ".join([card_text, jd[:800]])
        if not any(r.lower() in loc_text.lower() for r in target_regions):
            score -= 15
            excluded.append("地点不在目标区域（" + "/".join(target_regions[:4]) + "）")
        else:
            flags.append("地点在目标区域")

    if need_visa:
        if any(re.search(p, full, re.I) for p in VISA_NEED):
            score -= 35
            excluded.append("明确不提供签证赞助")
        elif any(re.search(p, full, re.I) for p in VISA_GOOD):
            score += 8
            flags.append("明确提供签证赞助/搬迁支持")

    # ---------- 5. .env 的 INCLUDE 门槛 ----------
    inc = _env_list("INCLUDE_KEYWORDS", "")
    if inc:
        if any(k in full or k.lower() in full.lower() for k in inc):
            flags.append("命中 INCLUDE 关键词")
            score += 5
        else:
            score -= 35
            excluded.append("未命中 INCLUDE 关键词")

    # ---------- 6. 归一 & 判定 ----------
    score = max(0, min(100, int(round(score))))
    if score >= threshold:
        verdict = "apply"
    elif score >= max(25, threshold - 25):
        verdict = "review"
    else:
        verdict = "skip"

    line = f"{score} 分 → {verdict}"
    if matched:
        line += " ｜ 命中 " + "/".join(matched[:3])
    if excluded:
        line += " ｜ 扣分 " + "；".join(excluded[:2])

    return _pack(score, verdict, reasons, matched, excluded, flags, line)


def _pack(score, verdict, reasons, matched, excluded, flags, reason_line):
    return {"ok": True, "score": score, "verdict": verdict,
            "reasons": reasons, "matched": matched, "excluded": excluded,
            "flags": flags, "reason_line": reason_line}


# ===========================================================================
# 六、LLM 复核：只在 review 档用，省 token 也省时间
# ===========================================================================
# 复核提示词来自配置项 LLM_PROMPT，里面写候选人事实与判断口径。
# 必须保留 {title} {company} {jd} 三个占位符，否则 .format() 会报错。
LLM_PROMPT = CFG.get("LLM_PROMPT") or _FALLBACK_PROMPT


def llm_review(client, title, company, jd, timeout_note=None):
    """让模型复核边界岗位。失败返回 None（调用方回退到规则判定）。"""
    if client is None:
        return None
    try:
        resp = client.chat.completions.create(
            model=_env("OPENAI_MODEL", "deepseek-chat"),
            messages=[{"role": "user", "content": LLM_PROMPT.format(
                title=title or "(未识别)", company=company or "(未识别)",
                jd=(jd or "")[:2500])}],
            temperature=0,
            max_tokens=200,
        )
        txt = (resp.choices[0].message.content or "").strip()
        txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        return {"fit": int(data.get("fit", 0)),
                "veto": bool(data.get("veto")),
                "reason": str(data.get("reason", ""))[:60]}
    except Exception:
        return None


def decide(title="", company="", jd="", card_text="", site="", client=None,
           threshold=None, use_llm=True, target_regions=None, need_visa=None):
    """
    完整决策：规则打分 → 边界分交给模型复核 → 合并结论。
    LLM 只在 review 档触发；veto 或 fit 过低则降级为 skip，fit 很高则升为 apply。
    """
    r = score_job(title, company, jd, card_text, site, threshold=threshold,
                  target_regions=target_regions, need_visa=need_visa)
    r["llm"] = None
    if not use_llm or r["verdict"] != "review":
        return r

    rev = llm_review(client, title, company, jd)
    if not rev:
        return r
    r["llm"] = rev
    conf_final = int(round((r["score"] * 0.4) + (rev["fit"] * 0.6)))
    r["score"] = conf_final
    if rev["veto"] or rev["fit"] < 40:
        r["verdict"] = "skip"
    elif rev["fit"] >= 70:
        r["verdict"] = "apply"
    else:
        r["verdict"] = "review"
    r["reason_line"] = (f"规则 {r['score']} 分 → {r['verdict']}"
                        f" ｜ 模型复核：{rev['reason']}")
    r["reasons"].append(f"模型复核 {rev['fit']} 分：{rev['reason']}")
    return r


# ===========================================================================
# 七、自测
# ===========================================================================
# 自测用例来自配置项 SELFTEST，跟着配置里的方向词表走。
# 改完 CORE_TERMS 之后，用 python matcher.py --selftest 校验判定是否符合预期。
_SELFTEST = CFG.get("SELFTEST") or []


def _selftest():
    print("=" * 72)
    print(" matcher 自测")
    print("=" * 72)
    bad = 0
    for c in _SELFTEST:
        exp = c.pop("expect")
        r = score_job(**c)
        got = r["verdict"]
        # review 视为「没被硬排除」也算合格（边界岗位不确定是正常的）
        ok = (got == exp) or (exp in ("apply", "review") and got in ("apply", "review"))
        bad += 0 if ok else 1
        print(f"[{'OK ' if ok else 'FAIL'}] {r['score']:>3} 分 {got:<6} "
              f"期望 {exp:<6} ｜ {c['title'][:34]}")
        print(f"        {r['reason_line']}")
    print("-" * 72)
    print("全部通过 ✅" if not bad else f"{bad} 个用例不符合预期 ❌")
    return bad


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--title", default="")
    ap.add_argument("--jd", default="")
    ap.add_argument("--file", default="")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(1 if _selftest() else 0)

    jd = args.jd
    if args.file:
        with open(args.file, encoding="utf-8", errors="ignore") as f:
            jd = f.read()
    print(json.dumps(score_job(title=args.title, jd=jd), ensure_ascii=False, indent=2))
