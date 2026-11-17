# Instala as tarefas agendadas do Goltrader (usuario atual, sem admin).
$wd  = "C:\Users\Ventura\goltrader"
$pyw = "$wd\.venv\Scripts\pythonw.exe"
$me  = "$env:USERDOMAIN\$env:USERNAME"
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
       -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$pr  = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$logon = New-ScheduledTaskTrigger -AtLogOn -User $me
Register-ScheduledTask -TaskName "Goltrader Autotrader" -Action (New-ScheduledTaskAction -Execute $pyw -Argument "autotrader.py" -WorkingDirectory $wd) -Trigger $logon -Settings $set -Principal $pr -Force | Out-Null
Register-ScheduledTask -TaskName "Goltrader Painel" -Action (New-ScheduledTaskAction -Execute $pyw -Argument "launch_panel.py" -WorkingDirectory $wd) -Trigger $logon -Settings $set -Principal $pr -Force | Out-Null
$rep = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$wset = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "Goltrader Watchdog" -Action (New-ScheduledTaskAction -Execute $pyw -Argument "gt_watchdog.py" -WorkingDirectory $wd) -Trigger @($logon, $rep) -Settings $wset -Principal $pr -Force | Out-Null
Get-ScheduledTask -TaskName "Goltrader*" | Select-Object TaskName, State
