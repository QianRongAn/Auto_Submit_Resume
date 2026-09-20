"""
轻量版「读简历 + 生成求职信」模块。

这是原项目 langchain_functions.py 的替代实现：
原版依赖 langchain + FAISS + sentence-transformers + torch（约 2GB），
而简历通常只有几千字，向量检索完全没有必要 —— 直接把整份简历文本喂给大模型即可。

依赖只有：pypdf + openai（OpenAI 兼容协议，DeepSeek 等国内大模型通用）
"""

import os
import glob

from pypdf import PdfReader
from openai import OpenAI
from dotenv import load_dotenv

# PyMuPDF 可选：某些 PDF（尤其 LaTeX/InDesign 排版、字符级定位的）用 pypdf
# 抽出来会是「每个字母占一行」，把大模型彻底带偏。PyMuPDF 读的是字形位置，
# 这种情况表现好得多。装不上就自动回退 pypdf。
try:
    import pymupdf as _fitz
except ImportError:
    try:
        import fitz as _fitz
    except ImportError:
        _fitz = None

from prompts import LETTER_SYSTEM_PROMPT, LETTER_USER_TEMPLATE, SCENES
from letter_variety import clean_letter   # 语言相关的后处理，中英文规则不同

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = (os.getenv("OPENAI_BASE_URL") or "").strip() or "https://api.deepseek.com"
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "").strip() or "deepseek-chat"

# 简历里的署名与联系方式，写在 .env 里
CANDIDATE_NAME = (os.getenv("CANDIDATE_NAME") or "").strip()
# 英文场景专用署名（LinkedIn / 海外投递用拼音名，别用中文名）
CANDIDATE_NAME_EN = (os.getenv("CANDIDATE_NAME_EN") or "").strip()
CANDIDATE_CONTACT = (os.getenv("CANDIDATE_CONTACT") or "").strip()

RESUME_DIR = os.path.join(BASE_DIR, "resume")
RESUME_DIR_EN = os.path.join(BASE_DIR, "resume_en")   # 英文简历放这里（投领英用）


def check_config():
    """启动前自检，缺什么就一次性说清楚，避免跑到一半才报错。"""
    problems = []

    if not OPENAI_API_KEY or OPENAI_API_KEY.startswith("sk-在这里"):
        problems.append("未配置 OPENAI_API_KEY：请打开 auto_job_find/.env 填入你的密钥")

    zh_pdfs = glob.glob(os.path.join(RESUME_DIR, "*.pdf"))
    en_pdfs = glob.glob(os.path.join(RESUME_DIR_EN, "*.pdf"))
    if not zh_pdfs and not en_pdfs:
        problems.append(f"没有找到简历 PDF：中文放到 {RESUME_DIR}，英文放到 {RESUME_DIR_EN}")

    return problems


def _repair_char_per_line(text):
    """
    修「每个字母占一行」的坏文本层。

    这种情况非常伤大模型：正常 2000 字的简历会膨胀成上万 token，
    而且单词被切碎，模型读到的是噪音。判定标准是单字符行占比 > 60%，
    命中就把换行全去掉、重新拼成连续文本。
    """
    lines = (text or "").split("\n")
    lines = [l for l in lines if l.strip()]
    if len(lines) < 40:
        return text
    single = sum(1 for l in lines if len(l.strip()) == 1)
    if single / len(lines) <= 0.6:
        return text
    print(f"[简历] ⚠ 检测到字符级文本层（{single}/{len(lines)} 行为单字符），已自动重拼")
    return "".join(l.strip() for l in lines)


def _extract_pdf_text(path):
    """抽一份 PDF 的文字。优先 PyMuPDF，失败或无结果再回退 pypdf。"""
    # 首选 PyMuPDF
    if _fitz is not None:
        try:
            doc = _fitz.open(path)
            try:
                text = "\n".join(page.get_text("text") for page in doc)
            finally:
                doc.close()
            text = _repair_char_per_line(text).strip()
            if text:
                return text, "PyMuPDF"
        except Exception as e:
            print(f"[警告] PyMuPDF 解析失败，回退 pypdf：{str(e)[:120]}")

    # 回退 pypdf
    reader = PdfReader(path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return _repair_char_per_line(text).strip(), "pypdf"


def read_resumes(lang="zh"):
    """
    读取简历 PDF，拼接为纯文本。

    lang="en" 时优先读 resume_en/（英文简历，投领英/海外岗用）；
    该目录为空则回退到 resume/。中文场景只读 resume/。
    """
    pdfs = []
    if (lang or "zh").startswith("en"):
        en_pdfs = sorted(glob.glob(os.path.join(RESUME_DIR_EN, "*.pdf")))
        if en_pdfs:
            pdfs = en_pdfs
            print(f"[简历] 使用英文简历目录 resume_en/")
        else:
            print(f"[简历] resume_en/ 里没有 PDF，回退到中文简历 resume/"
                  f"（英文话术质量会受影响，建议放一份英文简历）")
    if not pdfs:
        pdfs = sorted(glob.glob(os.path.join(RESUME_DIR, "*.pdf")))
    if not pdfs:
        raise FileNotFoundError(
            f"没有找到 PDF 简历（中文放 {RESUME_DIR}，英文放 {RESUME_DIR_EN}）")

    parts = []
    for path in pdfs:
        try:
            text, engine = _extract_pdf_text(path)
        except Exception as e:
            print(f"[警告] 解析 {os.path.basename(path)} 失败：{e}")
            continue
        if text:
            parts.append(f"--- {os.path.basename(path)} ---\n{text}")
            print(f"[简历] {os.path.basename(path)} 用 {engine} 提取 {len(text)} 字")
        else:
            print(f"[警告] {os.path.basename(path)} 没有提取到文字（可能是扫描件图片 PDF，"
                  f"请换成可复制文字的版本）")

    if not parts:
        raise ValueError("所有简历 PDF 都没提取到文字，请检查是否为扫描件")

    full = "\n\n".join(parts)
    print(f"[简历] 共读取 {len(parts)} 份 PDF，{len(full)} 字")
    return full


def build_client():
    if not OPENAI_API_KEY:
        raise ValueError("缺少 OPENAI_API_KEY，请先配置 .env")
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL, timeout=120)


def _ensure_signature(letter, name, is_en):
    """
    确保结尾是完整署名。

    模型在卡字数时（典型是 LinkedIn 备注 300 字符硬限）会把自己名字截断成 "Nan"，
    读起来像漏字，HR 也会觉得不专业。这里做一次收尾：
      - 结尾已含全名 → 不动
      - 结尾只有名字的一部分（"Nan"）→ 换成全名
      - 结尾完全没有名字 → 补上
    """
    letter = (letter or "").strip()
    name = (name or "").strip()
    if not letter or not name:
        return letter

    tail = letter[-120:]                      # 只看结尾一段，避免误判正文里的名字
    if name in tail:
        return letter

    for part in sorted([p for p in name.split() if p], key=len, reverse=True):
        if tail.rstrip(" .,|｜·-").endswith(part):
            idx = letter.rfind(part)
            head = letter[:idx].rstrip(" ,|·-")
            return (head + " " + name) if head else name

    return letter + (" | " if is_en else "｜") + name


def generate_letter(client, resume_text, job_description, character_limit=300,
                    angle_hint=None, avoid_hint=None, scene="zh_job"):
    """
    根据简历 + 岗位描述生成一段可直接粘贴发送的求职话术。
    返回纯文本（默认压成单行；套磁邮件场景保留段落）。

    scene: prompts.SCENES 里的键 —— zh_job / en_cover / en_note / en_phd
    angle_hint: 本条话术的切入角度（letter_variety 里选），让每条写法不同
    avoid_hint: 最近已发话术的避让清单，明确要求模型换句式
    """
    sc = SCENES.get(scene) or SCENES["zh_job"]
    is_en = sc["lang"] == "en"
    keep_newlines = scene == "en_phd"

    if len(resume_text) > 12000:
        resume_text = resume_text[:12000] + "\n（简历过长已截断）"

    system_prompt = sc["system"]
    if angle_hint:
        system_prompt += (("\n9. This draft's required angle: " + angle_hint) if is_en
                          else ("\n8. 本条话术的写法要求：" + angle_hint))

    # 署名：英文场景必须用拼音名（直接用中文名会显得像机器批量生成的）
    sign_name = ((CANDIDATE_NAME_EN or CANDIDATE_NAME or "Your Name") if is_en
                 else (CANDIDATE_NAME or "求职者"))

    user_prompt = sc["user"].format(
        character_limit=character_limit,
        job_description=job_description,
        resume_text=resume_text,
        candidate_name=sign_name,
        candidate_contact=CANDIDATE_CONTACT or ("(available on request)" if is_en else "（可在面试时提供）"),
    )
    if avoid_hint:
        user_prompt += "\n\n" + avoid_hint

    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.9,
        max_tokens=1500,
    )

    letter = resp.choices[0].message.content or ""

    # 统一交给 letter_variety.clean_letter 收尾 —— 它按语言区分规则：
    #   英文保留撇号（don't / group's），中文清掉"真诚的/此致"这类西式落款。
    # 之前这里自己写了一份通用清洗，结果把英文撇号也删了（"my group's" → "my groups"）。
    letter = clean_letter(letter, lang="en" if is_en else "zh",
                          keep_newlines=keep_newlines)

    return _ensure_signature(letter, sign_name, is_en)
