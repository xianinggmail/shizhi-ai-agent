param([int]$Port = 8765, [switch]$NoBrowser, [string]$LanIP = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ($env:STUDY_PYTHON) { $studyPython = $env:STUDY_PYTHON }
elseif (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe')) { $studyPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
elseif (Test-Path -LiteralPath $bundledPython) { $studyPython = $bundledPython }
else { $studyPython = (Get-Command python -ErrorAction Stop).Source }
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'work') | Out-Null
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPYCACHEPREFIX = Join-Path $PSScriptRoot 'work\pycache'
& $studyPython -c "import pypdf" 2>$null
if ($LASTEXITCODE -ne 0) { throw '缺少 pypdf。请使用所选 Python 执行：python -m pip install -r requirements.txt，再启动。' }
Write-Host "拾知学习助手 http://127.0.0.1:$Port"
Write-Host '保持本窗口打开。按 Ctrl+C 结束服务；数据保留在本机 data 目录。'
$studyArguments = @((Join-Path $PSScriptRoot 'app.py'),'--port',[string]$Port)
if ($LanIP) { $studyArguments += @('--lan-ip',$LanIP) }
if (-not $NoBrowser) { $studyArguments += '--open-browser' }
& $studyPython @studyArguments
if ($LASTEXITCODE -ne 0) { throw '服务启动失败；如端口被占用，请使用 .\start.ps1 -Port 8766' }
