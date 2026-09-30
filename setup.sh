#!/usr/bin/env bash
# kordoc local — 원샷 설치·실행 스크립트 (macOS / Linux / Windows Git Bash)
#
#   bash setup.sh            # Node·kordoc 설치 + LLM 서버 탐색/세팅 + 웹 서버 기동 + 브라우저
#   bash setup.sh stop       # 웹 서버 종료
#
# 환경변수로 덮어쓰기:
#   LLM_BASE_URL=http://gpu:8000/v1 LLM_API=openai   이미 있는 서버를 강제 지정 (탐색 생략)
#   MODEL=qwen3:32b                                  Ollama에 모델이 없을 때 pull 할 모델 (기본 qwen3:8b)
#   PORT=8766
set -euo pipefail

REPO=https://github.com/gggg8657/kordoc-local.git
MODEL="${MODEL:-qwen3:8b}"
PORT="${PORT:-8766}"
NODE_VER="${NODE_VER:-v20.19.5}"

# ── 화면 ─────────────────────────────────────────────────────────────────
if [ -t 1 ]; then B=$'\033[1m'; D=$'\033[2m'; C=$'\033[36m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; else B= D= C= G= R= Y= N=; fi
STEP=0
banner() {
  printf '%s' "$C"
  cat <<'EOF2'

  ██╗  ██╗ ██████╗ ██████╗ ██████╗  ██████╗  ██████╗
  ██║ ██╔╝██╔═══██╗██╔══██╗██╔══██╗██╔═══██╗██╔════╝
  █████╔╝ ██║   ██║██████╔╝██║  ██║██║   ██║██║
  ██╔═██╗ ██║   ██║██╔══██╗██║  ██║██║   ██║██║
  ██║  ██╗╚██████╔╝██║  ██║██████╔╝╚██████╔╝╚██████╗
  ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═════╝  ╚═════╝  ╚═════╝   local
EOF2
  printf '%s  %sHWP·HWPX·PDF → 로컬 LLM 요약·질문·공문서 초안(HWPX)%s   %s(엔진: github.com/chrisryugj/kordoc · MIT)%s\n\n' "$N" "$B" "$N" "$D" "$N"
}
step() { STEP=$((STEP+1)); printf '  %s[%d/9]%s %s%-14s%s ' "$D" "$STEP" "$N" "$B" "$1" "$N"; }
ok()   { printf '%s✔%s %s\n' "$G" "$N" "${1:-}"; }
skip() { printf '%s–%s %s\n' "$D" "$N" "${1:-}"; }
die()  { printf '%s✘ %s%s\n\n' "$R" "$*" "$N" >&2; exit 1; }
spin() {
  local msg=$1; shift; local log; log=$(mktemp)
  "$@" >"$log" 2>&1 & local pid=$!
  local f='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
  while kill -0 $pid 2>/dev/null; do printf '\r  %s      %s%s%s %s' "$D" "$C" "${f:i%10:1}" "$N" "$msg"; i=$((i+1)); sleep 0.1; done
  printf '\r\033[K'
  if wait $pid; then rm -f "$log"; return 0; else printf '%s' "$D"; tail -5 "$log" | sed 's/^/        /'; printf '%s' "$N"; rm -f "$log"; return 1; fi
}
has()   { command -v "$1" >/dev/null 2>&1; }
probe() { curl -fsS -m 2 "$1" >/dev/null 2>&1; }
wait_for() { for _ in $(seq 1 "${2:-30}"); do probe "$1" && return 0; sleep 1; done; return 1; }
node_ok() { has node && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] 2>/dev/null; }

banner

# ── 1. OS ────────────────────────────────────────────────────────────────
step "OS 감지"
case "$(uname -s)" in
  Darwin*) OS=mac; NOS=darwin ;; Linux*) OS=linux; NOS=linux ;; MINGW*|MSYS*|CYGWIN*) OS=windows; NOS=win ;;
  *) die "지원하지 않는 OS: $(uname -s)" ;;
esac
case "$(uname -m)" in x86_64|amd64) ARCH=x64 ;; arm64|aarch64) ARCH=arm64 ;; *) ARCH=x64 ;; esac
ok "$OS ($ARCH)"

# ── 2. 레포 ──────────────────────────────────────────────────────────────
step "패키지 확보"
if [ -f "$(dirname "${BASH_SOURCE[0]:-.}")/app.py" ]; then
  cd "$(dirname "${BASH_SOURCE[0]}")"; ok "이미 있음 → $(pwd)"
elif [ -f ./kordoc-local/app.py ]; then
  cd kordoc-local; ok "이미 있음 → $(pwd)"
else
  has git || die "git이 없습니다. 폐쇄망이면 zip(또는 pack.sh 번들)을 풀고 그 폴더 안에서 bash setup.sh 를 실행하세요."
  spin "git clone $REPO" git clone -q "$REPO" kordoc-local || die "클론 실패 (인터넷 연결 확인)"
  cd kordoc-local; ok "클론 완료 → $(pwd)"
fi

# ── 3. Python ────────────────────────────────────────────────────────────
step "Python 3.9+"
PY=""
for c in python3 python py; do
  if has "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY=$c; break; fi
done
[ -n "$PY" ] || case "$OS" in
  mac)   die "python3가 없습니다: xcode-select --install 또는 brew install python" ;;
  linux) die "python3 (3.9+)가 없습니다: sudo apt install python3 / sudo dnf install python3" ;;
  *)     die "Python 3.9+가 없습니다: winget install Python.Python.3.12 후 터미널을 다시 열고 재실행" ;;
esac
ok "$($PY --version 2>&1) ($PY)"

if [ "${1:-}" = "stop" ]; then
  [ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null && rm -f .server.pid && ok "웹 서버 종료" || skip "실행 중인 서버 없음"
  exit 0
fi

# ── 4. Node 20+ & kordoc ─────────────────────────────────────────────────
step "Node 20+"
[ -d ./node/bin ] && export PATH="$PWD/node/bin:$PATH"
[ -d ./node ] && [ -f ./node/node.exe ] && export PATH="$PWD/node:$PATH"
if node_ok; then ok "$(node -v) ($(command -v node))"
else
  skip "없음/구버전 → 이 폴더 안에 Node $NODE_VER 설치 (시스템 건드리지 않음)"
  curl -fsS -m 5 https://nodejs.org >/dev/null 2>&1 || die "Node 20+ 가 없고 인터넷도 없습니다. 폐쇄망이면 WITH_NODE=1 ./pack.sh 로 만든 번들(node/ 포함)을 쓰세요."
  case "$OS" in
    windows) spin "node-$NODE_VER-win-$ARCH.zip" bash -c "curl -fsSL https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-win-$ARCH.zip -o node.zip && unzip -qo node.zip && rm -rf node node.zip && mv node-$NODE_VER-win-$ARCH node" || die "Node 다운로드 실패"
             export PATH="$PWD/node:$PATH" ;;
    *)       spin "node-$NODE_VER-$NOS-$ARCH.tar.gz" bash -c "curl -fsSL https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-$NOS-$ARCH.tar.gz | tar -xz && rm -rf node && mv node-$NODE_VER-$NOS-$ARCH node" || die "Node 다운로드 실패"
             export PATH="$PWD/node/bin:$PATH" ;;
  esac
  node_ok || die "Node 설치 후에도 실행되지 않습니다"
  printf '  %s              ✔ %s (./node)%s\n' "$G" "$(node -v)" "$N"
fi

step "kordoc 엔진"
if [ -f node_modules/kordoc/dist/cli.js ]; then ok "이미 설치됨 (kordoc $(node node_modules/kordoc/dist/cli.js --version 2>/dev/null | tail -1))"
else
  curl -fsS -m 5 https://registry.npmjs.org >/dev/null 2>&1 || die "node_modules 가 없고 npm 레지스트리에도 못 갑니다. 폐쇄망이면 ./pack.sh 번들을 쓰세요."
  spin "npm install kordoc (~600MB, PDF·OCR 엔진 포함)" env ONNXRUNTIME_NODE_INSTALL=skip npm install --no-audit --no-fund || die "npm install 실패"
  ok "kordoc $(node node_modules/kordoc/dist/cli.js --version 2>/dev/null | tail -1)"
fi
if [ -d models ] && ! node node_modules/kordoc/dist/cli.js models --status 2>/dev/null | grep -q '"allReady": true'; then
  spin "OCR 모델 사이드로드 (models/)" node node_modules/kordoc/dist/cli.js models --import ./models || true
fi

# ── 5. LLM 서버 ──────────────────────────────────────────────────────────
step "LLM 서버 탐색"
export LLM_API="${LLM_API:-}" LLM_BASE_URL="${LLM_BASE_URL:-}"
if [ -n "$LLM_BASE_URL" ]; then
  [ -n "$LLM_API" ] || { case "$LLM_BASE_URL" in *11434*) LLM_API=ollama ;; *) LLM_API=openai ;; esac; }
  ok "지정됨 → $LLM_API $LLM_BASE_URL"
elif probe http://localhost:11434/api/tags; then
  LLM_API=ollama LLM_BASE_URL=http://localhost:11434; ok "Ollama 실행 중 (:11434)"
else
  for p in 8000 1234 8080; do  # vLLM · LM Studio · llama.cpp
    if probe "http://localhost:$p/v1/models"; then LLM_API=openai LLM_BASE_URL="http://localhost:$p/v1"; ok "OpenAI 호환 서버 실행 중 (:$p)"; break; fi
  done
fi
if [ -z "$LLM_BASE_URL" ]; then
  if has ollama; then skip "서버 없음 → Ollama 기동"
  else
    skip "서버 없음 → Ollama 설치"
    curl -fsS -m 5 https://ollama.com >/dev/null 2>&1 || die "LLM 서버가 없고 인터넷도 없습니다. 서버 담당자에게 주소를 받아  LLM_BASE_URL=http://gpu:8000/v1 bash setup.sh  로 실행하세요."
    case "$OS" in
      mac)     if has brew; then spin "brew install ollama" brew install --quiet ollama
               else spin "Ollama.app 다운로드" bash -c 'curl -fsSL https://ollama.com/download/Ollama-darwin.zip -o /tmp/ollama.zip && unzip -qo /tmp/ollama.zip -d /Applications' && ln -sf /Applications/Ollama.app/Contents/Resources/ollama /usr/local/bin/ollama 2>/dev/null || true; fi ;;
      linux)   spin "ollama.com/install.sh (sudo 필요)" bash -c 'curl -fsSL https://ollama.com/install.sh | sh' ;;
      windows) has winget || die "winget이 없습니다. https://ollama.com/download 에서 설치 후 재실행"
               spin "winget install Ollama" winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements ;;
    esac || die "Ollama 설치 실패"
    has ollama || export PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:${LOCALAPPDATA:-}/Programs/Ollama"
    has ollama || die "설치 후에도 ollama 명령을 찾지 못했습니다. 터미널을 다시 열고 재실행하세요."
    printf '  %s              ✔ Ollama 설치 완료%s\n' "$G" "$N"
  fi
  nohup ollama serve >/dev/null 2>&1 &
  spin "ollama serve 기동 대기" wait_for http://localhost:11434/api/tags 30 || die "Ollama가 뜨지 않습니다. 'ollama serve' 를 직접 실행해 보세요."
  LLM_API=ollama LLM_BASE_URL=http://localhost:11434
  printf '  %s              ✔ Ollama 실행 중 (:11434)%s\n' "$G" "$N"
fi

# ── 6. 모델 ──────────────────────────────────────────────────────────────
step "모델"
if [ "$LLM_API" = ollama ]; then
  if ! curl -fsS "$LLM_BASE_URL/api/tags" | grep -q '"name"'; then
    skip "설치된 모델 없음 → pull (수 GB, 시간 걸림)"
    ollama pull "$MODEL" || die "모델 pull 실패. 폐쇄망이면 모델 파일을 별도 반입해 'ollama create' 하세요."
    printf '  %s              ' "$D"
  fi
  curl -fsS "$LLM_BASE_URL/api/tags" | grep -q "\"name\":\"$MODEL\"" || MODEL=$(curl -fsS "$LLM_BASE_URL/api/tags" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["models"][0]["name"])')
else
  MODEL=$(curl -fsS "$LLM_BASE_URL/models" | "$PY" -c 'import json,sys;print(json.load(sys.stdin)["data"][0]["id"])')
fi
ok "$MODEL"

# ── 7. 자가검증 ──────────────────────────────────────────────────────────
step "자가검증"
spin "kordoc 왕복 (HWPX→MD→lint→generate→validate)" "$PY" selftest.py || die "selftest 실패 — node_modules/kordoc 또는 sample/ 이 빠졌는지 확인"
ok "파싱·표기법 검수·HWPX 생성·구조 검증 통과"

# ── 8. 웹 서버 ───────────────────────────────────────────────────────────
step "웹 서버"
[ -f .server.pid ] && kill "$(cat .server.pid)" 2>/dev/null || true
LLM_API=$LLM_API LLM_BASE_URL=$LLM_BASE_URL LLM_MODEL=$MODEL PORT=$PORT KORDOC_OFFLINE="${KORDOC_OFFLINE:-}" nohup "$PY" app.py > server.log 2>&1 &
echo $! > .server.pid
spin "기동 대기 (:$PORT)" wait_for "http://localhost:$PORT/api/models" 20 || { cat server.log; die "웹 서버가 뜨지 않았습니다 (server.log 확인)"; }
URL="http://localhost:$PORT"
ok "$URL"

case "$OS" in mac) open "$URL" ;; linux) has xdg-open && xdg-open "$URL" >/dev/null 2>&1 || true ;; windows) start "$URL" ;; esac
printf '\n  %s┌──────────────────────────────────────────────────────────┐%s\n' "$G" "$N"
printf '  %s│%s  %s준비 완료%s  브라우저에서  %s%s%s  를 여세요.%*s%s│%s\n' "$G" "$N" "$B" "$N" "$C" "$URL" "$N" $((22 - ${#URL})) "" "$G" "$N"
printf '  %s│%s  LLM: %s%-10s%s %-38s%s│%s\n' "$G" "$N" "$D" "$LLM_API" "$N" "$MODEL" "$G" "$N"
printf '  %s│%s  종료: bash setup.sh stop   로그: server.log%*s%s│%s\n' "$G" "$N" 12 "" "$G" "$N"
printf '  %s└──────────────────────────────────────────────────────────┘%s\n\n' "$G" "$N"
