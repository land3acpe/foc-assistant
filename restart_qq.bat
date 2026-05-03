@echo off
title Restart FOC-Assistant QQ Bot
echo Restarting FOC-Assistant QQ Bot...

powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -like '*foc-assistant*qq_bot.py*' }; foreach ($p in $procs) { Write-Host ('Stopping PID ' + $p.ProcessId); Stop-Process -Id $p.ProcessId -Force }"

timeout /t 1 /nobreak >nul

echo Starting in debug mode...
echo Press Ctrl+C in this window to stop.
echo.
D:\Python312\python.exe -u C:\Users\macree\foc-assistant\qq_bot.py

pause
