# kordoc local — Windows PowerShell 원샷 설치·실행 스크립트
#
#   powershell -ExecutionPolicy Bypass -File setup.ps1          # Node·kordoc 설치 + LLM 탐색/세팅 + 웹 서버 + 브라우저
#   powershell -ExecutionPolicy Bypass -File setup.ps1 stop     # 웹 서버 종료
#
# 환경변수: $env:LLM_BASE_URL / $env:LLM_API (기존 서버 강제 지정), $env:MODEL (pull 할 모델), $env:PORT
param([string]$Cmd = "")
$ErrorActionPreference = "Stop"
$Repo    = "https://github.com/gggg8657/kordoc-local.git"
$Model   = if ($env:MODEL) { $env:MODEL } else { "qwen3:8b" }
$Port    = if ($env:PORT)  { $env:PORT }  else { "8766" }
$NodeVer = "v20.19.5"
function Say($m) { Write-Host "▶ $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "✖ $m" -ForegroundColor Red; exit 1 }
function Has($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }
function Probe($u) { try { Invoke-RestMethod -Uri $u -TimeoutSec 2 | Out-Null; $true } catch { $false } }
function NodeOk { (Has node) -and ([int]((node -p "process.versions.node.split('.')[0]") 2>$null) -ge 20) }

# 1. 레포
if (Test-Path "$PSScriptRoot\app.py") { Set-Location $PSScriptRoot }
elseif (Test-Path ".\kordoc-local\app.py") { Set-Location kordoc-local }
else {
  if (-not (Has git)) { Die "git이 없습니다. 폐쇄망이면 zip을 풀고 그 폴더 안에서 실행하세요. (winget install Git.Git)" }
  Say "레포 클론: $Repo"; git clone -q $Repo kordoc-local; Set-Location kordoc-local
}
Say "작업 폴더: $(Get-Location)"

# 2. Python
$Py = $null
foreach ($c in "python", "python3", "py") {
  if ((Has $c) -and ((& $c -c "import sys;print(sys.version_info>=(3,9))" 2>$null) -eq "True")) { $Py = $c; break }
}
if (-not $Py) { Die "Python 3.9+가 없습니다: winget install Python.Python.3.12 후 터미널을 다시 열고 재실행" }
Say "Python: $(& $Py --version) ($Py)"

# 3. stop
if ($Cmd -eq "stop") {
  if (Test-Path .server.pid) { Stop-Process -Id (Get-Content .server.pid) -ErrorAction SilentlyContinue; Remove-Item .server.pid; Say "웹 서버 종료" }
  else { Say "실행 중인 서버 없음" }
  exit 0
}

# 4. Node 20+ (없으면 이 폴더 안에 zip 풀어서 사용 — 시스템 건드리지 않음)
if (Test-Path ".\node\node.exe") { $env:PATH = "$PWD\node;$env:PATH" }
if (-not (NodeOk)) {
  if (-not (Probe "https://nodejs.org")) { Die "Node 20+ 가 없고 인터넷도 없습니다. 폐쇄망이면 WITH_NODE=1 ./pack.sh 번들(node\ 포함)을 쓰세요." }
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
  Say "Node $NodeVer 다운로드 (.\node)"
  Invoke-WebRequest "https://nodejs.org/dist/$NodeVer/node-$NodeVer-win-$arch.zip" -OutFile node.zip
  Expand-Archive node.zip -DestinationPath . -Force; Remove-Item node.zip
  if (Test-Path node) { Remove-Item node -Recurse -Force }; Rename-Item "node-$NodeVer-win-$arch" node
  $env:PATH = "$PWD\node;$env:PATH"
  if (-not (NodeOk)) { Die "Node 설치 후에도 실행되지 않습니다" }
}
Say "Node: $(node -v)"

# 5. kordoc 엔진
if (-not (Test-Path "node_modules\kordoc\dist\cli.js")) {
  if (-not (Probe "https://registry.npmjs.org")) { Die "node_modules 가 없고 npm 레지스트리에도 못 갑니다. 폐쇄망이면 pack.sh 번들을 쓰세요." }
  Say "npm install kordoc (~600MB, PDF·OCR 엔진 포함)"
  $env:ONNXRUNTIME_NODE_INSTALL = "skip"; npm install --no-audit --no-fund; if ($LASTEXITCODE) { Die "npm install 실패" }
}
Say "kordoc: $((node node_modules\kordoc\dist\cli.js --version 2>$null | Select-Object -Last 1))"
if ((Test-Path models) -and -not ((node node_modules\kordoc\dist\cli.js models --status 2>$null) -match '"allReady": true')) {
  Say "OCR 모델 사이드로드 (models\)"; node node_modules\kordoc\dist\cli.js models --import .\models
}

# 6. LLM 서버 탐색
$Api = $env:LLM_API; $Base = $env:LLM_BASE_URL
if ($Base) {
  if (-not $Api) { $Api = if ($Base -match "11434") { "ollama" } else { "openai" } }
  Say "지정된 LLM 서버 사용: $Api $Base"
} elseif (Probe "http://localhost:11434/api/tags") { $Api = "ollama"; $Base = "http://localhost:11434"; Say "Ollama 발견 (11434)" }
else {
  foreach ($p in 8000, 1234, 8080) { if (Probe "http://localhost:$p/v1/models") { $Api = "openai"; $Base = "http://localhost:$p/v1"; Say "OpenAI 호환 서버 발견 ($p)"; break } }
}

# 7. 없으면 Ollama 설치·기동
if (-not $Base) {
  if (-not (Has ollama)) {
    if (-not (Probe "https://ollama.com")) { Die "LLM 서버가 없고 인터넷도 없습니다. `$env:LLM_BASE_URL='http://gpu:8000/v1' 로 지정해 다시 실행하세요." }
    if (-not (Has winget)) { Die "winget이 없습니다. https://ollama.com/download 에서 설치 후 재실행" }
    Say "Ollama 설치"; winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
    $env:PATH += ";$env:LOCALAPPDATA\Programs\Ollama"
    if (-not (Has ollama)) { Die "설치 후에도 ollama 명령을 찾지 못했습니다. 터미널을 다시 열고 재실행" }
  }
  if (-not (Probe "http://localhost:11434/api/tags")) {
    Say "Ollama 서버 기동"; Start-Process ollama -ArgumentList serve -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not (Probe "http://localhost:11434/api/tags"); $i++) { Start-Sleep 1 }
    if (-not (Probe "http://localhost:11434/api/tags")) { Die "Ollama가 뜨지 않습니다. 'ollama serve' 를 직접 실행해 보세요." }
  }
  $Api = "ollama"; $Base = "http://localhost:11434"
}

# 8. 모델
if ($Api -eq "ollama") {
  $names = @((Invoke-RestMethod "$Base/api/tags").models | ForEach-Object { $_.name })
  if ($names.Count -eq 0) { Say "설치된 모델 없음 → pull $Model (수 GB)"; ollama pull $Model; $names = @($Model) }
  if ($names -notcontains $Model) { $Model = $names[0] }
} else {
  $Model = (Invoke-RestMethod "$Base/models").data[0].id
}
Say "모델: $Model"

# 9. 자가검증 + 서버
Say "kordoc 왕복 자가검증 (HWPX→MD→lint→generate→validate)"
& $Py selftest.py | Out-Null; if ($LASTEXITCODE) { Die "selftest 실패 — node_modules\kordoc 또는 sample\ 누락 확인" }
if (Test-Path .server.pid) { Stop-Process -Id (Get-Content .server.pid) -ErrorAction SilentlyContinue }
$env:LLM_API = $Api; $env:LLM_BASE_URL = $Base; $env:LLM_MODEL = $Model; $env:PORT = $Port
Say "웹 서버 기동 (포트 $Port)"
$p = Start-Process $Py -ArgumentList app.py -WindowStyle Hidden -PassThru -RedirectStandardOutput server.log -RedirectStandardError server.err.log
$p.Id | Set-Content .server.pid
for ($i = 0; $i -lt 20 -and -not (Probe "http://localhost:$Port/api/models"); $i++) { Start-Sleep 1 }
if (-not (Probe "http://localhost:$Port/api/models")) { Get-Content server.err.log; Die "웹 서버가 뜨지 않았습니다" }
Start-Process "http://localhost:$Port"
Say "완료 → http://localhost:$Port   (종료: setup.ps1 stop / 로그: server.log)"
