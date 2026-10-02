#!/usr/bin/env python3
"""LLM 없이 kordoc 왕복만 검증한다: parse(HWPX→MD) → [draft] lint → generate → validate, [polish] 조각 윤문 → patch.
Ollama 호출을 가짜 함수로 바꿔 끼운다.  npm install 후  python3 selftest.py"""
import os
import re
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
assert os.path.basename(app.polish_source(r["run_id"])) == "polish_test.hwpx"  # 윤문본을 다시 → 이름 유지
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
assert m["kind"] == "body" and m["roles"].get("chapter") and m["roles"].get("l1") and m["roles"].get("table"), m
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
_sh.rmtree(app.forms.DIR)

# 저작권 표기: ui.html 에서 지워도 서버가 다시 붙인다 (LICENSE·NOTICE)
import base64 as _b
_h = app.signed(app.HTML.replace("data-sig", "").replace('name="author"', ""))
assert "data-sig" in _h and 'name="author"' in _h and _b.b64decode("ZG9uZ2p1a2ltLmRldkBnbWFpbC5jb20=").decode() in _h, "저작권 표기 누락"

print("selftest OK — kordoc:", " ".join(app.KORDOC[:2]), "runs:", [x["run_id"] for x in app.list_runs()[:3]])
