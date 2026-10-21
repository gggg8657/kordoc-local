#!/usr/bin/env python3
"""LLM 없이 kordoc 왕복만 검증한다: parse(HWPX→MD) → [draft] lint → generate → validate, [polish] 조각 윤문 → patch.
Ollama 호출을 가짜 함수로 바꿔 끼운다.  npm install 후  python3 selftest.py"""
import json
import os
import re
import shutil
import sys
import app

SAMPLE = os.path.join(app.ROOT, "sample", "dummy.hwpx")
DRAFT = ("# 자문 결과 보고\n\n> 서면자문 의견을 검토하고자 함\n\n## Ⅰ. 추진배경\n\n1. 테스트 목적 더미 문서 자문\n"
         "  - 자문위원: 홍길동\n\n## Ⅱ. 자문 의견\n\n1. 사업은 적정하게 추진 중이며 향후 보완 필요\n\n"
         "| 항목 | 내용 |\n| --- | --- |\n| 자문위원 | 홍길동 |\n\n붙임: 의견서 1부. 끝.\n")

calls = []
def fake(system, user, model, on_token=None):
    calls.append((system, user))
    out = FAKE
    if on_token: on_token(out)
    return out
app.ollama = fake

# 1) summary: kordoc 이 실제로 HWPX 를 파싱해 프롬프트에 넣는다
FAKE = "□ 더미 자문 의견서 요약"
r = app.process("summary", "", SAMPLE, "fake")
assert "자문" in r["source"] and "홍길동" in r["source"], r["source"][:200]
assert "[문서 시작]" in calls[-1][1] and "summary" not in calls[-1][0].split("## ")[0]  # 공통부 + summary 역할만
assert r["output"] == FAKE and r["hwpx"] is None

# 2) qa: 문서 없이도 동작
r = app.process("qa", "수도는?", None, "fake")
assert "[문서 없음]" in calls[-1][1] and "수도는?" in calls[-1][1]

# 3) draft: lint 가 '붙임:' 오류를 잡고, HWPX 생성 + 검증 통과, 다시 파싱하면 표가 살아있다
FAKE = "```markdown\n" + DRAFT + "\n```"  # 코드펜스 제거 확인
r = app.process("draft", "보고서 써줘", SAMPLE, "fake", "보고서")
assert r["output"].startswith("# 자문 결과 보고"), r["output"][:60]
assert r["lint"]["summary"]["errors"] >= 1 and any(f["rule"] == "BUNIM_COLON" for f in r["lint"]["findings"]), r["lint"]
assert r["hwpx"] and r["valid"], r["log"]
back = app.read(os.path.join(app.WS, r["run_id"], "01_source.md"))  # 원본 파싱본
_, msg = app.kordoc("--silent", os.path.join(app.WS, r["run_id"], "03_result.hwpx"), "-o", os.path.join(app.WS, r["run_id"], "04_back.md"))
rt = app.read(os.path.join(app.WS, r["run_id"], "04_back.md"))
assert "| 자문위원 | 홍길동 |" in rt and "자문 결과 보고" in rt, rt[:300]

draft_run = r["run_id"]

# 4) 잘못된 확장자 거부
try:
    app.process("summary", "", os.path.join(app.ROOT, "package.json"), "fake"); assert False
except ValueError:
    pass

# 5) polish: 조각을 번호로 돌려받아 원본 서식 그대로 반영. 잠긴 표 셀은 텍스트 대조로, 숫자를 바꾼 제안은 원문 유지
def fake_polish(system, user, model, on_token=None):
    calls.append((system, user))
    return "\n".join(f"[{n}] " + t.replace("되어지고", "되고").replace("42%", "40%").replace("의 확보가", "을 확보하기")
                     for n, t in re.findall(r"^\[(\d+)\] (.*)$", user, flags=re.M))
app.ollama = fake_polish
r = app.process("polish", "", os.path.join(app.ROOT, "sample", "polish_test.hwpx"), "fake")
assert "[조각 시작]" in calls[-1][1] and "polish" not in calls[-1][0].split("## ")[0]
assert r["mode"] == "patch" and r["hwpx"] == "03_result.hwpx" and r["valid"], r["log"]
assert r["changes"] and all(c["applied"] for c in r["changes"]), [c for c in r["changes"] if not c["applied"]]
assert any("42%" in c["before"] for c in r["rejected"]), r["rejected"]
back = app.parse_to(os.path.join(app.WS, r["run_id"], "03_result.hwpx"))
assert "| 수작업 위주로 진행되고 있음 |" in back and "42%" in back and "통일성을 확보하기" in back, back

# 6) 결과 화면의 "윤문하기": 초안 HWPX 결과를 이어서 윤문 (이름은 <원본>_<프리셋>), 원본만 있는 실행은 원본을
src = app.polish_source(draft_run)
assert os.path.basename(src) == "dummy_보고서.hwpx", src
r2 = app.process("polish", "", src, "fake")
assert r2["mode"] == "patch" and r2["valid"] and app.result_name(r2, r2["hwpx"]) == "dummy_보고서_윤문.hwpx", (r2["log"], r2["file"])
_src2 = app.polish_source(r["run_id"])
assert os.path.basename(_src2) == "polish_test.hwpx"  # 윤문본을 다시 → 이름 유지
for _p in (src, _src2):  # 서버는 실행 뒤 _upload 복사본을 지운다 — 시험도 남기지 않음
    os.path.exists(_p) and os.remove(_p)
try:
    app.polish_source("../etc"); assert False
except ValueError:
    pass

# 7) 기안문: 표준 일반기안문 서식 채우기 — 붙임 유무별 '끝.' 처리, 항목별 문단·내어쓰기, gian_defaults 로 빈 칸 채우기
import json
GIAN = {"행정기관명": "다른기관", "발신명의": "", "수신자": "내부결재", "제목": "시범 구축 계획 보고", "본문": "1. 추진 목적\n  가. 업무 효율화\n\n2. 추진 내용\n  가. 매우 긴 항목입니다 " + "가나다라 " * 30 + "\n위와 같이 보고합니다. 끝.",
        "붙임": [], "기안자": "", "공개구분": ""}
app.gian_defaults = lambda: ({"행정기관명": "시험기관", "발신명의": "시험기관장", "기안자": "주무관 ○○○"}, {"행정기관명", "발신명의"})
def fake_gian(system, user, model, on_token=None):
    calls.append((system, user)); return "```json\n" + json.dumps(GIAN, ensure_ascii=False) + "\n```"
app.ollama = fake_gian
r = app.process("draft", "기안문 써줘", None, "fake", "기안문")
assert "일반기안문" in calls[-1][0] and r["hwpx"] and r["valid"], r["log"]
assert r["fields"]["행정기관명"] == "시험기관" and r["fields"]["발신명의"] == "시험기관장" and r["fields"]["기안자"] == "주무관 ○○○" and r["fields"]["본문"].endswith("보고합니다.  끝."), r["fields"]
assert "붙임" not in r["output"].split("보고합니다.")[1].split("수신자")[0], r["output"]  # 붙임 없으면 붙임 줄 비움
assert "[indent] 본문 항목별 문단·내어쓰기 적용" in r["log"] and "\n\n2. 추진 내용" in r["output"], r["output"][:400]
GIAN["붙임"] = ["세부 계획 1부", "명단"]
r = app.process("draft", "기안문 써줘", None, "fake", "기안문")
assert "붙임 1. 세부 계획 1부.\n2. 명단 1부. 끝." in r["output"] and "보고합니다. 끝." not in r["output"], r["output"]
assert app.result_name(r, r["hwpx"]) == "기안문.hwpx"

# 8) 연구원 양식: HWPX 만 등록, 본문 작성형은 견본 복제(장 막대·□·ㅇ·표), 칸 채우기형은 라벨 칸 채우기
import tempfile, shutil as _sh
app.forms.DIR = tempfile.mkdtemp()
m = app.forms.register("계획서 견본", open(os.path.join(app.ROOT, "sample", "polish_test.hwpx"), "rb").read(), app.kordoc)
assert m["kind"] == "fill" and m["roles"].get("chapter") and m["roles"].get("l1") and m["roles"].get("table"), m
app.forms.set_kind(m["id"], "body")  # Explicit consent to replace body template paragraphs.
BODY = {"front": {str(m["front"][0]["i"]): "시험 계획"}, "body": ["Ⅰ. 첫 장", "□ 항목 하나", "ㅇ 세부 하나", "<표 1. 일정>", "| 구분 | 기간 | 내용 |", "|---|---|---|", "| 1단계 | 10월 | 설계 |", "| 2단계 | 11월 | 개발 |", "Ⅱ. 둘째 장", "□ 항목 둘"]}
def fake_form(system, user, model, on_token=None):
    calls.append((system, user)); return json.dumps(BODY, ensure_ascii=False)
app.ollama = fake_form
r = app.process("draft", "계획서 써줘", None, "fake", "form:" + m["id"])
assert "[양식 앞부분" in calls[-1][1] and r["valid"] and r["preset"] == "계획서 견본", r["log"]
out = r["output"]
assert "시험 계획" in out and "첫 장" in out and "□ 항목 하나" in out and "| 1단계 | 10월 | 설계 |" in out and "둘째 장" in out, out
assert "추진배경" not in out and "최근 생성형" not in out, out  # 견본 본문은 버린다
g = app.forms.register("기안문 서식", open(os.path.join(app.TEMPLATES, "일반기안문_서식.hwpx"), "rb").read(), app.kordoc)
assert g["kind"] == "fill" and any(f["label"] == "제목" for f in g["fields"]), g
FILL = {"제목": "시험 제목", "수신자": "내부결재"}
app.ollama = lambda system, user, model, on_token=None: json.dumps(FILL, ensure_ascii=False)
r = app.process("draft", "기안 써줘", None, "fake", "form:" + g["id"])
assert r["valid"] and "시험 제목" in r["output"], r["output"][:300]
assert app.forms.remove(g["id"])["ok"] and len(app.forms.listing()) == 1
# 양식 권한: 만든 계정·owner 만 지우기·종류 바꾸기, 응답에 계정 없음
h = app.forms.register("권한 시험", open(os.path.join(app.ROOT, "sample", "polish_test.hwpx"), "rb").read(), app.kordoc, "alice")
assert "_by" not in h and h["can_edit"] and not next(x for x in app.forms.listing("bob") if x["id"] == h["id"])["can_edit"]
for fn in (lambda: app.forms.remove(h["id"], "bob"), lambda: app.forms.set_kind(h["id"], "fill", "bob"), lambda: app.forms.remove(m["id"], "alice")):
    try:
        fn(); raise AssertionError("남의 양식을 바꾸면 안 됨")
    except PermissionError:
        pass
assert app.forms.remove(h["id"], "alice")["ok"]
_bad = app.forms.analyze
app.forms.analyze = lambda *a: (_ for _ in ()).throw(RuntimeError("분석 실패"))
_n = len(os.listdir(app.forms.DIR))
try:
    app.forms.register("실패", open(os.path.join(app.ROOT, "sample", "polish_test.hwpx"), "rb").read(), app.kordoc, "alice"); raise AssertionError
except RuntimeError:
    pass
app.forms.analyze = _bad
assert len(os.listdir(app.forms.DIR)) == _n, "분석 실패한 양식 파일이 남음"
_sh.rmtree(app.forms.DIR)

# 9) 글 윤문 API(다른 도구의 "윤문하기"): 파일 없이 글만, 숫자를 바꾼 제안은 원문 유지
app.ollama = fake_polish
r = app.polish_text("ㅇ 수작업 위주로 진행되어지고 있음\nㅇ 약 42%가 검토 중임")
assert "진행되고 있음" in r["output"] and "42%" in r["output"] and r["rejected"] and r["units"] == 2, r

# 브라우저가 떠나도(첫 진행 이벤트 뒤 쓰기가 BrokenPipeError) 작업은 끝까지 — 결과는 실행 기록에 남고, 서버 오류·트레이스백 없음
import io, threading, urllib.request
from http.server import ThreadingHTTPServer


class Cut:
    def __init__(s, f, n):
        s.f, s.n = f, n

    def write(s, b):
        s.n -= 1
        if s.n < 0:
            raise BrokenPipeError(32, "Broken pipe")
        return s.f.write(b)

    def flush(s):
        pass

    def __getattr__(s, k):
        return getattr(s.f, k)


class CutH(app.H):
    def setup(s):
        super().setup()
        s.wfile = Cut(s.wfile, 2)  # 응답 머리 + 첫 이벤트까지만 닿음


ERRS = []


class Srv(ThreadingHTTPServer):
    def handle_error(s, req, addr):
        ERRS.append(sys.exc_info()[1])


def cut_post(path, body, user="dc-test"):
    cut = Srv(("127.0.0.1", 0), CutH)
    threading.Thread(target=cut.serve_forever, daemon=True).start()
    err0, sys.stderr = sys.stderr, io.StringIO()
    try:
        r = urllib.request.Request(f"http://127.0.0.1:{cut.server_address[1]}{path}", json.dumps(body).encode(),
                                   {"Content-Type": "application/json", "X-Agent-User": user})
        got, log = urllib.request.urlopen(r, timeout=60).read().decode(), sys.stderr.getvalue()
    finally:
        sys.stderr = err0
        cut.shutdown()
        cut.server_close()
    assert got.count("data: ") == 1 and not ERRS and "Traceback" not in log and log.count("연결 끊김") == 1, (got, ERRS, log)
    return os.path.join(app.WS, "u", user)
app.ollama, FAKE = fake, "□ 떠난 뒤에도 끝까지 쓴 답"
d = cut_post("/api/run", {"task": "qa", "question": "수도는?", "model": "fake"})
try:
    runs = app.list_runs(d)
    assert [x["task"] for x in runs] == ["qa"] and app.read(os.path.join(d, runs[0]["run_id"], "result.json")).count("끝까지 쓴 답"), runs
finally:
    shutil.rmtree(d, ignore_errors=True)

# 저작권 표기: ui.html 에서 지워도 서버가 다시 붙인다 (LICENSE·NOTICE)
import base64 as _b
_h = app.signed(app.HTML.replace("data-sig", "").replace('name="author"', ""))
assert "data-sig" in _h and 'name="author"' in _h and _b.b64decode("ZG9uZ2p1a2ltLmRldkBnbWFpbC5jb20=").decode() in _h, "저작권 표기 누락"

# 10) 관리자 계정 분리(포털 관문 그대로): 관리자 두 계정은 각자 u/<id>, 둘 다 양식 관리 권한. 서명 없는/위장 역할은 권한 없음
import json as _j, tempfile as _tf, threading as _th, urllib.request as _ur, urllib.error as _ue
from http.server import ThreadingHTTPServer as _S
os.environ["PORTAL_TOOL_KEY"] = "selftest-" + "k" * 40
_ws0, app.WS = app.WS, _tf.mkdtemp()
app.forms.DIR = _tf.mkdtemp()
_srv = _S(("127.0.0.1", 0), app.portal_auth.install(app.H, "kordoc-local"))
_th.Thread(target=_srv.serve_forever, daemon=True).start()
def _get(path, hd):
    try:
        with _ur.urlopen(_ur.Request(f"http://127.0.0.1:{_srv.server_address[1]}{path}", headers=hd), timeout=10) as r:
            return r.status, _j.loads(r.read())
    except _ue.HTTPError as e:
        return e.code, None
_f = app.forms.register("관리 시험", open(os.path.join(app.ROOT, "sample", "polish_test.hwpx"), "rb").read(), app.kordoc, "alice")
_sig = lambda u, role=None: app.portal_auth.sign(u, "kordoc-local", role=role)
_edit = lambda hd: next(x for x in _get("/api/forms", hd)[1] if x["id"] == _f["id"])["can_edit"]
assert _edit(_sig("adm1", role="admin")) and _edit(_sig("adm2", role="admin")) and _edit(_sig("owner")), "관리자 두 계정·owner 모두 관리 권한"
assert not _edit(_sig("bob")) and not _edit({**_sig("bob"), "X-Agent-Role": "admin"}), "서명 없는 역할은 권한 없음"
_forged = {**_sig("bob"), "X-Agent-Role": "admin", "X-Agent-Role-Auth": _sig("adm1", role="admin")["X-Agent-Role-Auth"]}
assert not _edit(_forged), "남의 역할 서명을 옮겨 붙여도 권한 없음"
assert _get("/api/forms", {"X-Agent-User": "adm1", "X-Agent-Role": "admin"})[0] == 401, "서명 없으면 거부"
for _u in ("adm1", "adm2"):
    assert _get("/api/runs", _sig(_u, role="admin"))[0] == 200
assert sorted(os.listdir(os.path.join(app.WS, "u"))) == ["adm1", "adm2"], "관리자 계정은 각자 작업 폴더(루트를 같이 쓰지 않음)"
_srv.shutdown(); _sh.rmtree(app.WS); _sh.rmtree(app.forms.DIR); app.WS = _ws0
os.environ.pop("PORTAL_TOOL_KEY")

print("selftest OK — kordoc:", " ".join(app.KORDOC[:2]), "runs:", [x["run_id"] for x in app.list_runs()[:3]])
