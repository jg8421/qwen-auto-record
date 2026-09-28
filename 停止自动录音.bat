@echo off
rem Stop ONLY this tool's watcher. Kills the app (auto_record.pid) and the venv
rem launcher (launcher.pid) if present, leaving other pythonw processes alone.
setlocal
set HERE=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -Command "$f1='%HERE%auto_record.pid'; $f2='%HERE%launcher.pid'; $killed=@(); foreach ($f in @($f1,$f2)) { if (Test-Path $f) { $procId=(Get-Content $f | Select-Object -First 1).Trim(); if ($procId -match '^\d+$') { $p = Get-Process -Id $procId -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue; $killed += $procId } } } }; Start-Sleep -Seconds 2; foreach ($f in @($f1,$f2)) { Remove-Item $f -ErrorAction SilentlyContinue }; if ($killed.Count -gt 0) { Write-Host ('Stopped. PIDs=' + ($killed -join ',')) } else { Write-Host 'Nothing to stop (no live pid found).' }; Write-Host ('Remaining pythonw: ' + (@(Get-Process pythonw -ErrorAction SilentlyContinue).Count))"
