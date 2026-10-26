$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'DeepSeek API 密钥仅在本机输入，不要发送到聊天。'
Write-Host '保存后使用 Windows 当前账户 DPAPI 加密；不发起网络请求。'
$secureKey = Read-Host '请输入 DeepSeek API Key（输入隐藏）' -AsSecureString
if ($secureKey.Length -lt 10) { throw '密钥过短或为空，未保存。' }
$secretDir = Join-Path $PSScriptRoot '.secrets'
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null
$encryptedKey = ConvertFrom-SecureString -SecureString $secureKey
$secretPath = Join-Path $secretDir 'deepseek-key.dpapi'
[IO.File]::WriteAllText($secretPath,$encryptedKey,[Text.UTF8Encoding]::new($false))
$secureKey.Dispose()
Remove-Variable encryptedKey,secureKey -ErrorAction SilentlyContinue
Write-Host '已加密保存。返回网页点击“重新检查”，即可使用。'
Write-Host 'API费用按DeepSeek账户计费，应用不设费用上限。'
