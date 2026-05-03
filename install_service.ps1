# 清理旧任务
Unregister-ScheduledTask -TaskName "FOC-QQ-Bot" -ErrorAction SilentlyContinue -Confirm:$false

$action = New-ScheduledTaskAction -Execute "D:\Python312\pythonw.exe" -Argument "C:\Users\macree\foc-assistant\qq_bot.py"
$trigger = New-ScheduledTaskTrigger -AtLogon
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName "FOC-QQ-Bot" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force

Write-Host "Task created OK. Starting now..."
Start-ScheduledTask -TaskName "FOC-QQ-Bot"

Write-Host ""
Write-Host "Done. Bot will auto-start at login and run in background."
