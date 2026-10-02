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

print("selftest OK — kordoc:", " ".join(app.KORDOC[:2]), "runs:", [x["run_id"] for x in app.list_runs()[:3]])
