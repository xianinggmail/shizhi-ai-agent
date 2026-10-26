param([ValidateRange(1024,65535)][int]$Port = 8765, [string]$LanIP = '', [switch]$NoBrowser, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
try {
    $addresses = @()
    foreach ($adapter in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne 'Up' -or $adapter.NetworkInterfaceType -notin @('Wireless80211','Ethernet')) { continue }
        $properties = $adapter.GetIPProperties()
        $hasGateway = @($properties.GatewayAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and $_.Address.ToString() -ne '0.0.0.0' }).Count -gt 0
        if (-not $hasGateway) { continue }
        foreach ($entry in $properties.UnicastAddresses) {
            $address = $entry.Address.ToString()
            if ($entry.Address.AddressFamily -eq 'InterNetwork' -and ($address -match '^10\.' -or $address -match '^192\.168\.' -or $address -match '^172\.(1[6-9]|2[0-9]|3[01])\.')) { $addresses += $address }
        }
    }
    $addresses = @($addresses | Select-Object -Unique)
    if ($LanIP) {
        if ($LanIP -notin $addresses) { throw '指定地址不是本机可用的 Wi-Fi 或以太网专用地址。' }
    } else {
        if ($addresses.Count -ne 1) { throw '无法唯一确定 Wi-Fi 地址。请使用 PowerShell 运行 .\lan-start.ps1 -LanIP 电脑的IPv4地址，或先断开 VPN 后重试。' }
        $LanIP = $addresses[0]
    }
    Write-Host ('手机访问地址：http://' + $LanIP + ':' + $Port + '/')
    Write-Host '电脑和手机连接同一可信 Wi-Fi，保持电脑开机及此窗口运行。'
    Write-Host '如 Windows 防火墙询问，请仅允许专用网络；访问码会在服务启动后显示。'
    Write-Host '若原本机服务正在运行，请先在原窗口按 Ctrl+C，再重新启动本文件。'
    & (Join-Path $PSScriptRoot 'local-start.ps1') -Port $Port -LanIP $LanIP -NoBrowser:$NoBrowser -CheckOnly:$CheckOnly
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
