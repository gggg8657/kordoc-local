# kordoc-local — HWP·PDF 문서를 로컬 LLM으로 요약·질문·공문서 초안(HWPX) 생성

> **한 줄 요약** — 오픈소스 한국 공문서 파서 [kordoc](https://github.com/chrisryugj/kordoc)(MIT, PDF 공개 벤치 1위·HWPX 표 무손실)을
> 폐쇄망 서버의 Ollama나 vLLM에 붙여 쓰는 문서 에이전트 패키지입니다. HWP·HWPX·PDF·DOCX·XLSX(스캔본은 내장 OCR)를 올리면
> 로컬 LLM이 요약·질의응답을 하고, 보고서·기안문 초안은 kordoc이 표기법 검수 후 **한컴에서 바로 열리는 HWPX** 로 내려줍니다.
> 파이썬은 표준 라이브러리만, 문서 엔진은 Node 20 위에서 돌며 한컴오피스·클라우드 API·API 키가 필요 없습니다.
> 로컬 8B 모델로 PoC를 돌려 HWPX 생성·검증까지 확인했고, GPU 서버에서 큰 모델을 붙이면 초안 품질이 올라갑니다.
>
> - **설치**: `bash setup.sh` 하나 (OS 판별 → Node·kordoc 설치 → LLM 서버 탐색/세팅 → 웹 페이지 기동). 폐쇄망은 `pack.sh` 번들 반입.
> - **외부 통신**: 없음. OCR 모델은 번들에 동봉, `KORDOC_OFFLINE=1` 로 kordoc 아웃바운드 전량 차단.
> - **모델**: 한국어 되는 아무거나. Ollama든 OpenAI 호환(vLLM·LM Studio·llama.cpp)이든 환경변수 하나로 전환, 코드 수정 없음.

## 실행 — 스크립트 하나

```bash
bash setup.sh                                    # macOS / Linux / Windows Git Bash
powershell -ExecutionPolicy Bypass -File setup.ps1   # Windows PowerShell
```

순서: OS 감지 → 패키지 확보(없으면 clone) → Python 3.9+ 확인 → Node 20+ 확인(없으면 이 폴더 안 `node/` 에 설치, 시스템 안 건드림) →
`npm install kordoc` → LLM 서버 탐색(Ollama :11434 → vLLM :8000 → LM Studio :1234 → llama.cpp :8080) → 없으면 Ollama 설치·기동·모델 pull →
`selftest.py` → 웹 서버 기동 → 브라우저. 종료는 `bash setup.sh stop`.

이미 있는 서버를 쓰려면: `LLM_BASE_URL=http://gpu-server:8000/v1 bash setup.sh`

## 수동 실행

```bash
npm install && python3 selftest.py              # kordoc 설치 + LLM 없이 왕복 검증
python3 app.py                                  # Ollama, http://localhost:8766
LLM_API=openai LLM_BASE_URL=http://gpu-server:8000/v1 LLM_MODEL=Qwen3-32B python3 app.py

python3 app.py --cli summary 문서.hwpx
python3 app.py --cli qa 문서.pdf "3장 예산 총액은?"
python3 app.py --cli draft 문서.hwpx "개선방안 보고서 초안" 보고서     # 문서 없이는 -
```

| 환경변수 | 기본 | 설명 |
|---|---|---|
| `LLM_API` | `ollama` | `ollama` 또는 `openai` |
| `LLM_BASE_URL` | `http://localhost:11434` / `http://localhost:8000/v1` | 서버 주소 |
| `LLM_MODEL` | `qwen3:8b` | 기본 모델 (UI에서 변경 가능) |
| `LLM_API_KEY` | (없음) | OpenAI 호환 서버에 키가 필요할 때 |
| `NUM_CTX` | `16384` | Ollama 컨텍스트 창 |
| `MAX_CHARS` | `12000` | 문서 앞부분 컷 (모델 컨텍스트에 맞춰 조정) |
| `PORT` | `8766` | |
| `KORDOC_OFFLINE` | (없음) | `1` 이면 kordoc 아웃바운드 전량 차단 (폐쇄망 권장) |

## 파이프라인

| 작업 | 흐름 | LLM 콜 |
| --- | --- | ---: |
| 요약 | 문서 → `kordoc` parse → 개조식 요약 | 1 |
| 질문 | 문서 → `kordoc` parse → 근거 인용 답변 (없으면 "문서에 없음") | 1 |
| 공문서 초안 | (문서) → LLM 이 kordoc 규약 Markdown 작성 → `kordoc lint --munche` 표기법·문체 검수 → `kordoc generate --preset` → `validate` → HWPX 내려받기 | 1 |

프리셋: 보고서 · 기안문 · 계획서 · 통지 · 회의록 · 개조식 · 업무보고 · 서울방침 · 보도자료.
결과는 `_workspace/<run>/` 에 남는다(00 원본 · 01 파싱 md · 02 답변 · 03 HWPX). 지원 입력: hwp hwpx hml pdf docx xlsx xls png jpg webp md txt.

## 폐쇄망 반입

kordoc 은 상시 외부 통신이 없고(OCR 모델 최초 다운로드만), 이 앱은 LLM 서버 외에 아무 데도 붙지 않는다.
네이티브 모듈 때문에 **대상 OS/CPU 를 지정**해 인터넷 되는 PC 에서 번들을 만든다:

```bash
./pack.sh linux-x64            # → dist-offline/kordoc-local-linux-x64.tar.gz (~200MB, OCR 모델 포함)
WITH_NODE=1 ./pack.sh          # 서버에 Node 20+ 가 없으면 바이너리까지 동봉
```

내부망에서는 번들을 풀고 `bash setup.sh` (node_modules·models·node 가 있으면 다운로드 없이 그대로 사용) 또는 번들 안 `INSTALL.md` 대로 수동 실행.
`selftest.py` 는 LLM 없이 kordoc 왕복(HWPX→MD→lint→generate→validate)만 검증하므로 반입 직후 점검용.

## 파일

`app.py` 서버+파이프라인 · `ui.html` · `goal-prompt.md` 역할 프롬프트 3종 · `selftest.py` · `setup.sh`/`setup.ps1` 원샷 설치 · `pack.sh` 폐쇄망 번들 ·
`sample/dummy.hwpx` · `package.json`(kordoc ^4.17). 출처·라이선스는 `NOTICE`.
