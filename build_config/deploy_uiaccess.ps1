#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

try {
    $ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
    $ProjectRoot = Split-Path -Parent $ScriptDir
    $DistDir     = Join-Path $ProjectRoot "dist\CustomScreensaver"
    $InstallDir  = "C:\Program Files\CustomScreensaver"
    $ExePath     = Join-Path $InstallDir "CustomScreensaver.exe"
    $CertSubject = "CN=CustomRemoteScreensaver Local UIAccess"

    Write-Host "=== [1/4] 检查打包产物 ===" -ForegroundColor Cyan
    $DistExe = Join-Path $DistDir "CustomScreensaver.exe"
    if (-not (Test-Path $DistExe)) {
        throw "未找到 $DistExe，请先完成 PyInstaller 打包。"
    }
    $exeBytes = [System.IO.File]::ReadAllBytes($DistExe)
    $exeText  = [System.Text.Encoding]::ASCII.GetString($exeBytes)
    if ($exeText -notmatch 'uiAccess="true"') {
        throw "打包产物 $DistExe 的嵌入清单中缺少 uiAccess=`"true`"！请确认 CustomScreensaver.spec 中已设置 uac_uiaccess=True 并重新打包。"
    }
    Write-Host "已找到打包产物且已验证内嵌 uiAccess=true: $DistDir" -ForegroundColor Green

    Write-Host "=== [2/4] 准备本机受信任自签名代码签名证书 ===" -ForegroundColor Cyan
    $cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.Subject -eq $CertSubject -and $_.NotAfter -gt (Get-Date).AddDays(30)
    } | Select-Object -First 1

    if (-not $cert) {
        Write-Host "正在创建新的本机代码签名证书: $CertSubject" -ForegroundColor Yellow
        $cert = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $CertSubject `
            -CertStoreLocation "Cert:\LocalMachine\My" `
            -KeyAlgorithm RSA `
            -KeyLength 2048 `
            -HashAlgorithm SHA256 `
            -NotAfter (Get-Date).AddYears(5)
    }

    $tmpCer = Join-Path $env:TEMP "CustomScreensaver_UIAccess.cer"
    Export-Certificate -Cert $cert -FilePath $tmpCer -Force | Out-Null
    try {
        Import-Certificate -FilePath $tmpCer -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
        Import-Certificate -FilePath $tmpCer -CertStoreLocation "Cert:\LocalMachine\TrustedPublisher" | Out-Null
    } finally {
        Remove-Item $tmpCer -Force -ErrorAction SilentlyContinue
    }
    Write-Host "证书已导入受信任根证书，指纹: $($cert.Thumbprint)" -ForegroundColor Green

    Write-Host "=== [3/4] 部署到受保护目录 $InstallDir ===" -ForegroundColor Cyan
    Get-Process -Name "CustomScreensaver" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500

    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
    }
    Copy-Item -Path $DistDir -Destination $InstallDir -Recurse -Force
    $seedCfg = Join-Path $ProjectRoot "config.json"
    if (Test-Path $seedCfg) {
        Copy-Item -Path $seedCfg -Destination (Join-Path $InstallDir "config.json") -Force
    }
    Write-Host "文件已复制到 $InstallDir" -ForegroundColor Green

    Write-Host "=== [4/4] 对 CustomScreensaver.exe 进行 Authenticode 签名 ===" -ForegroundColor Cyan
    $sig = Set-AuthenticodeSignature -FilePath $ExePath -Certificate $cert -HashAlgorithm SHA256
    Write-Host "签名状态: $($sig.Status) ($($sig.StatusMessage))" -ForegroundColor Green
    if ($sig.Status -ne "Valid") {
        throw "签名状态异常: $($sig.Status) - $($sig.StatusMessage)"
    }

    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut((Join-Path $ProjectRoot "启动UIAccess屏保(Band2).lnk"))
    $Shortcut.TargetPath = $ExePath
    $Shortcut.WorkingDirectory = $InstallDir
    $Shortcut.Description = "启动带 UIAccess (Band=2) 的自定义隐私屏保"
    $Shortcut.Save()

    Write-Host ""
    Write-Host "全部部署成功！正在启动 Band=2 (UIAccess) 版自定义隐私屏保..." -ForegroundColor Green
    Start-Process -FilePath $ExePath -WorkingDirectory $InstallDir
    Write-Host "已启动！右下角托盘会出现图标，你可以关闭本窗口了。" -ForegroundColor Cyan
} catch {
    Write-Host ""
    Write-Host "部署失败: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkRed
}