#!/usr/bin/env python3
"""kordoc local — HWP/HWPX/PDF/DOCX/XLSX 를 kordoc 으로 읽어 로컬 Ollama 에 넘기는 문서 에이전트.
외부 파이썬 의존성 없음(stdlib). kordoc 은 같은 폴더의 node_modules 에서 실행 (npm install 한 번).

  npm install && python3 app.py                       # http://localhost:8766
  LLM_MODEL=gpt-oss:20b python3 app.py
  LLM_API=openai LLM_BASE_URL=http://gpu:8000/v1 LLM_MODEL=Qwen3-32B python3 app.py
  python3 app.py --cli summary 문서.hwpx
  python3 app.py --cli qa 문서.pdf "3장 예산은 얼마인가"
  python3 app.py --cli draft [문서.hwpx|-] "○○ 개선방안 보고서 써줘" [보고서|기안문|계획서|통지|회의록]

파이프라인:
  kordoc parse (문서 → Markdown, LLM 0콜)
  → Ollama 1콜 (summary / qa / draft, 토큰 스트리밍)
  → [draft] kordoc lint (표기법 검수) → kordoc generate --preset (HWPX) → kordoc validate
"""
import base64
import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.path.join(ROOT, "_workspace")
LLM_API = os.environ.get("LLM_API", "ollama")            # ollama | openai (vLLM·LM Studio·llama.cpp·TGI 등 /v1/chat/completions)
LLM_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1" if LLM_API == "openai" else "http://localhost:11434").rstrip("/")
OLLAMA = LLM_BASE
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
PORT = int(os.environ.get("PORT", "8766"))
NUM_CTX = int(os.environ.get("NUM_CTX", "16384"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "12000"))  # ponytail: 앞부분만 잘라 넣음, 긴 문서는 --format chunks + RAG 로 승급
TASKS = ("summary", "qa", "draft")
PRESETS = ("보고서", "기안문", "계획서", "통지", "회의록", "개조식", "업무보고", "서울방침", "보도자료")
EXTS = (".hwp", ".hwpx", ".hml", ".pdf", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp", ".md", ".txt")
_CLI = os.path.join(ROOT, "node_modules", "kordoc", "dist", "cli.js")
KORDOC = ["node", _CLI] if os.path.exists(_CLI) else ["npx", "-y", "kordoc@^4"]


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def kordoc(*args, cwd=None):
    r = subprocess.run([*KORDOC, *args], capture_output=True, text=True, cwd=cwd or ROOT, timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip()


# ── Ollama ──────────────────────────────────────────────────────────────
def _clean(out):
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()
    out = re.sub(r"^```\w*\s*\n", "", out)
    out = re.sub(r"\n?```\s*$", "", out)
    return out.strip()


def openai_chat(system, user, model, on_token=None):
    """OpenAI 호환 /v1/chat/completions 스트리밍 (vLLM·TGI·LM Studio 등)."""
    body = {"model": model, "stream": True, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    hdr = {"Content-Type": "application/json", **({"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})}
    req = urllib.request.Request(LLM_BASE + "/chat/completions", json.dumps(body).encode(), hdr)
    buf = []
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data:") or line == "data: [DONE]":
                    continue
                tok = (json.loads(line[5:])["choices"][0].get("delta") or {}).get("content") or ""
                if tok:
                    buf.append(tok)
                    if on_token:
                        on_token(tok)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    return "".join(buf)


def ollama(system, user, model, on_token=None):
    """Ollama /api/chat 스트리밍. think:false 미지원 모델이면 자동 재시도. LLM_API=openai 면 OpenAI 호환으로."""
    if LLM_API == "openai":
        return openai_chat(system, user, model, on_token)
    body = {"model": model, "stream": True, "think": False,
            "options": {"temperature": 0.2, "num_ctx": NUM_CTX},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in (0, 1):
        try:
            req = urllib.request.Request(OLLAMA + "/api/chat", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            buf = []
            with urllib.request.urlopen(req, timeout=3600) as r:
                for line in r:
                    if not line.strip():
                        continue
                    j = json.loads(line)
                    if "error" in j:
                        raise RuntimeError(j["error"])
                    tok = j.get("message", {}).get("content", "")
                    if tok:
                        buf.append(tok)
                        if on_token:
                            on_token(tok)
                    if j.get("done"):
                        break
            return "".join(buf)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if attempt == 0 and "think" in msg:
                body.pop("think")
                continue
            raise RuntimeError(f"Ollama HTTP {e.code}: {msg[:300]}")


def models():
    if LLM_API == "openai":
        req = urllib.request.Request(LLM_BASE + "/models", headers={"Authorization": f"Bearer {LLM_KEY}"} if LLM_KEY else {})
        with urllib.request.urlopen(req, timeout=10) as r:
            return [m["id"] for m in json.load(r)["data"]]
    with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=10) as r:
        return [m["name"] for m in json.load(r)["models"]]


# ── 프롬프트 ────────────────────────────────────────────────────────────
def prompts():
    txt = read(os.path.join(ROOT, "goal-prompt.md"))
    common, *rest = re.split(r"^## (\w+)\s*$", txt, flags=re.M)
    return common.strip(), {rest[i]: rest[i + 1].strip() for i in range(0, len(rest), 2)}


# ── 파이프라인 ──────────────────────────────────────────────────────────
def parse(src, d, log):
    """문서 → Markdown. md/txt 는 그대로."""
    if src.lower().endswith((".md", ".txt")):
        return read(src)
    out = os.path.join(d, "01_source.md")
    code, msg = kordoc("--silent", src, "-o", out, cwd=d)
    log.append(f"[kordoc parse] exit {code} {msg}"[:2000])
    if code != 0 or not os.path.exists(out):
        raise RuntimeError(f"kordoc 파싱 실패: {msg[-500:]}")
    return read(out)


def process(task, question="", file=None, model=MODEL, preset="보고서", emit=lambda ev: None):
    if task not in TASKS:
        raise ValueError(f"task 는 {TASKS} 중 하나")
    if preset not in PRESETS:
        preset = "보고서"
    run_id = f"{datetime.date.today()}-{secrets.token_hex(2)}"
    d = os.path.join(WS, run_id)
    os.makedirs(d)
    log, source, truncated = [], "", False

    if file:
        name = os.path.basename(file)
        if not name.lower().endswith(EXTS):
            raise ValueError(f"지원하지 않는 확장자: {name} (지원: {', '.join(EXTS)})")
        src = os.path.join(d, "00_" + name)
        shutil.copy(file, src)
        emit({"stage": "parse", "msg": f"kordoc 파싱 {name}"})
        source = parse(src, d, log)
        if len(source) > MAX_CHARS:
            source, truncated = source[:MAX_CHARS], True
            log.append(f"[truncate] {MAX_CHARS}자로 잘림 (MAX_CHARS 환경변수로 조정)")

    common, roles = prompts()
    system = common + "\n\n" + roles[task]
    user = (f"[문서 시작]\n{source}\n[문서 끝]" + ("\n(※ 문서가 길어 앞부분만 제공됨)" if truncated else "")
            if source else "[문서 없음]") + f"\n\n[요청]\n{question or {'summary': '요약해줘', 'qa': '핵심 내용은?', 'draft': '문서 내용으로 초안 작성'}[task]}"
    write(os.path.join(d, "01_prompt.txt"), user)

    emit({"stage": "llm", "msg": f"Ollama {model} ({task})", "reset": True})
    answer = _clean(ollama(system, user, model, on_token=lambda t: emit({"token": t})))
    write(os.path.join(d, "02_answer.md"), answer)

    result = {"run_id": run_id, "task": task, "model": model, "question": question, "file": os.path.basename(file) if file else None,
              "source": source, "truncated": truncated, "output": answer, "hwpx": None, "lint": None, "valid": None,
              "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    if task == "draft":
        emit({"stage": "lint", "msg": "kordoc lint (표기법 검수)"})
        _, out = kordoc("lint", "--json", "--munche", os.path.join(d, "02_answer.md"))  # --munche: 개조식 문체(당위·서술형 종결)까지
        try:
            result["lint"] = json.loads(out[out.index("{"):])
        except (ValueError, json.JSONDecodeError):
            result["lint"] = {"raw": out}
        emit({"stage": "generate", "msg": f"kordoc generate --preset {preset}"})
        hwpx = os.path.join(d, "03_result.hwpx")
        code, out = kordoc("generate", os.path.join(d, "02_answer.md"), "-o", hwpx, "--preset", preset)
        log.append(f"[kordoc generate] exit {code} {out}")
        if code == 0 and os.path.exists(hwpx):
            vcode, vout = kordoc("validate", hwpx)
            log.append(f"[kordoc validate] exit {vcode} {vout}")
            result["hwpx"], result["valid"], result["preset"] = "03_result.hwpx", vcode == 0, preset
    result["log"] = "\n".join(log)
    write(os.path.join(d, "result.json"), json.dumps(result, ensure_ascii=False, indent=1))
    return result


def list_runs():
    out = []
    if not os.path.isdir(WS):
        return out
    for name in sorted(os.listdir(WS), reverse=True)[:50]:
        p = os.path.join(WS, name, "result.json")
        if os.path.exists(p):
            try:
                j = json.load(open(p, encoding="utf-8"))
                out.append({k: j.get(k) for k in ("run_id", "task", "model", "file", "hwpx", "ts")}
                           | {"head": (j.get("question") or j.get("output") or "")[:40]})
            except Exception:
                pass
    return out


# ── HTTP ───────────────────────────────────────────────────────────────
HTML = read(os.path.join(ROOT, "ui.html")) if os.path.exists(os.path.join(ROOT, "ui.html")) else "ui.html 없음"
RUN_RE = r"\d{4}-\d{2}-\d{2}-[0-9a-f]{4}"


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if "/api/run" in (a[0] if a else ""):
            super().log_message(fmt, *a)

    def _send(self, body, ctype="application/json", code=200, name=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        if name:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.request.quote(name)}")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        try:
            if self.path == "/api/models":
                return self._send(models())
            if self.path == "/api/runs":
                return self._send(list_runs())
            m = re.fullmatch(rf"/api/runs/({RUN_RE})", self.path)
            if m:
                return self._send(read(os.path.join(WS, m.group(1), "result.json")).encode())
            m = re.fullmatch(rf"/api/runs/({RUN_RE})/(03_result\.hwpx|02_answer\.md|01_source\.md)", self.path)
            if m:
                with open(os.path.join(WS, m.group(1), m.group(2)), "rb") as f:
                    return self._send(f.read(), "application/octet-stream", name=f"{m.group(1)}_{m.group(2)}")
            self._send(HTML.replace("%MODEL%", json.dumps(MODEL)).encode(), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send({"error": "없음"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        task = req.get("task") or "summary"
        file = None
        if req.get("file_b64"):
            name = re.sub(r"[^\w.\-가-힣 ]", "_", os.path.basename(req.get("file_name") or "upload.bin"))
            os.makedirs(os.path.join(WS, "_upload"), exist_ok=True)
            file = os.path.join(WS, "_upload", name)
            with open(file, "wb") as f:
                f.write(base64.b64decode(req["file_b64"]))
        if not file and not (req.get("question") or "").strip():
            return self._send({"error": "문서나 요청 중 하나는 있어야 합니다"}, code=400)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(ev):
            self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        try:
            emit({"done": process(task, (req.get("question") or "").strip(), file, req.get("model") or MODEL,
                                  req.get("preset") or "보고서", emit)})
        except Exception as e:
            emit({"error": f"{type(e).__name__}: {e}"})
        finally:
            if file:
                os.remove(file)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        task = sys.argv[2] if len(sys.argv) > 2 else "summary"
        file = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
        q = sys.argv[4] if len(sys.argv) > 4 else ""
        preset = sys.argv[5] if len(sys.argv) > 5 else "보고서"
        r = process(task, q, file, MODEL, preset,
                    emit=lambda ev: print(f"[{ev['stage']}] {ev['msg']}", file=sys.stderr) if "stage" in ev else None)
        r.pop("source")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        sys.exit(0 if r["task"] != "draft" or r["valid"] else 2)
    if not os.path.exists(_CLI):
        print("경고: node_modules/kordoc 없음 → npx 로 대체 (느림). 이 폴더에서 `npm install` 권장", file=sys.stderr)
    print(f"kordoc local → http://localhost:{PORT}  (model={MODEL}, llm={LLM_API} {LLM_BASE}, kordoc={' '.join(KORDOC[:2])})")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
