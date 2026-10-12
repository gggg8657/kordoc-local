# 제3자 구성요소 고지 (Third-Party Notices)

이 문서는 kordoc-local 가 쓰는 라이브러리·모델·데이터·외부 서비스와 그 라이선스를 적은 것입니다(2026-10-12 조사, 실제 파일 기준).
라이선스는 각 1차 출처(패키지의 LICENSE·package.json, GitHub 저장소, HuggingFace 모델 카드)에서 확인했습니다. 법률 검토가 아니며, 상업 이용 전에는 각 원문을 직접 확인하세요.
이 저장소의 원저작물(코드·UI·프롬프트·문서·도구 설계)은 DongJu Kim(gggg8657)이 작성했습니다 — `NOTICE` 참고. 제3자 구성요소의 권리는 이 저장소의 라이선스로 바뀌지 않습니다.

쓰임: **동봉** = 이 저장소에 파일이 들어 있음 · **설치** = setup 이 사용자 PC 에 내려받음(저장소에 없음) · **가중치** = 내려받는 모델 가중치(저장소에 없음) · **외부** = 별도로 실행 중인 서비스/프로그램을 호출

| 이름 | 버전 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|---|
| kordoc (chrisryugj) | 4.17.2 (`package.json` ^4.17.2) | 설치 (npm, 수정 없이) | MIT | https://github.com/chrisryugj/kordoc | 가능 | 라이선스 사본 `LICENSE-kordoc`. 저작권 © chrisryugj |
| kordoc 테스트 문서 `sample/dummy.hwpx` | — | **동봉** (kordoc `tests/fixtures` 에서 가져옴) | MIT | https://github.com/chrisryugj/kordoc | 가능 | © chrisryugj |
| Python 표준 라이브러리 | 3.9+ | 런타임 | PSF-2.0 | https://docs.python.org/3/license.html | 가능 | pip 설치 없음 |
| Node.js | 20+ (setup 이 없으면 v20.19.5 내려받음) | 외부 / 설치 · `WITH_NODE=1 ./pack.sh` 면 번들에 동봉 | MIT + Node 배포본의 제3자 라이선스 | https://github.com/nodejs/node/blob/main/LICENSE | 가능 | 번들 재배포 시 LICENSE 사본 |
| Ollama | — | 외부 (LLM 서버가 없으면 setup 이 공식 설치 스크립트로 설치) | MIT | https://github.com/ollama/ollama | 가능 | OpenAI 호환 서버(vLLM 등)로 대체 가능 |
| Google Gemma 4 (`gemma4:31b`) | — | 가중치 (권장 배포 모델 · 포털 환경 기본값) | Apache-2.0 (모델 카드 `google/gemma-4-31B-it`) | https://huggingface.co/google/gemma-4-31B-it · https://ai.google.dev/gemma/docs/gemma_4_license | 가능 | 저장소에 넣지 않음. 이전 세대 Gemma(1~3)는 별도의 Gemma Terms of Use — 다른 버전을 쓰면 그 조건 확인 |
| Qwen3 8B (`qwen3:8b`) | — | 가중치 (`LLM_MODEL` 을 안 줄 때 코드·setup 기본값, Ollama 에 모델이 없으면 `ollama pull`) | Apache-2.0 (모델 카드 `Qwen/Qwen3-8B`) | https://huggingface.co/Qwen/Qwen3-8B | 가능 | 저장소에 넣지 않음 |

### kordoc 의 의존성 (`npm install` 로 설치, 저장소에 없음)

`package-lock.json` 기준 199개 항목(플랫폼별 미리 빌드한 바이너리 변형 포함 — 실제로는 그 PC 플랫폼 것만 설치). 라이선스 분포: MIT 134 · Apache-2.0 22 · BSD-3-Clause 13 · LGPL-3.0-or-later 10(플랫폼별 libvips) · ISC 9 · Apache-2.0 AND LGPL-3.0-or-later 3(sharp 플랫폼 바이너리) · BSD-2-Clause 2 · 기타 1개씩(Apache-2.0 AND LGPL-3.0-or-later AND MIT[sharp-wasm32], MIT OR GPL-3.0-or-later[jszip], MIT AND Zlib[pako], MIT OR CC0-1.0[type-fest], Python-2.0[argparse], 0BSD[tslib]).
GPL·AGPL 단독 라이선스 패키지는 없습니다.
눈여겨볼 것:

| 이름 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| sharp | 0.35.5 | Apache-2.0 | kordoc 의 optional 의존성(이미지 처리) |
| @img/sharp-libvips-* (libvips 공유 라이브러리) | 1.3.4 | **LGPL-3.0-or-later** | 미리 빌드한 바이너리. 재배포(오프라인 번들) 시 고지 의무 — 아래 "확인 필요" |
| jszip | 3.10.2 | MIT OR GPL-3.0-or-later | MIT 로 사용(kordoc NOTICE 명시). `textpatch.mjs` 가 직접 import |
| onnxruntime-node | 1.30.0 | MIT | OCR 추론 |
| pdfjs-dist | 4.10.38 | Apache-2.0 | PDF 텍스트 추출 |
| @hyzyla/pdfium | 2.1.13 | MIT (PDFium 본체 BSD-3-Clause·Apache-2.0) | PDF 렌더 (wasm) |
| @huggingface/transformers | 4.3.0 | Apache-2.0 | 수식 OCR 경로에서만 사용 |
| cfb | 1.2.2 | Apache-2.0 | HWP 5.x 컨테이너 |
| @modelcontextprotocol/sdk | 1.3x | MIT | kordoc MCP 서버(이 도구는 안 씀) |

kordoc 자체가 포함한 파생 코드와 서식 자산(kordoc `NOTICE`·`THIRD_PARTY/`): OpenDataLoader PDF(Apache-2.0, Hancom), hml-equation-parser(Apache-2.0), rhwp(MIT, Edward Kim — 표준 기안문 서식 `templates/` 포함; 원 서식은 「행정 효율과 협업 촉진에 관한 규정 시행규칙」 별지 서식), claw-hwp(MIT), Pix2Text 알고리즘 이식(MIT).

### kordoc 이 실행 중 내려받는 모델 (저장소에 없음)

| 이름 | 쓰임 | 라이선스 | 링크 | 상업 이용 | 비고 |
|---|---|---|---|---|---|
| PP-OCRv5 mobile det / korean rec (ONNX) | 가중치 — 스캔 PDF·이미지 OCR. `pack.sh` 가 번들에 포함 | Apache-2.0 | https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det_onnx · https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec_onnx | 가능 | 재배포 시 LICENSE 사본 |
| Pix2Text MFD / MFR | 가중치 — PDF 수식 OCR(`--formula-ocr`). **이 도구는 켜지 않음** | MIT (MFD 는 YOLOv8 AGPL-3.0 기반 학습) | https://huggingface.co/breezedeus/pix2text-mfd · https://huggingface.co/breezedeus/pix2text-mfr | 조건부 | 아래 "확인 필요" |

## 확인 필요

- [#1](https://github.com/gggg8657/kordoc-local/issues/1) **폐쇄망 번들(pack.sh) 재배포 — sharp/libvips LGPL-3.0-or-later 외** — `pack.sh` 의 오프라인 번들은 node_modules(libvips LGPL-3.0-or-later 포함)·OCR 모델(Apache-2.0)·선택 시 Node.js 를 함께 묶는다. 번들을 외부에 넘기면 재배포가 되므로 라이선스 사본·고지 동봉 필요. 회피: 내부 시험용으로만 쓰기, 넘길 때 이 파일과 `node_modules/kordoc/NOTICE`·`THIRD_PARTY/` 를 번들 최상위에 복사, PDF·OCR 이 필요 없으면 `--omit=optional` 로 sharp/libvips 제외.
- [#2](https://github.com/gggg8657/kordoc-local/issues/2) **Pix2Text 수식 검출 모델(MFD) — MIT / YOLOv8 AGPL-3.0 기반** — kordoc 의 수식 OCR 모델 MFD 는 MIT 표기지만 YOLOv8(AGPL-3.0) 기반. 이 도구는 `--formula-ocr` 를 쓰지 않아 평소엔 받지 않는다. 회피: 계속 끈 채로 쓰고, 번들 전 `kordoc models --status` 로 수식 모델이 캐시에 섞이지 않았는지 확인.

## 이 저장소의 원저작물

위 표에 없는 파일(코드·UI·프롬프트·패턴·문서·설정 예시·selftest)은 DongJu Kim(gggg8657)이 작성했고 이 저장소의 `LICENSE`(MIT)를 따릅니다.
