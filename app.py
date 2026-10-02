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

import forms

ROOT = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("WORKSPACE") or os.path.join(ROOT, "_workspace")  # 포털이 AGENT_DATA/<도구> 로 모아 줌
LLM_API = os.environ.get("LLM_API", "ollama")            # ollama | openai (vLLM·LM Studio·llama.cpp·TGI 등 /v1/chat/completions)
LLM_BASE = os.environ.get("LLM_BASE_URL", "http://localhost:8000/v1" if LLM_API == "openai" else "http://localhost:11434").rstrip("/")
OLLAMA = LLM_BASE
MODEL = os.environ.get("LLM_MODEL", "qwen3:8b")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
PORT = int(os.environ.get("PORT", "8766"))
NUM_CTX = int(os.environ.get("NUM_CTX", "16384"))
MAX_CHARS = int(os.environ.get("MAX_CHARS", "12000"))  # ponytail: 앞부분만 잘라 넣음, 긴 문서는 --format chunks + RAG 로 승급
TASKS = ("summary", "qa", "draft", "polish")
PRESETS = ("보고서", "기안문", "간이기안문", "계획서", "통지", "회의록", "개조식", "업무보고", "서울방침", "보도자료")
EXTS = (".hwp", ".hwpx", ".hml", ".pdf", ".docx", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".webp", ".md", ".txt")
_CLI = os.path.join(ROOT, "node_modules", "kordoc", "dist", "cli.js")
KORDOC = ["node", _CLI]  # npx 폴백 없음: 폐쇄망에서 npx 는 무한 대기한다. 없으면 기동 시 안내 후 종료


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def kordoc(*args, cwd=None):
    r = subprocess.run([*KORDOC, *args], capture_output=True, text=True, cwd=cwd or ROOT, timeout=600)
    return r.returncode, (r.stdout + r.stderr).strip()


def lint(path, *opts):
    """kordoc lint --json → dict (파싱 실패 시 {"raw": ...})."""
    _, out = kordoc("lint", "--json", *opts, path)
    try:
        return json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {"raw": out}


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
def parse(src, d, log, *opts):
    """문서 → Markdown. md/txt 는 그대로."""
    if src.lower().endswith((".md", ".txt")):
        return read(src)
    out = os.path.join(d, "01_source.md")
    code, msg = kordoc("--silent", *opts, src, "-o", out, cwd=d)
    log.append(f"[kordoc parse] exit {code} {msg}"[:2000])
    if code != 0 or not os.path.exists(out):
        raise RuntimeError(f"kordoc 파싱 실패: {msg[-500:]}")
    return read(out)


# ── 윤문 (polish) ────────────────────────────────────────────────────────
# 문단·목록·표 셀을 조각으로 떼어 번호를 붙여 LLM 에 보내고, 같은 자리에 되돌려 넣는다.
# HWPX/HWP 는 kordoc patch 로 원본 서식 그대로 반영, 그 밖의 형식은 프리셋으로 새 HWPX 를 만든다.
PATCHABLE = (".hwpx", ".hwp")
UNIT_MIN = int(os.environ.get("POLISH_MIN_CHARS", "10"))   # 이보다 짧은 조각(제목·단어 셀)은 건드리지 않음
BATCH_CHARS = int(os.environ.get("POLISH_BATCH_CHARS", "2500"))
LEAD = re.compile(r"^(\s*(?:[-*+]|\d+[.)]|[가-하][.)]|\(\d+\)|[□■○●◦ㅇ▪※∙·\u2460-\u2473>])\s*)")
CELL = re.compile(r"(?<=\|)([^|\n]*?(?:\\\|[^|\n]*?)*)(?=\|)")
KEEP = re.compile(r"\d+(?:[.,:]\d+)*|「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"|[A-Za-z][A-Za-z0-9&.+\-]*|○{2,}|\*\*|</?\w+>|[①-⑳※□○:]")  # ':' — '제목  ○○' 같은 서식 항목명에 쌍점을 붙이지 않게
STRENGTH = {"light": "강도: 가볍게. 맞춤법·띄어쓰기·이중 피동·명백한 비문만 고치고 나머지는 그대로 둔다.",
            "standard": "강도: 보통. 위 '고칠 것'을 모두 적용해 읽기 쉽게 다듬되 문장 구조는 크게 바꾸지 않는다.",
            "strong": "강도: 적극. 뜻을 지키는 한에서 문장을 간결하게 다시 쓴다(길이 30%까지 줄여도 됨)."}


def units_of(md):
    """윤문 대상 조각 [(줄 번호, (시작, 끝), 원문)] — 제목·그림·수식·HTML·짧은 조각은 제외"""
    out, fence = [], False
    for i, line in enumerate(md.split("\n")):
        s = line.strip()
        if s.startswith(("```", "$$")): fence = not fence; continue
        if fence or not s or s.startswith(("#", "![", "<", "|-", "| -", "| :")) or re.fullmatch(r"[|\s:\-]+", s): continue
        if s.startswith("|"):
            for m in CELL.finditer(line):
                t = m.group(1).strip()
                if len(t) >= UNIT_MIN and not t.startswith("<"):
                    st = m.start(1) + (len(m.group(1)) - len(m.group(1).lstrip()))
                    out.append((i, (st, st + len(t)), t))
            continue
        lead = LEAD.match(line); st = lead.end() if lead else len(line) - len(line.lstrip())
        t = line[st:].rstrip()
        if len(t) >= UNIT_MIN: out.append((i, (st, st + len(t)), t))
    return out


def guard(a, b):
    """윤문 결과를 받아도 되는지. 거부 사유 문자열 또는 None"""
    if not b: return "빈 응답"
    if "\n" in b or "|" in b and "|" not in a: return "구조 변경"
    ka, kb = KEEP.findall(a), KEEP.findall(b)
    if sorted(ka) != sorted(kb):
        lost = [x for x in ka if x not in kb][:3]; new = [x for x in kb if x not in ka][:3]
        return "숫자·고유 표기 변경" + (f" (사라짐: {', '.join(lost)})" if lost else "") + (f" (생김: {', '.join(new)})" if new else "")
    r = len(b) / max(1, len(a))
    if not 0.55 <= r <= 1.5: return f"길이 변화 과다 ({r:.0%})"
    return None


def parse_to(path):
    """결과 문서를 다시 파싱한 마크다운 (반영 확인용)"""
    out = path + ".md"
    kordoc("--silent", "--keep-layout-tables", path, "-o", out)
    try: return read(out)
    finally:
        if os.path.exists(out): os.remove(out)


def polish_batch(units, model, strength, system):
    body = "\n".join(f"[{n}] {t}" for n, t in units)
    out = _clean(ollama(system + "\n\n" + STRENGTH.get(strength, STRENGTH["standard"]), f"[조각 시작]\n{body}\n[조각 끝]", model))
    got = {}
    for m in re.finditer(r"^\s*\[(\d+)\]\s?(.*)$", out, flags=re.M):
        got[int(m.group(1))] = m.group(2).strip()
    return got


def polish(src_file, d, model, strength, preset, log, emit):
    from concurrent.futures import ThreadPoolExecutor
    import difflib
    ext = os.path.splitext(src_file)[1].lower()
    patchable = ext in PATCHABLE
    source = parse(src_file, d, log, *(["--keep-layout-tables"] if patchable else []))
    lines = source.split("\n")
    us = units_of(source)
    emit({"stage": "split", "msg": f"윤문 대상 {len(us)}조각 (문단·목록·표 셀)"})
    batches, cur, size = [], [], 0
    for n, (_, _, t) in enumerate(us):
        if cur and size + len(t) > BATCH_CHARS: batches.append(cur); cur, size = [], 0
        cur.append((n, t)); size += len(t)
    if cur: batches.append(cur)
    common, roles = prompts(); system = common + "\n\n" + roles["polish"]
    got, done = {}, [0]

    def run(b):
        try:
            got.update(polish_batch(b, model, strength, system))
        except Exception as e:
            log.append(f"[polish] 묶음 실패: {type(e).__name__}: {e}")
        done[0] += 1
        emit({"stage": "llm", "msg": f"윤문 {done[0]}/{len(batches)} 묶음 ({model})"})

    with ThreadPoolExecutor(2) as ex: list(ex.map(run, batches))
    changes, rejected = [], []
    for n, (i, (st, en), t) in reversed(list(enumerate(us))):  # 같은 줄 안 여러 셀: 뒤에서부터 바꿔야 위치가 안 밀린다
        new = got.get(n)
        if new is None or new == t: continue
        why = guard(t, new)
        if why: rejected.append({"n": n, "before": t, "after": new, "reason": why}); continue
        lines[i] = lines[i][:st] + new + lines[i][en:]
        changes.append({"n": n, "before": t, "after": new, "ratio": round(1 - difflib.SequenceMatcher(None, t, new).ratio(), 2)})
    changes.reverse(); rejected.reverse()
    polished = "\n".join(lines)
    write(os.path.join(d, "02_polished.md"), polished)
    emit({"stage": "lint", "msg": "kordoc lint (표기법 검수)"})
    lint_r = lint(os.path.join(d, "02_polished.md"))
    res = {"source": source, "output": polished, "changes": changes, "rejected": rejected, "units": len(us), "lint": lint_r,
           "strength": strength, "hwpx": None, "valid": None, "mode": "patch" if patchable else "generate"}
    if patchable:
        name, orig = "03_result" + ext, os.path.join(d, os.path.basename(src_file))
        out_path = os.path.join(d, name)
        emit({"stage": "patch", "msg": f"kordoc patch — 원본 서식 그대로 {name}"})
        code, out = kordoc("patch", orig, os.path.join(d, "02_polished.md"), "-o", out_path)
        log.append(f"[kordoc patch] exit {code} {out}")
        if code not in (0, 2) or not os.path.exists(out_path):  # 패치 자체가 실패하면 원본에서 텍스트 대조로만 반영
            shutil.copy(orig, out_path)
        norm = lambda s: re.sub(r"\s+", " ", re.sub(r"\\([\\`*_{}\[\]()#+\-.!~|>])", r"\1", s)).strip()  # 마크다운 이스케이프(\~ 등) 무시
        back = lambda: norm(parse_to(out_path))
        cur = back()
        pending = [c for c in changes if norm(c["after"]) not in cur]
        if pending and ext == ".hwpx":
            emit({"stage": "patch", "msg": f"위치 매핑이 잠긴 {len(pending)}조각 → 문단 텍스트 대조로 반영"})
            ej = os.path.join(d, "02_fallback_edits.json")
            write(ej, json.dumps([{"before": c["before"], "after": c["after"]} for c in pending], ensure_ascii=False))
            r = subprocess.run(["node", os.path.join(ROOT, "textpatch.mjs"), out_path, ej, out_path], capture_output=True, text=True, cwd=ROOT, timeout=600)
            log.append(f"[textpatch] exit {r.returncode} {(r.stdout + r.stderr)[-1500:]}")
            cur = back()
        for c in changes: c["applied"] = norm(c["after"]) in cur
        res["applied"] = sum(c["applied"] for c in changes)
        res["hwpx"] = name
        if ext == ".hwpx":
            vcode, vout = kordoc("validate", out_path); log.append(f"[kordoc validate] exit {vcode} {vout}")
            res["valid"] = vcode == 0
        else:
            res["valid"] = res["applied"] == len(changes)
    else:
        emit({"stage": "generate", "msg": f"원본이 {ext} 라 서식 보존 불가 → kordoc generate --preset {preset}"})
        code, out = kordoc("generate", os.path.join(d, "02_polished.md"), "-o", os.path.join(d, "03_result.hwpx"), "--preset", preset)
        log.append(f"[kordoc generate] exit {code} {out}")
        if code == 0:
            vcode, vout = kordoc("validate", os.path.join(d, "03_result.hwpx")); log.append(f"[kordoc validate] exit {vcode} {vout}")
            res.update(hwpx="03_result.hwpx", valid=vcode == 0, preset=preset)
    return res


# ── 기안문 (표준 서식 채우기) ────────────────────────────────────────────
# 기안문·간이기안문은 kordoc generate 프리셋 대신 kordoc 내장 표준 서식(행정 효율과 협업 촉진에 관한 규정 시행규칙 별지 제1·2호)을
# 채운다. LLM 은 칸 값을 JSON 으로 쓰고, 비운 칸은 gian_defaults.json(기관명·주소·결재라인 등)으로 메운다.
GIAN = {"기안문": ("gian", "일반기안문_서식.hwpx"), "간이기안문": ("gian_simple", "간이기안문_서식.hwpx")}
TEMPLATES = os.path.join(ROOT, "node_modules", "kordoc", "templates")


def gian_defaults():
    """(값, 고정 키). 고정 키는 문서 내용과 상관없이 덮어쓰고, 나머지는 LLM 이 비운 칸만 채운다."""
    try:
        raw = json.loads(read(os.path.join(ROOT, "gian_defaults.json")))
    except FileNotFoundError:
        return {}, set()
    return {k: v for k, v in raw.items() if not k.startswith("_")}, set(raw.get("_고정") or [])


def gian_values(preset, answer):
    """LLM JSON → 서식 칸 값. 붙임·끝. 처리: 붙임이 있으면 '붙임  ○○ 1부.  끝.', 없으면 본문 끝에 '  끝.'"""
    m = re.search(r"\{.*\}", answer, re.S)
    raw = json.loads(m.group(0)) if m else {}
    v = {k: [str(i).strip() for i in x if str(i).strip()] if isinstance(x, list) else str(x or "").strip() for k, x in raw.items()}
    dv, fixed = gian_defaults()
    for k, x in dv.items():
        if k in fixed or not v.get(k): v[k] = x
    org = dv.get("행정기관명")
    if preset == "간이기안문" and org and v.get("작성기관") and not v["작성기관"].startswith(org):
        v["작성기관"] = f"{org} {v['작성기관']}"  # 부서만 썼으면 기관명을 앞에
    for k in ("본문", "요약설명"):  # 표준 서식은 항목 사이 빈 줄 없음
        if v.get(k): v[k] = re.sub(r"\n\s*\n+", "\n", v[k]).strip("\n")
    att = []
    if preset == "기안문":
        att = v.pop("붙임", []) or []
        if isinstance(att, str): att = [att]
        att = [re.sub(r"\s*\d+\s*부\.?\s*(끝\.?)?\s*$", "", a) for a in att]
        body = re.sub(r"\s*끝\.?\s*$", "", v.get("본문", "")).rstrip()
        if att:
            v["붙임"] = (f"{att[0]} 1부." if len(att) == 1 else "\n".join(f"{i}. {a} 1부." for i, a in enumerate(att, 1))) + "  끝."
            v["본문"] = body
        else:
            v["붙임"], v["본문"] = "", body + "  끝."
    elif not v.get("작성일"):
        t = datetime.date.today(); v["작성일"] = f"{t.year}. {t.month}. {t.day}."
    return v, att


def gian_template(preset, has_att, dst):
    """내장 서식 사본. 일반기안문의 고정 글자 '붙임  [칸]  1부.  끝.' 을 비워 붙임 줄을 값으로 직접 만든다."""
    import zipfile
    with zipfile.ZipFile(os.path.join(TEMPLATES, GIAN[preset][1])) as zi, zipfile.ZipFile(dst, "w") as zo:
        for info in zi.infolist():
            data = zi.read(info)
            if preset == "기안문" and info.filename == "Contents/section0.xml":
                x = data.decode("utf-8")
                x = x.replace("<hp:t>  1부.  끝.</hp:t>", "<hp:t></hp:t>", 1)
                if not has_att: x = x.replace("<hp:t>붙임  </hp:t>", "<hp:t></hp:t>", 1)
                data = x.encode("utf-8")
            zo.writestr(info, data)  # ZipInfo 그대로 → mimetype 무압축·순서 유지


MARK = re.compile(r"^(\d+\.|[가-하]\.|\d+\)|[가-하]\)|\(\d+\)|\([가-하]\)|[-·○□ㅇ※])\s+")


def _em(s):
    """대략 글자 폭(em): 한글·전각 1, 그 밖(숫자·영문·기호·공백) 0.5"""
    return sum(1 if ord(ch) > 0x2E7F else 0.5 for ch in s)


def gian_indent(hwpx, field):
    """누름틀 `field` 가 든 문단(줄바꿈으로 이은 본문)을 항목마다 문단으로 나누고 단계별 내어쓰기를 입힌다.
       줄 앞 공백 2칸 = 한 단계(2em). 항목 기호 뒤 글자에 둘째 줄이 맞춰진다. 한글이 다시 조판하므로 줄 배치 캐시는 넣지 않는다."""
    import zipfile, html as H
    with zipfile.ZipFile(hwpx) as z:
        infos, files = z.infolist(), {i.filename: z.read(i) for i in z.infolist()}
    sec = files["Contents/section0.xml"].decode("utf-8"); hdr = files["Contents/header.xml"].decode("utf-8")
    i = sec.find(f'name="{field}"')
    if i < 0: return False
    ps, pe = sec.rfind("<hp:p ", 0, i), sec.find("</hp:p>", i) + len("</hp:p>")
    para = sec[ps:pe]
    tm = re.search(r"<hp:t>(.*?)</hp:t>", para, re.S)
    if not tm or "<hp:lineBreak/>" not in tm.group(1): return False
    lines = [H.unescape(re.sub(r"<[^>]+>", "", x)) for x in tm.group(1).split("<hp:lineBreak/>")]
    pid = re.search(r'paraPrIDRef="(\d+)"', para).group(1); cid = re.search(r'charPrIDRef="(\d+)"', para).group(1)
    em = int(re.search(rf'<hh:charPr id="{cid}" height="(\d+)"', hdr).group(1))
    base = re.search(rf'<hh:paraPr id="{pid}".*?</hh:paraPr>', hdr, re.S).group(0)
    ids = [int(x) for x in re.findall(r'<hh:paraPr id="(\d+)"', hdr)]
    made, new_pr, out = {}, [], []
    for ln in lines:
        if not ln.strip(): continue
        lvl = (len(ln) - len(ln.lstrip(" "))) // 2; t = ln.strip()
        m = MARK.match(t); hang = round(_em(m.group(0)) * em) if m else 0
        key = (lvl, hang)
        if key not in made:
            nid = max(ids) + 1; ids.append(nid); made[key] = nid
            left, intent = lvl * 2 * em, -hang  # 한글 내어쓰기: 첫 줄은 left, 둘째 줄부터 left + |intent|
            pr = re.sub(r'<hh:paraPr id="\d+"', f'<hh:paraPr id="{nid}"', base, 1)
            pr = re.sub(r'(<hh:align horizontal=")\w+', r'\1JUSTIFY', pr)  # 가운데 정렬 칸(간이기안문 요약설명)도 항목은 양쪽 정렬
            pr = re.sub(r'<hc:intent value="-?\d+"', f'<hc:intent value="{intent}"', pr)
            pr = re.sub(r'<hc:left value="-?\d+"', f'<hc:left value="{left}"', pr)
            new_pr.append(pr)
        out.append(f'<hp:p id="0" paraPrIDRef="{made[key]}" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">'
                   f'<hp:run charPrIDRef="{cid}"><hp:t>{H.escape(t, quote=False)}</hp:t></hp:run></hp:p>')
    hdr = hdr.replace("</hh:paraProperties>", "".join(new_pr) + "</hh:paraProperties>", 1)
    hdr = re.sub(r'<hh:paraProperties itemCnt="\d+"', f'<hh:paraProperties itemCnt="{len(ids)}"', hdr, 1)
    files["Contents/section0.xml"] = (sec[:ps] + "".join(out) + sec[pe:]).encode("utf-8")
    files["Contents/header.xml"] = hdr.encode("utf-8")
    tmp = hwpx + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        for info in infos: z.writestr(info, files[info.filename])
    os.replace(tmp, hwpx)
    return True


def form_build(form, answer, d, log, emit):
    """연구원 양식 채우기 — kind=fill 은 칸 값, kind=body 는 앞부분 + 본문 줄"""
    meta, path = form
    try:
        v = forms.parse_json(answer)
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"LLM 이 양식 값을 JSON 으로 쓰지 못했습니다: {e}")
    write(os.path.join(d, "02_fields.json"), json.dumps(v, ensure_ascii=False, indent=1))
    hwpx = os.path.join(d, "03_result.hwpx")
    if meta["kind"] == "fill":
        emit({"stage": "fill", "msg": f"양식 칸 채우기 — {meta['name']}"})
        info = forms.fill(meta, path, v, hwpx, kordoc, d)
        log.append(f"[form fill] {info['filled']}칸 {info['log'][-300:]}")
        body = "\n".join(f"{k}: {x}" for k, x in v.items() if str(x).strip())
    else:
        emit({"stage": "build", "msg": f"양식 견본으로 본문 짜기 — {meta['name']}"})
        lines = v.get("body") or []
        if isinstance(lines, str): lines = lines.split("\n")
        info = forms.build_body(path, hwpx, {str(k): x for k, x in (v.get("front") or {}).items()}, lines)
        log.append(f"[form body] 앞부분 {info['front']}칸, 본문 {info['lines']}줄")
        body = "\n".join(map(str, lines))
    write(os.path.join(d, "02_body.md"), body)
    _, out = kordoc("lint", "--json", os.path.join(d, "02_body.md"))
    try: lint = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError): lint = {"raw": out}
    vcode, vout = kordoc("validate", hwpx); log.append(f"[kordoc validate] exit {vcode} {vout}")
    return {"lint": lint, "fields": v, "hwpx": "03_result.hwpx", "valid": vcode == 0, "preset": meta["name"], "form": meta["id"],
            "output": parse_to(hwpx).strip()}


def gian_build(preset, answer, d, log, emit):
    try:
        v, att = gian_values(preset, answer)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM 이 서식 칸 값을 JSON 으로 쓰지 못했습니다: {e}")
    write(os.path.join(d, "02_fields.json"), json.dumps(v, ensure_ascii=False, indent=1))
    body = v.get("본문") or v.get("요약설명") or ""
    write(os.path.join(d, "02_body.md"), body)
    emit({"stage": "lint", "msg": "kordoc lint (본문 표기법 검수)"})
    lint_r = lint(os.path.join(d, "02_body.md"), *(["--munche"] if preset == "간이기안문" else []))
    emit({"stage": "fill", "msg": f"kordoc fill — 표준 {preset} 서식"})
    tpl, hwpx = os.path.join(d, "02_template.hwpx"), os.path.join(d, "03_result.hwpx")
    gian_template(preset, bool(att), tpl)
    code, out = kordoc("fill", tpl, "-j", os.path.join(d, "02_fields.json"), "-o", hwpx)
    log.append(f"[kordoc fill] exit {code} {out}")
    res = {"lint": lint_r, "fields": v, "hwpx": None, "valid": None, "preset": preset}
    if code == 0 and os.path.exists(hwpx):
        field = "본문" if preset == "기안문" else "요약설명"
        try:
            log.append(f"[indent] {field} 항목별 문단·내어쓰기 {'적용' if gian_indent(hwpx, field) else '해당 없음'}")
        except Exception as e:  # 실패해도 줄바꿈 본문으로 남긴다
            log.append(f"[indent] 건너뜀: {type(e).__name__}: {e}")
        vcode, vout = kordoc("validate", hwpx); log.append(f"[kordoc validate] exit {vcode} {vout}")
        res.update(hwpx="03_result.hwpx", valid=vcode == 0, output=parse_to(hwpx).strip())
    return res


def process(task, question="", file=None, model=MODEL, preset="보고서", emit=lambda ev: None, strength="standard"):
    if task not in TASKS:
        raise ValueError(f"task 는 {TASKS} 중 하나")
    if preset not in PRESETS and not str(preset).startswith("form:"):
        preset = "보고서"
    if file and not file.lower().endswith(EXTS):
        raise ValueError(f"지원하지 않는 확장자: {os.path.basename(file)} (지원: {', '.join(EXTS)})")
    run_id = f"{datetime.date.today()}-{secrets.token_hex(2)}"
    d = os.path.join(WS, run_id)
    os.makedirs(d)
    log, source, truncated = [], "", False
    if task == "polish":
        if not file: raise ValueError("윤문할 문서를 올려 주세요")
        src = os.path.join(d, "00_" + os.path.basename(file)); shutil.copy(file, src)
        emit({"stage": "parse", "msg": f"kordoc 파싱 {os.path.basename(file)}"})
        result = {"run_id": run_id, "task": task, "model": model, "question": question, "file": os.path.basename(file), "truncated": False,
                  "ts": datetime.datetime.now().isoformat(timespec="seconds"), **polish(src, d, model, strength, preset, log, emit)}
        result["log"] = "\n".join(log)
        write(os.path.join(d, "result.json"), json.dumps(result, ensure_ascii=False, indent=1))
        return result

    if file:
        name = os.path.basename(file)
        src = os.path.join(d, "00_" + name)
        shutil.copy(file, src)
        emit({"stage": "parse", "msg": f"kordoc 파싱 {name}"})
        source = parse(src, d, log)
        if len(source) > MAX_CHARS:
            source, truncated = source[:MAX_CHARS], True
            log.append(f"[truncate] {MAX_CHARS}자로 잘림 (MAX_CHARS 환경변수로 조정)")

    common, roles = prompts()
    gian = task == "draft" and preset in GIAN
    form = forms.get(preset[5:]) if task == "draft" and str(preset).startswith("form:") else None  # (meta, path)
    role = ("form_" + form[0]["kind"]) if form else GIAN[preset][0] if gian else task
    system = common + "\n\n" + roles[role]
    user = (f"[문서 시작]\n{source}\n[문서 끝]" + ("\n(※ 문서가 길어 앞부분만 제공됨)" if truncated else "")
            if source else "[문서 없음]") + f"\n\n[요청]\n{question or {'summary': '요약해줘', 'qa': '핵심 내용은?', 'draft': '문서 내용으로 초안 작성'}[task]}"
    if form:
        t = datetime.date.today()
        user = f"[양식] {form[0]['name']}\n{forms.prompt_spec(form[0])}\n\n[오늘] {t.year}. {t.month}. {t.day}.\n\n" + user
    write(os.path.join(d, "01_prompt.txt"), user)

    emit({"stage": "llm", "msg": f"Ollama {model} ({task})", "reset": True})
    answer = _clean(ollama(system, user, model, on_token=lambda t: emit({"token": t})))
    write(os.path.join(d, "02_answer.md"), answer)

    result = {"run_id": run_id, "task": task, "model": model, "question": question, "file": os.path.basename(file) if file else None,
              "source": source, "truncated": truncated, "output": answer, "hwpx": None, "lint": None, "valid": None,
              "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    if form:
        result.update(form_build(form, answer, d, log, emit))
    elif gian:
        result.update(gian_build(preset, answer, d, log, emit))
    elif task == "draft":
        emit({"stage": "lint", "msg": "kordoc lint (표기법 검수)"})
        result["lint"] = lint(os.path.join(d, "02_answer.md"), "--munche")  # --munche: 개조식 문체(당위·서술형 종결)까지
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


def result_name(j, path):
    """03_result 를 내려받을 때 이름"""
    stem, ext = os.path.splitext(j.get("file") or "")[0], os.path.splitext(path)[1]
    tag = "윤문" if j.get("task") == "polish" else j.get("preset") or "초안"
    return (f"{stem}_{tag}" if stem else tag) + ext


def polish_source(run_id):
    """이전 실행에서 윤문할 문서를 고른다 — 03_result(초안·윤문 결과) 우선, 없으면 00_원본. 알아볼 수 있는 이름으로 _upload 에 복사"""
    if not re.fullmatch(RUN_RE, run_id or ""): raise ValueError("잘못된 실행 ID")
    d = os.path.join(WS, run_id); j = json.loads(read(os.path.join(d, "result.json")))
    if j.get("hwpx") and os.path.exists(os.path.join(d, j["hwpx"])):
        src = os.path.join(d, j["hwpx"])
        name = (os.path.splitext(j["file"])[0] + os.path.splitext(src)[1]) if j["task"] == "polish" else result_name(j, src)  # 윤문본을 다시 윤문하면 이름 유지
    else:
        cand = [f for f in os.listdir(d) if f.startswith("00_")]
        if not cand: raise ValueError("이 실행에는 윤문할 문서가 없습니다")
        src, name = os.path.join(d, cand[0]), cand[0][3:]
    os.makedirs(os.path.join(WS, "_upload"), exist_ok=True)
    dst = os.path.join(WS, "_upload", re.sub(r"[^\w.\-가-힣 ]", "_", name))
    shutil.copy(src, dst)
    return dst


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

# ── 저작권 표기 (LICENSE·NOTICE 참고) ─────────────────────────────────────
_SIG = __import__("base64").b64decode("wqkgMjAyNiDquYDrj5nso7wgwrcgZG9uZ2p1a2ltLmRldkBnbWFpbC5jb20=").decode()
_SIG_A = __import__("base64").b64decode("RG9uZ0p1IEtpbSA8ZG9uZ2p1a2ltLmRldkBnbWFpbC5jb20+").decode()


def signed(html):
    """화면에 저작권 표기를 붙인다. ui.html 에서 지워져도 서버가 내보낼 때 다시 붙는다."""
    name, mail = _SIG.split(" · ")
    if 'name="author"' not in html:
        meta = f'<meta name="author" content="{name[7:]} <{mail}>">'
        html = html.replace("<head>", "<head>" + meta, 1) if "<head>" in html else meta + html
    if "data-sig" not in html:
        tag = (f'<!-- {_SIG} --><div data-sig title="{mail}" style="text-align:center;font-size:11px;color:#9aa0a6;'
               f'opacity:.55;margin:28px 0 8px">{name}</div>')
        html = html.replace("</body>", tag + "</body>", 1) if "</body>" in html else html + tag
    return html


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        if "/api/run" in (a[0] if a else ""):
            super().log_message(fmt, *a)

    def _send(self, body, ctype="application/json", code=200, name=None):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("X-Author", _SIG_A)
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
            if self.path == "/api/forms":
                return self._send(forms.listing())
            if self.path == "/api/runs":
                return self._send(list_runs())
            m = re.fullmatch(rf"/api/runs/({RUN_RE})", self.path)
            if m:
                return self._send(read(os.path.join(WS, m.group(1), "result.json")).encode())
            m = re.fullmatch(rf"/api/runs/({RUN_RE})/(03_result\.hwpx|03_result\.hwp|02_answer\.md|02_polished\.md|01_source\.md)", self.path)
            if m:
                name = f"{m.group(1)}_{m.group(2)}"
                if m.group(2).startswith("03_result"):  # 원본이름_윤문.hwpx / 원본이름_보고서.hwpx / 보고서.hwpx
                    name = result_name(json.loads(read(os.path.join(WS, m.group(1), "result.json"))), m.group(2))
                with open(os.path.join(WS, m.group(1), m.group(2)), "rb") as f:
                    return self._send(f.read(), "application/octet-stream", name=name)
            self._send(signed(HTML.replace("%MODEL%", json.dumps(MODEL))).encode(), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send({"error": "없음"}, code=404)
        except Exception as e:
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/api/forms"):  # 양식 등록·삭제·종류 변경
            try:
                if self.path == "/api/forms":
                    name = req.get("file_name") or ""
                    if not name.lower().endswith(".hwpx"):
                        raise ValueError(f"양식은 HWPX 파일만 받습니다({os.path.splitext(name)[1] or '확장자 없음'}). " + forms.HOWTO)
                    return self._send(forms.register((req.get("name") or os.path.splitext(name)[0]).strip()[:60], base64.b64decode(req["file_b64"]), kordoc))
                if self.path == "/api/forms/delete": return self._send(forms.remove(req.get("id")))
                if self.path == "/api/forms/kind": return self._send(forms.set_kind(req.get("id"), req.get("kind")))
            except (ValueError, FileNotFoundError, KeyError) as e:
                return self._send({"error": str(e)}, code=400)
            return self._send({"error": "no route"}, code=404)
        task = req.get("task") or "summary"
        file = None
        if req.get("file_b64"):
            name = re.sub(r"[^\w.\-가-힣 ]", "_", os.path.basename(req.get("file_name") or "upload.bin"))
            os.makedirs(os.path.join(WS, "_upload"), exist_ok=True)
            file = os.path.join(WS, "_upload", name)
            with open(file, "wb") as f:
                f.write(base64.b64decode(req["file_b64"]))
        if req.get("from_run"):  # 결과 화면의 "윤문하기": 그 실행의 HWPX 결과(없으면 올렸던 원본)를 이어서 윤문
            try:
                file = polish_source(req["from_run"])
            except (ValueError, FileNotFoundError) as e:
                return self._send({"error": str(e)}, code=400)
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
                                  req.get("preset") or "보고서", emit, req.get("strength") or "standard")})
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
        strength = q if task == "polish" and q in STRENGTH else "standard"  # polish: 4번째 인자가 강도
        r = process(task, "" if task == "polish" else q, file, MODEL, preset,
                    emit=lambda ev: print(f"[{ev['stage']}] {ev['msg']}", file=sys.stderr) if "stage" in ev else None, strength=strength)
        r.pop("source")
        print(json.dumps(r, ensure_ascii=False, indent=1))
        sys.exit(0 if r["task"] not in ("draft", "polish") or r["valid"] else 2)
    if not os.path.exists(_CLI):
        sys.exit("node_modules/kordoc 없음 — 이 폴더에서 `npm install` 또는 pack.sh 번들을 쓰세요")
    print(f"kordoc local → http://localhost:{PORT}  (model={MODEL}, llm={LLM_API} {LLM_BASE}, kordoc={' '.join(KORDOC[:2])})  {_SIG}")
    ThreadingHTTPServer(("", PORT), H).serve_forever()
