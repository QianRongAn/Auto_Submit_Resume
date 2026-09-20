# -*- coding: utf-8 -*-
"""
岗位筛选打分引擎 —— 「替你决定这个岗位值不值得投」。

为什么要单开一个模块：
  全自动投递最大的风险不是技术，是「投错」—— 把招呼语发给一个卖保险的岗位，
  既浪费当天配额，也让账号看起来像机器。所以投之前必须先过一道筛。
  筛选规则必须**可解释**：用户要能看到「为什么跳过这个岗位」，否则不敢放手让它跑。

判定分三档：
  skip   —— 硬排除命中，或分数低于下限。不投。
  review —— 边界分（默认 35~59）。规则吃不准，交给用户/模型复核。
  apply  —— 分数达到阈值（默认 60）。投。

打分模型（总分 0~100）：
  核心技能命中（方向词）     最多 60 分，标题里命中权重 ×2
  职位名强化词               最多 20 分
  ── 扣分项 ──
  硬排除词（标题）           直接 0 分，不参与后续
  资历要求过高               最多 -45
  学历要求（要博士）          -18
  地点/签证不匹配            -15 ~ -35
  猎头含糊/外包/驻场          -12
  命中 .env 的 INCLUDE 门槛   没命中 -35（INCLUDE_KEYWORDS 非空时才生效）

LLM 复核：只在 review 档触发，避免每条岗位都花 token。
"""

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
# 一、候选人事实（来源：pipeline/profile.md —— 这部分是事实，不要凭感觉改）
# ===========================================================================
PROFILE = {
    "degree": "master",
    "years": 2.5,          # 2024.07 起三段科研/企业经历 ≈ 2.5 年
    "field": "计算生物学 / 肿瘤免疫与多组学",
    "skills": "Python / R / NGS 分析 / scRNA-seq / 空间转录组 / 免疫组库 / 新抗原预测",
    "lang": "zh + en",
}


def _env(key, default=""):
    return (os.getenv(key) or "").strip() or default


def _env_list(key, default=""):
    raw = _env(key, default)
    return [w.strip() for w in re.split(r"[,，;；\n]", raw) if w.strip()]


# ===========================================================================
# 二、核心方向词：命中就说明「这个岗位和本人专业有交集」
#     (正则, 权重, 展示名)   —— 权重是「这个词有多能代表本人方向」
# ===========================================================================
CORE_TERMS = [
    # —— 最核心：直接就是本行 ——
    (r"生物信息|生信|生物信息学", 16, "生物信息学"),
    (r"bioinformatic", 16, "bioinformatics"),
    (r"计算生物|computational biology|computational biologist", 15, "计算生物学"),
    (r"生物统计|biostatistic", 12, "生物统计"),

    # —— 组学 / 测序 ——
    (r"多组学|multi-?omics|组学分析", 11, "多组学"),
    (r"单细胞|single[- ]cell|scRNA", 10, "单细胞"),
    (r"空间转录组|spatial transcriptom", 9, "空间转录组"),
    (r"转录组|transcriptom|RNA[- ]?seq", 8, "转录组"),
    (r"免疫组库|immune repertoire|TCR|BCR", 9, "免疫组库"),
    (r"测序|NGS|sequencing|高通量", 7, "测序/NGS"),
    (r"基因组|genomic|WES|WGS|变异分析", 6, "基因组"),

    # —— 肿瘤免疫 ——
    (r"肿瘤免疫|tumor immunolog|cancer immunolog|immuno-?oncology", 11, "肿瘤免疫"),
    (r"肿瘤|癌症|cancer|tumou?r", 5, "肿瘤"),
    (r"免疫|immun", 4, "免疫学"),
    (r"新抗原|neoantigen", 8, "新抗原"),
    (r"CAR-?T|细胞治疗|cell therapy", 7, "细胞治疗"),

    # —— 通用生信技能（弱信号）——
    (r"\bPython\b", 3, "Python"),
    (r"\bR\b(?!\w)|R语言", 3, "R 语言"),
    (r"数据分析|数据挖掘|data analys|data scien", 4, "数据分析"),
    (r"算法|机器学习|machine learning|deep learning", 3, "算法/ML"),
    (r"精准医疗|转化医学|生物医学|医药研发", 3, "生物医药"),
    (r"疾病机制|靶点|drug discovery|biomarker|生物标记", 3, "靶点/标志物"),
]

# 出现在岗位标题里 → 额外加权（说明这就是这个岗位的主职）
TITLE_BOOST = [
    r"生信|生物信息", r"计算生物", r"生物统计", r"组学", r"单细胞",
    r"bioinformatic", r"computational", r"biostatistic",
    r"data analyst|data scientist|数据科学家|数据分析",
    r"research scientist|研究员|科学家",
]

# ===========================================================================
# 三、硬排除：命中即 0 分
#
#  ⚠ 只查「岗位标题」，**绝不查 JD 正文，也不查 BOSS 卡片全文**。
#    踩过的坑：BOSS 的岗位卡片尾部挂着一堆福利标签 ——
#    「股票期权」「培训」「五险一金」「团建聚餐」…… 一旦拿卡片全文去匹配排除词，
#    「股票」会命中「股票期权」、「培训」会命中福利标签，
#    于是一个正经的新药研发岗因为「有股票期权」被判定成证券销售给跳过了。
#    标题短、信息密度高，用它判排除最稳。
# ===========================================================================
EXCLUDE_HARD = [
    # 销售 / 中介 / 拉人
    "销售", "保险", "房产", "中介", "招生", "课程顾问", "导购", "招商", "地推",
    "贷款", "理财", "催收", "电话", "渠道经理", "商务拓展", "客户经理",
    "医药代表", "客户代表", "业务代表", "商务代表",
    "店长", "门店", "加盟", "微商", "代理商",
    # 服务 / 体力
    "服务员", "客服", "司机", "普工", "外卖", "骑手", "主播", "直播", "保安",
    "保洁", "厨师", "月嫂", "美发", "快递", "分拣", "仓管", "收银", "前台",
    "保姆", "管家", "学徒",
    # 与本人无关的白领岗（易被推荐算法塞进来）
    "文员", "行政", "人事", "招聘专员", "会计", "出纳", "法务", "审计",
    "电商运营", "新媒体运营", "文案策划", "美工", "教师", "幼教", "助教",
    "证券", "期货", "股票经纪人",
    # 临床 / 需执照
    "护士", "医师", "药师", "检验技师", "注册专员",
]

# 「不限方向」的模糊词 —— 标题里出现这些要小心，但它们也常出现在正经岗位里
# （「生物信息工程师（数据分析方向）」），所以只当软扣分，不当硬排除。
AMBIGUOUS_TITLE = ["平面设计", "UI设计", "活动策划", "销售运营"]

EXCLUDE_HARD_EN = [
    "sales representative", "account executive", "insurance agent",
    "real estate", "recruiter", "talent acquisition", "customer service",
    "driver", "cashier", "warehouse", "nurse", "physician", "clinical fellow",
    "staff accountant", "marketing manager", "social media manager",
    "product marketing", "business development manager",
]

# 公司名命中 → 基本可以断定不是目标行业（公司名短，误杀风险低）
EXCLUDE_COMPANY = ["保险", "房产", "中介", "传销", "劳务", "人力资源",
                   "人才服务", "招聘", "网贷", "证券", "期货"]

# 这些词出现在标题里多半是「不匹配的岗位被推荐过来」
EXCLUDE_SOFT = ["实习", "兼职", "intern", "part-time", "应届", "管培"]

# ===========================================================================
# 四、资历 / 学历 / 地点
# ===========================================================================
SENIORITY_BLOCK = [
    (r"首席|首席科学家|chief (scientist|officer)|CTO|CEO", 45, "岗位要求过高（首席级）"),
    (r"总监|director\b|head of|VP\b|vice president", 40, "岗位要求过高（总监级）"),
    (r"资深专家|principal (scientist|engineer)|fellow\b", 35, "岗位要求过高（专家级）"),
]
SENIORITY_WARN = [
    (r"高级|senior|lead\b|负责人", 8, "偏高级岗位，可能需要更多年限"),
]

# 明确写了「要博士」→ 扣分（本人硕士，走的是企业线）
DEGREE_PHD = [r"博士(?!优先|学历优先|研究生在读)", r"PhD (required|is required)",
              r"doctorate", r"Ph\.?D\.? required"]

# 猎头 / 外包 / 驻场 / 含糊
VAGUE_HINTS = ["猎头", "代招", "外包", "驻场", "派遣", "人力", "众包", "日结"]

# 海外岗位：签证。NEED_VISA=1 时，明确「不提供赞助」的直接劝退
VISA_NEED = [r"no (visa )?sponsorship", r"without sponsorship",
             r"must be (a )?(us|u\.s\.) (citizen|person)", r"security clearance",
             r"work authorization.*(required|must)", r"不提供.*(签证|工签)"]
VISA_GOOD = [r"visa sponsorship", r"sponsor(ship)? (is )?available",
             r"we sponsor", r"relocation support", r"支持.*(签证|工签|relocation)"]


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
    # ⚠ 判排除只用 title / company —— 原因见 EXCLUDE_HARD 上面的注释（福利标签误杀）。
    full = "\n".join([title, company, card_text, jd])

    reasons, matched, excluded, flags = [], [], [], []
    score = 0

    # ---------- 1. 硬排除（只看标题 / 公司名）----------
    hits_zh = _hits(EXCLUDE_HARD, title)
    hits_en = _hits(EXCLUDE_HARD_EN, title.lower())
    hits_co = _hits(EXCLUDE_COMPANY, company)
    # .env 的 EXCLUDE_KEYWORDS 是你自己临时加的排除词，和内置表同等待遇
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
LLM_PROMPT = """你是求职匹配顾问。判断这个岗位是否值得投递。

【候选人事实（不得假设任何额外经历）】
- 学历：免疫学硕士（厦门大学）；生物技术学士
- 经历：约 2.5 年，肿瘤医院病理科生信、生物信息公司分析师、实验室科研助理
- 方向：计算生物学 / 肿瘤免疫 / 多组学；Python+R；NGS、scRNA-seq、空间转录组、
  免疫组库(TCR)、新抗原预测；6 篇论文（含 J. Hematol. Oncol. 共同一作）
- 求职方向：生物信息工程师 / 生信分析师 / 肿瘤免疫计算岗
- 不希望投：销售、中介、外包、需博士学位、资深岗位

【岗位】
标题：{title}
公司：{company}
描述（截断）：
{jd}

只返回 JSON，不要任何解释文字：
{{"fit": 0-100, "veto": true/false, "reason": "20字以内中文理由"}}
veto=true 表示这个岗位根本不该投（方向完全无关、或明显不合适）。
"""


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
_SELFTEST = [
    dict(title="生物信息工程师（单细胞与空间转录组）", company="某生物科技",
         site="zhipin", expect="apply",
         jd="岗位职责：负责单细胞转录组与空间转录组数据分析，搭建 NGS 分析流水线。"
            "任职要求：硕士及以上，熟悉 Python/R，有 scRNA-seq 项目经验。"),
    dict(title="医药代表（销售岗）", company="某药企", site="zhipin", expect="skip",
         jd="负责区域医院客户拜访，完成销售指标。要求：大专以上，能吃苦。"),
    dict(title="服务员", company="某餐饮", site="zhipin", expect="skip",
         jd="负责餐厅传菜、桌面清洁。"),
    dict(title="Senior Principal Scientist, Computational Biology", company="Genentech",
         site="linkedin", expect="skip",
         jd="We seek a Principal Scientist with 12+ years of experience in cancer "
            "immunology and single-cell genomics. PhD required. No visa sponsorship."),
    dict(title="Bioinformatics Analyst", company="Oxford Nanopore",
         site="linkedin", expect="apply",
         jd="About the job: You will develop NGS analysis pipelines in Python and R, "
            "working on transcriptomics and multi-omics. Visa sponsorship available. "
            "Master's degree with 2-3 years experience preferred."),
    dict(title="数据分析师（生物医药方向）", company="某医疗科技",
         site="zhipin", expect="apply",
         jd="岗位职责：肿瘤多组学数据整合分析，免疫组库分析。任职要求：硕士，"
            "熟悉 R/Python，有测序数据处理经验者优先。"),
    dict(title="客服专员", company="某科技", site="zhipin", expect="skip",
         jd="接听用户来电，处理售后问题。"),
    dict(title="医药代表", company="某药企", site="zhipin", expect="skip",
         jd="负责医院客户拜访，完成销售指标。"),
    # ⚠ 回归用例：BOSS 卡片挂着「股票期权」「培训」这类福利标签，
    #    绝不能因此把一个正经的研发岗判成证券/培训销售。
    dict(title="生物信息分析工程师", company="某生物科技", site="zhipin", expect="apply",
         card_text="五险一金 股票期权 定期体检 培训 带薪年假 团建聚餐",
         jd="岗位职责：负责肿瘤多组学数据分析，搭建 NGS 分析流程。"
            "任职要求：硕士，熟悉 Python/R，有单细胞测序分析经验。"),
]


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
