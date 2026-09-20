"""
本地话术服务 —— 给油猴脚本 boss_ai_helper.user.js 调用的 HTTP 接口。

为什么要有这个东西：
  油猴脚本跑在你的真实 Edge 页面里（真实登录态、真实指纹，没有 webdriver 指纹，
  也就不存在 BOSS 的驱动反检测问题）。但脚本是 JavaScript，拿不到我们这边的
  简历解析 + DeepSeek 话术引擎。所以中间放一个只监听 127.0.0.1 的小服务，
  把两者接起来。

接口：
  GET  /health            → 自检信息（场景列表、简历是否就绪、关键配置是否齐全、筛选阈值）
  POST /letter            → {"jd": "...", "scene": "zh_job"} 返回定制话术
  POST /match             → {"title","company","jd","card_text","site"} 返回筛选判定
                            （apply / review / skip + 分数 + 可解释理由）
  POST /record            → 把「已投 / 已跳过 / 需人工」写进 pipeline 归档
  POST /letter 的返回     → {"ok": true, "letter": "...", "angle": "...", "chars": N}

安全：
  - 只绑定 127.0.0.1，外网访问不到
  - 不碰浏览器、不碰你的 BOSS 账号，只做文本进 / 文本出
"""

import os
import csv
import sys
import json
import threading
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import resume_ai                      # noqa: E402
import clip_pipeline as cp            # noqa: E402  复用场景表 / 记录 / 防重复历史
import matcher                        # noqa: E402  岗位筛选打分引擎

HOST = (os.getenv("LETTER_HOST") or "127.0.0.1").strip()
PORT = int((os.getenv("LETTER_PORT") or "8765").strip())

PIPE_DIR = os.path.join(os.path.dirname(BASE_DIR), "pipeline")
SKIPPED_CSV = os.path.join(PIPE_DIR, "skipped.csv")
LOG_CSV = os.path.join(PIPE_DIR, "applied.csv")

# 全局状态：懒加载 + 一把锁。生成一次要 3~5 秒，并发请求串行排队，
# 避免防重复历史被两个请求同时读改写坏。
_LOCK = threading.Lock()
_STATE = {"resume": {}, "client": None, "history": None}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}")


def _ensure(scene):
    """按需加载简历 / 客户端 / 历史。返回 (resume_text, client, scene_conf)。"""
    conf = cp.SCENES[scene]
    lang = conf["lang"]

    if _STATE["client"] is None:
        _STATE["client"] = resume_ai.build_client()
    if lang not in _STATE["resume"]:
        _STATE["resume"][lang] = resume_ai.read_resumes(lang)
    if _STATE["history"] is None:
        _STATE["history"] = cp.load_history_letters()
        log(f"已加载历史话术 {len(_STATE['history'])} 条（用于防重复）")

    return _STATE["resume"][lang], _STATE["client"], conf


def make_letter(jd, scene):
    """生成一条话术。返回 dict，异常时 raise。"""
    if scene not in cp.SCENES:
        raise ValueError(f"未知场景「{scene}」，可选：{', '.join(cp.SCENES)}")

    with _LOCK:
        resume_text, client, conf = _ensure(scene)

        # clip_pipeline.make_letter 读的是它模块级的 CUR_SCENE / CUR_CONF，
        # 这里必须先同步过去，否则记录会串场景。
        cp.CUR_SCENE = scene
        cp.CUR_CONF = conf

        title, company = cp.guess_title_company(jd)
        letter, angle = cp.make_letter(
            client, resume_text, jd, _STATE["history"], conf)

        if not letter:
            raise RuntimeError("模型返回了空话术")

        # 落盘：和剪贴板管道共用同一套归档，方便统一查看
        cp.append_outbox(title, company, letter)
        cp.append_dashboard(title, company, letter)
        _STATE["history"].append(letter)
        if len(_STATE["history"]) > 200:
            _STATE["history"] = _STATE["history"][-100:]

    return {"letter": letter, "angle": angle, "chars": len(letter),
            "scene": scene, "label": conf["label"],
            "title": title, "company": company}


def make_match(data):
    """岗位筛选判定。规则打分 + 边界分 LLM 复核。"""
    use_llm = data.get("use_llm", True)
    with _LOCK:
        if _STATE["client"] is None:
            try:
                _STATE["client"] = resume_ai.build_client()
            except Exception as e:
                log(f"⚠ 模型客户端初始化失败（筛选将只走规则）：{e}")
                _STATE["client"] = None
        client = _STATE["client"]

    r = matcher.decide(
        title=(data.get("title") or "").strip(),
        company=(data.get("company") or "").strip(),
        jd=(data.get("jd") or "").strip()[:12000],
        card_text=(data.get("card_text") or "").strip()[:1000],
        site=(data.get("site") or "").strip(),
        client=client,
        threshold=data.get("threshold"),
        use_llm=bool(use_llm),
    )
    return r


def _append_csv(path, header, row):
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def make_record(data):
    """
    记录一条投递结果。这是「全自动跑完之后你还能复盘」的关键 ——
    投了哪些、跳过了哪些、为什么跳过，全部落盘。
    """
    os.makedirs(PIPE_DIR, exist_ok=True)
    site = (data.get("site") or "").strip()
    job_id = str(data.get("job_id") or "").strip()
    title = (data.get("title") or "未知岗位").strip()[:120]
    company = (data.get("company") or "").strip()[:120]
    url = (data.get("url") or "").strip()[:400]
    score = data.get("score")
    verdict = (data.get("verdict") or "").strip()
    status = (data.get("status") or verdict or "unknown").strip()
    reason = (data.get("reason") or "").strip()[:300]
    letter = (data.get("letter") or "").strip()
    ts = f"{datetime.now():%Y-%m-%d %H:%M:%S}"

    if status in ("sent", "filled") and letter:
        # 走剪贴板管道同一套归档：这样防重复历史也能吃到自动投递的话术
        scene = (data.get("scene") or cp.CUR_SCENE or "zh_job")
        if scene in cp.SCENES:
            cp.CUR_SCENE = scene
            cp.CUR_CONF = cp.SCENES[scene]
        with _LOCK:
            cp.append_outbox(title, company, letter)
            cp.append_dashboard(title, company, letter)
            if _STATE["history"] is not None:
                _STATE["history"].append(letter)

    _append_csv(LOG_CSV,
                ["时间", "站点", "岗位ID", "岗位", "公司", "分数", "判定", "状态", "理由", "链接"],
                [ts, site, job_id, title, company, score, verdict, status, reason, url])

    if status == "skip":
        _append_csv(SKIPPED_CSV,
                    ["时间", "站点", "岗位ID", "岗位", "公司", "分数", "理由", "链接"],
                    [ts, site, job_id, title, company, score, reason, url])

    return {"ok": True, "logged": status, "file": os.path.basename(LOG_CSV)}


class Handler(BaseHTTPRequestHandler):
    server_version = "LetterBridge/1.0"

    # 安静一点，别把每条请求都刷屏
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/health"):
            problems = resume_ai.check_config()
            self._send(200, {
                "ok": not problems,
                "problems": problems,
                "scenes": {k: v["label"] for k, v in cp.SCENES.items()},
                "model": resume_ai.OPENAI_MODEL,
                "base_url": resume_ai.OPENAI_BASE_URL,
                "candidate_zh": resume_ai.CANDIDATE_NAME,
                "candidate_en": resume_ai.CANDIDATE_NAME_EN,
                "history_count": len(cp.load_history_letters()),
                # —— 筛选配置：脚本面板会显示这些，方便你核对筛选是否按预期在跑 ——
                "match": {
                    "threshold": int(matcher._env("MATCH_THRESHOLD", "60")),
                    "include": matcher._env_list("INCLUDE_KEYWORDS", ""),
                    "exclude": matcher._env_list("EXCLUDE_KEYWORDS", ""),
                    "target_regions": matcher._env_list("TARGET_REGIONS", ""),
                    "need_visa": matcher._env("NEED_VISA", "1") == "1",
                    "hard_exclude_terms": len(matcher.EXCLUDE_HARD),
                },
                # —— 候选人资质应答：Easy Apply 自动填表的「如实答案」，脚本照此勾选 ——
                "screening": {
                    "auto_answer": matcher._env("AUTO_ANSWER_SCREENING", "1") == "1",
                    "require_sponsorship": matcher._env("ANS_REQUIRE_SPONSORSHIP", "yes")
                        .strip().lower() in ("1", "yes", "true", "y", "是"),
                    "authorized": matcher._env("ANS_AUTHORIZED", "no")
                        .strip().lower() in ("1", "yes", "true", "y", "是"),
                    "nationality": matcher._env("ANS_NATIONALITY", "China").strip(),
                },
            })
            return
        self._send(404, {"ok": False, "error": "unknown path"})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            data = json.loads(raw or "{}")
        except Exception as e:
            self._send(400, {"ok": False, "error": f"请求体不是合法 JSON：{e}"})
            return

        if path == "/letter":
            jd = (data.get("jd") or "").strip()
            scene = (data.get("scene") or "zh_job").strip()

            if len(jd) < 30:
                self._send(400, {"ok": False, "error": "JD 太短，可能是没读到内容"})
                return
            if len(jd) > 12000:
                jd = jd[:12000]

            t0 = datetime.now()
            try:
                result = make_letter(jd, scene)
            except Exception as e:
                log(f"✗ 生成失败：{e}")
                self._send(500, {"ok": False, "error": str(e)})
                return

            cost = (datetime.now() - t0).total_seconds()
            log(f"✓ [{result['label']}] {result['title'] or '(未识别岗位)'} "
                f"｜ {result['chars']} 字 ｜ 角度 {result['angle']} ｜ {cost:.1f}s")
            self._send(200, {"ok": True, **result, "seconds": round(cost, 1)})
            return

        if path == "/match":
            t0 = datetime.now()
            try:
                r = make_match(data)
            except Exception as e:
                log(f"✗ 筛选失败：{e}")
                self._send(500, {"ok": False, "error": str(e)})
                return
            cost = (datetime.now() - t0).total_seconds()
            log(f"▣ [筛选] {(data.get('title') or '(未识别)')[:34]} "
                f"｜ {r['score']} 分 ｜ {r['verdict']}"
                f"{' ｜ 模型复核' if r.get('llm') else ''} ｜ {cost:.1f}s")
            self._send(200, {**r, "seconds": round(cost, 1)})
            return

        if path == "/record":
            try:
                r = make_record(data)
            except Exception as e:
                log(f"✗ 归档失败：{e}")
                self._send(500, {"ok": False, "error": str(e)})
                return
            self._send(200, r)
            return

        self._send(404, {"ok": False, "error": "unknown path"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--check", action="store_true", help="只做自检，然后退出")
    args = ap.parse_args()

    problems = resume_ai.check_config()
    if args.check:
        if problems:
            print("自检未通过：")
            for p in problems:
                print("  - " + p)
        else:
            print("自检通过。")
        return

    print("=" * 62)
    print("  本地话术服务（供油猴脚本调用）")
    print("=" * 62)
    if problems:
        print("⚠ 自检发现以下问题（可能影响生成）：")
        for p in problems:
            print("   - " + p)
    print(f"  监听：http://{HOST}:{args.port}")
    print(f"  模型：{resume_ai.OPENAI_MODEL} @ {resume_ai.OPENAI_BASE_URL}")
    print(f"  场景：{', '.join(cp.SCENES)}")
    print(f"  筛选阈值：{matcher._env('MATCH_THRESHOLD', '60')} 分"
          f" ｜ 硬排除词 {len(matcher.EXCLUDE_HARD)} 个"
          f" ｜ 目标区域 {matcher._env_list('TARGET_REGIONS', '') or '不限'}")
    print(f"  资质自动回填：{'开' if matcher._env('AUTO_ANSWER_SCREENING','1')=='1' else '关'}"
          f" ｜ 需赞助 {'是' if matcher._env('ANS_REQUIRE_SPONSORSHIP','yes').strip().lower() in ('1','yes','true','y','是') else '否'}"
          f" ｜ 国籍 {matcher._env('ANS_NATIONALITY','China')}")
    print("  接口：/health  /letter  /match  /record")
    print("  健康检查：浏览器打开 http://127.0.0.1:%d/health" % args.port)
    print("  按 Ctrl+C 退出")
    print("=" * 62)

    srv = ThreadingHTTPServer((HOST, args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
