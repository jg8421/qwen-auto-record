@echo off
rem Start the Qwen meeting auto-recorder (tray + panel) in the background.
rem Runtime venv lives OUTSIDE OneDrive: %LOCALAPPDATA%\QwenAutoRecord\venv
rem
rem IMPORTANT: if this folder path contains SPACES (e.g. under OneDrive),
rem Passing it via -ArgumentList unquoted makes Python receive a truncated path
rem passing it unquoted makes Python receive a truncated path. Instead we set
rem -WorkingDirectory and pass the script as a bare filename.
rem Also: writes to launcher.pid, NOT auto_record.pid -- the app owns that file as its lock.
setlocal
set HERE=%~dp0
set VENV=%LOCALAPPDATA%\QwenAutoRecord\venv
powershell -NoProfile -ExecutionPolicy Bypass -Command "$exe=Join-Path '%VENV%' 'Scripts\pythonw.exe'; if (-not (Test-Path $exe)) { Write-Host ('ERROR: venv not found: ' + $exe); Write-Host 'Run setup script first.'; exit 1 }; $p = Start-Process -FilePath $exe -ArgumentList 'tray_app.py' -WorkingDirectory '%HERE%' -WindowStyle Hidden -PassThru; $p.Id | Out-File -Encoding ascii ('%HERE%launcher.pid'); Start-Sleep -Seconds 5; $ok = Test-NetConnection -ComputerName 127.0.0.1 -Port 8765 -InformationLevel Quiet -WarningAction SilentlyContinue; if ($ok) { Write-Host 'OK  - panel is up: http://127.0.0.1:8765/' } else { Write-Host 'WARN - panel did not come up in 5s; see auto_record.log' }"