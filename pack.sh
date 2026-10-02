#!/usr/bin/env bash
# 폐쇄망 반입 번들 — 인터넷 되는 PC에서 실행. 대상 플랫폼용 node_modules + OCR 모델(+선택 Node 바이너리)을 tar.gz 하나로.
#   ./pack.sh                 # linux-x64 (기본)
#   ./pack.sh linux-arm64
#   WITH_NODE=1 ./pack.sh     # Node 20 바이너리까지 동봉 (서버에 node 없을 때)
set -euo pipefail
cd "$(dirname "$0")"
PLATFORM="${1:-linux-x64}"; OS="${PLATFORM%-*}"; CPU="${PLATFORM#*-}"
NODE_VER="${NODE_VER:-v20.19.5}"
STAGE="dist-offline/kordoc-local-$PLATFORM"
rm -rf "$STAGE" && mkdir -p "$STAGE"

# 1) 대상 플랫폼용 의존성 (native: sharp 는 --os/--cpu 로 선택, onnxruntime-node 는 전 플랫폼 동봉, pdfium 은 wasm)
cp package.json "$STAGE/"
( cd "$STAGE" && ONNXRUNTIME_NODE_INSTALL=skip npm install --os="$OS" --cpu="$CPU" --libc=glibc --omit=dev --ignore-scripts --no-audit --no-fund )

# 2) OCR 모델 (없으면 지금 내려받아 캐시) → SHA-256 매니페스트와 함께 export
node node_modules/kordoc/dist/cli.js check-ocr-models >/dev/null 2>&1 || true
node node_modules/kordoc/dist/cli.js models --export "$STAGE/models"

# 3) 앱 파일
cp app.py forms.py textpatch.mjs gian_defaults.json gian_defaults.example.json ui.html goal-prompt.md selftest.py setup.sh setup.ps1 README.md NOTICE LICENSE LICENSE-kordoc "$STAGE/"
cp -r sample "$STAGE/"

# 4) (선택) Node 바이너리
if [ "${WITH_NODE:-}" = "1" ]; then
  curl -fsSL "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-$PLATFORM.tar.gz" | tar -xz -C "$STAGE"
  mv "$STAGE/node-$NODE_VER-$PLATFORM" "$STAGE/node"
fi

cat > "$STAGE/INSTALL.md" <<'INS'
# 폐쇄망 설치 (kordoc-local)

```bash
tar -xzf kordoc-local-<platform>.tar.gz && cd kordoc-local-<platform>

# A. 스크립트 하나로 (node_modules·models·node 가 번들에 있으므로 다운로드 없음)
KORDOC_OFFLINE=1 LLM_BASE_URL=http://<gpu-host>:11434 bash setup.sh        # Ollama
KORDOC_OFFLINE=1 LLM_BASE_URL=http://<gpu-host>:8000/v1 bash setup.sh      # vLLM 등 OpenAI 호환

# B. 수동
export PATH="$PWD/node/bin:$PATH"          # 번들에 node/ 가 있을 때만
node node_modules/kordoc/dist/cli.js models --import ./models   # OCR 모델 사이드로드 (SHA-256 검증)
export KORDOC_OFFLINE=1                    # 모든 아웃바운드 차단 (kordoc)
python3 selftest.py                        # LLM 없이 kordoc 왕복 검증
LLM_BASE_URL=http://<gpu-host>:11434 LLM_MODEL=<모델> python3 app.py                    # Ollama
LLM_API=openai LLM_BASE_URL=http://<gpu-host>:8000/v1 LLM_MODEL=<모델> python3 app.py   # OpenAI 호환
```

Python 3.9+ (stdlib 만), Node 20+. 포트 8766 (PORT 로 변경).
INS

( cd dist-offline && tar -czf "kordoc-local-$PLATFORM.tar.gz" "kordoc-local-$PLATFORM" )
ls -lh "dist-offline/kordoc-local-$PLATFORM.tar.gz"
