@echo off
title Stop FOC-Assistant QQ Bot
echo Stopping FOC-Assistant QQ Bot...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -like '*foc-assistant*qq_bot.py*' }; if (-not $procs) { Write-Host 'No qq_bot.py process found.'; exit 0 }; foreach ($p in $procs) { Write-Host ('Stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }"
echo Done.
pause
