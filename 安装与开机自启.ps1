# 千问会议自动录音 —— 安装 / 修复 / 注册开机自启
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File "安装与开机自启.ps1"
#
# 做三件事：
#   1) 确保本机运行环境（venv）存在，缺了就建好并装依赖
#      —— venv 故意放在 OneDrive 之外：它有一堆小文件，同步进去既慢又可能
#         被 OneDrive 按需下载变成占位符，导致 Python 起不来
#   2) 注册登录自启的计划任务（在你自己的会话里跑，这样才有托盘图标）
#   3) 打印核对结果

$ErrorActionPreference = 'Stop'

$App      = $PSScriptRoot
$Venv     = Join-Path $env:LOCALAPPDATA 'QwenAutoRecord\venv'
$VenvPy   = Join-Path $Venv 'Scripts\python.exe'
$VenvPyW  = Join-Path $Venv 'Scripts\pythonw.exe'
$TaskName = 'QwenAutoRecord'
$Script   = Join-Path $App 'tray_app.py'

Write-Host '=== 1) 运行环境 ===' -ForegroundColor Cyan
if (Test-Path $VenvPy) {
    Write-Host "  已存在: $Venv"
} else {
    Write-Host "  未找到，正在创建: $Venv"
    New-Item -ItemType Directory -Path (Split-Path $Venv) -Force | Out-Null
    $sysPy = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $sysPy) { throw 'PATH 里找不到 python，请先安装 Python 3。' }
    & $sysPy -m venv $Venv
    & $VenvPy -m pip install --upgrade pip --quiet
    Write-Host '  安装依赖（第一次会稍慢）...'
    & $VenvPy -m pip install pystray Pillow pycaw comtypes psutil soundcard websocket-client numpy
}

Write-Host '  校验依赖...'
$env:PYTHONIOENCODING='utf-8'
& $VenvPy -c "import pystray, PIL, pycaw, comtypes, psutil, soundcard, websocket, numpy; print('  deps OK')"
if ($LASTEXITCODE -ne 0) { throw '依赖校验失败。' }

Write-Host ''
Write-Host '=== 2) 注册开机自启（登录时启动）===' -ForegroundColor Cyan
if (-not (Test-Path $Script)) { throw "找不到 $Script" }

$action = New-ScheduledTaskAction -Execute $VenvPyW `
    -Argument ('"' + $Script + '"') -WorkingDirectory $App
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ("$env:USERDOMAIN\$env:USERNAME")
# ExecutionTimeLimit=0 -> 不限时长；MultipleInstances=IgnoreNew -> 防重复启动
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force `
    -Description '千问会议自动录音：检测 Teams/Zoom/微信 等通话并自动唤起千问录音纪要（托盘 + 控制面板）' | Out-Null

Write-Host "  已注册计划任务: $TaskName"

Write-Host ''
Write-Host '=== 3) 核对 ===' -ForegroundColor Cyan
$t = Get-ScheduledTask -TaskName $TaskName
Write-Host "  任务状态 : $($t.State)"
Write-Host "  运行程序 : $VenvPyW"
Write-Host "  参数     : $Script"
Write-Host "  触发条件 : 登录时（当前用户）"
Write-Host ''
Write-Host '用法：' -ForegroundColor Green
Write-Host '  · 手动启动 / 停止：双击同目录下的 启动自动录音.bat / 停止自动录音.bat'
Write-Host '  · 控制面板：http://127.0.0.1:8765/  （或右键托盘图标）'
Write-Host '  · 取消开机自启：Unregister-ScheduledTask -TaskName QwenAutoRecord -Confirm:$false'
Write-Host ''
Write-Host '⚠ 想让它现在就跑起来，请运行：启动自动录音.bat（不用等下次登录）'
