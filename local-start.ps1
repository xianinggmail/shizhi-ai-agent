param([ValidateRange(1024,65535)][int]$Port = 8765, [switch]$NoBrowser, [switch]$CheckOnly, [string]$LanIP = '')
$ErrorActionPreference = 'Stop'
$phase = '进入项目目录'
function Test-LocalPort {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync('127.0.0.1',$Port)
        if (-not $task.Wait(700)) { return $false }
        return $client.Connected
    } catch { return $false }
    finally { $client.Dispose() }
}
function Read-LocalAIStatus {
    $statusJSON = & $localPython -X utf8 -B -c 'import sys,json; from deepseek_ai import DeepSeekExtractor; print(json.dumps(DeepSeekExtractor(sys.argv[1]).status()))' $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw '无法检查本机配置，请检查 Python 环境。' }
    return ($statusJSON | ConvertFrom-Json)
}
try {
    Set-Location -LiteralPath $PSScriptRoot
    Write-Host '拾知：本机启动并配置'
    Write-Host '使用日常 Windows 账户运行即可。启动器不会强制关闭其他进程。'
    $phase = '查找 Python'
    $localVenv = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $localBundle = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if ($env:STUDY_PYTHON) { $localPython = $env:STUDY_PYTHON }
    elseif (Test-Path -LiteralPath $localVenv) { $localPython = $localVenv }
    elseif (Test-Path -LiteralPath $localBundle) { $localPython = $localBundle }
    else { $localPython = (Get-Command python -ErrorAction Stop).Source }
    $phase = '检查 Python 和程序文件'
    $localBuild = & $localPython -X utf8 -B (Join-Path $PSScriptRoot 'startup-check.py') $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw 'Python或依赖无法运行，请保留上面的错误文字。' }
    $localBuild = ([string]$localBuild).Trim()
    $phase = '检查已有网页服务'
    $url = 'http://127.0.0.1:' + $Port + '/'
    if (Test-LocalPort) {
        try { $service = Invoke-RestMethod ($url + 'api/config') -TimeoutSec 3 }
        catch { throw ('端口 ' + $Port + ' 已被占用，但无法读取服务信息。请先确认并关闭原启动窗口，再启动；未关闭任何进程。') }
        $expectedData = if ($env:STUDY_DATA_DIR) { [IO.Path]::GetFullPath($env:STUDY_DATA_DIR) } else { Join-Path $PSScriptRoot 'data' }
        if (-not $service.data_dir -or [IO.Path]::GetFullPath($service.data_dir).TrimEnd('\') -ne $expectedData.TrimEnd('\')) {
            throw ('端口 ' + $Port + ' 属于另一份项目或程序。未关闭该程序，也未另开数据库。')
        }
        if ($LanIP -and (-not $service.lan_enabled -or $service.lan_address -ne $LanIP)) { throw '当前服务没有启用此手机地址。请先在原启动窗口按 Ctrl+C，再双击手机访问启动.cmd。' }
        if ($service.server_build -eq $localBuild -and $service.ready) {
            Write-Host ('当前版本已在运行：' + $url)
            if (-not $NoBrowser -and -not $CheckOnly) {
                & $localPython -X utf8 -B -c 'import webbrowser,sys; webbrowser.open(sys.argv[1])' $url
            }
            exit 0
        }
        Write-Host '旧服务仍在使用这个端口，需要先关闭原启动窗口。' -ForegroundColor Yellow
        Write-Host '请回到之前启动网页的窗口，按 Ctrl+C（先保存网页草稿），再双击本文件。'
        Write-Host '本次没有强制结束进程，也没有重新配置密钥或启动第二份数据库服务。'
        exit 2
    }
    if ($CheckOnly) { Write-Host '环境检查通过；未启动服务、未读取密钥。'; exit 0 }
    $phase = '读取本机安全配置'
    $localStatus = Read-LocalAIStatus
    if (-not $localStatus.ready) {
        Write-Host $localStatus.error
        $phase = '在本机隐藏输入配置'
        & (Join-Path $PSScriptRoot 'configure-deepseek.ps1')
        $localStatus = Read-LocalAIStatus
        if (-not $localStatus.ready) { throw $localStatus.error }
    }
    Write-Host '本机配置可读取。此步骤不调用模型，不产生API费用。'
    $phase = '启动网页服务'
    & (Join-Path $PSScriptRoot 'start.ps1') -Port $Port -NoBrowser:$NoBrowser -LanIP $LanIP
} catch {
    Write-Host ('启动未完成，停在【' + $phase + '】。') -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '请保留本窗口，并提供这两行错误；不要提供API Key。'
    exit 1
}
