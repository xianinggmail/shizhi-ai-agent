param([switch]$NoStart)
$ErrorActionPreference = 'Stop'
$target = 'D:\大四\新传AI学习助手'
$resolved = (Resolve-Path -LiteralPath $target).Path.TrimEnd('\')
if ($resolved -ne $target) { throw '目标目录校验失败。' }
if (-not (Test-Path -LiteralPath (Join-Path $target 'app.py'))) { throw '未找到原项目 app.py。' }
$checks = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'checksums.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$files = @($checks.PSObject.Properties | ForEach-Object { $_.Name })
foreach ($name in $files) {
    $destination = [IO.Path]::GetFullPath((Join-Path $target $name))
    if (-not $destination.StartsWith($target + '\',[StringComparison]::OrdinalIgnoreCase)) { throw '安装路径越界，已停止。' }
    $source = Join-Path $PSScriptRoot $name
    $expected = $checks.PSObject.Properties[$name].Value
    if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw ('升级文件校验失败：' + $name) }
}
$pythonBundle = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if ($env:STUDY_PYTHON) { $python = $env:STUDY_PYTHON }
elseif (Test-Path -LiteralPath (Join-Path $target '.venv\Scripts\python.exe')) { $python = Join-Path $target '.venv\Scripts\python.exe' }
elseif (Test-Path -LiteralPath $pythonBundle) { $python = $pythonBundle }
else { $python = (Get-Command python -ErrorAction Stop).Source }
& $python -c 'import pypdf'
if ($LASTEXITCODE -ne 0) { throw '原环境缺少 pypdf，请先修复环境。' }
$service = $null
try { $service = Invoke-RestMethod 'http://127.0.0.1:8765/api/config' -TimeoutSec 2 } catch { }
if ($service -and $service.data_dir -and ([IO.Path]::GetFullPath($service.data_dir).TrimEnd('\') -eq (Join-Path $target 'data'))) {
    $jobs = @(Invoke-RestMethod 'http://127.0.0.1:8765/api/jobs' -TimeoutSec 3)
    if (@($jobs | Where-Object { $_.status -in @('queued','running') }).Count -gt 0) { throw '原服务仍在生成知识点，请等待完成后重新安装。' }
    if ($service.paper_lab_version) {
        $paperJobs = @(Invoke-RestMethod 'http://127.0.0.1:8765/api/paper-jobs' -TimeoutSec 3)
        if (@($paperJobs | Where-Object { $_.status -in @('queued','running') }).Count -gt 0) { throw '原服务仍在精读论文，请等待完成后重新安装。' }
    }
}
$backup = Join-Path $target ('work\论文功能升级备份_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff'))
New-Item -ItemType Directory -Path $backup -ErrorAction Stop | Out-Null
$missing = @()
foreach ($name in $files) {
    $original = Join-Path $target $name
    if (Test-Path -LiteralPath $original) {
        $copy = Join-Path $backup $name
        New-Item -ItemType Directory -Path (Split-Path -Parent $copy) -Force | Out-Null
        Copy-Item -LiteralPath $original -Destination $copy
    } else { $missing += $name }
}
$missing | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backup 'originally-missing.json') -Encoding UTF8
$database = Join-Path $target 'data\learning.sqlite3'
if (Test-Path -LiteralPath $database) {
    & $python (Join-Path $PSScriptRoot 'backup_sqlite.py') $database (Join-Path $backup 'learning.sqlite3')
    if ($LASTEXITCODE -ne 0) { throw 'SQLite备份失败，程序未安装。' }
}
try {
    foreach ($name in $files) {
        $destination = Join-Path $target $name
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination ($destination + '.new')
        Move-Item -LiteralPath ($destination + '.new') -Destination $destination -Force
    }
    foreach ($name in $files) {
        if ((Get-FileHash -LiteralPath (Join-Path $target $name) -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checks.PSObject.Properties[$name].Value) { throw ('安装后校验失败：' + $name) }
    }
} catch {
    foreach ($name in $files) {
        $destination = Join-Path $target $name
        if ($missing -contains $name) { if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination } }
        else { Copy-Item -LiteralPath (Join-Path $backup $name) -Destination $destination -Force }
        if (Test-Path -LiteralPath ($destination + '.new')) { Remove-Item -LiteralPath ($destination + '.new') }
    }
    throw
}
Write-Host '论文精读功能安装完成。PDF与密钥未修改，数据库将在启动时增加论文专用表。'
Write-Host ('旧程序和数据库备份：' + $backup)
Write-Host '网页打开后按 Ctrl+F5，在左侧进入“论文精读”。'
if (-not $NoStart) { & (Join-Path $target 'local-start.ps1') }
