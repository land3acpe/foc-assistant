@echo off
title FOC-Assistant QQ Bot Status
echo.
echo ==========================================
echo   FOC-Assistant QQ Bot Status
echo ==========================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$procs = Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and $_.CommandLine -like '*foc-assistant*qq_bot.py*' }; if ($procs) { Write-Host 'Status: running'; $procs | Select-Object ProcessId,CreationDate,CommandLine | Format-Table -AutoSize } else { Write-Host 'Status: not running' }"
echo.
echo Start: run_qq.bat
echo Debug: run_qq_debug.bat
echo Stop : stop_qq.bat
echo.
pause
