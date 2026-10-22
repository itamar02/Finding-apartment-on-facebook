# Start the phone app's server automatically when you log in to Windows.
#   powershell -ExecutionPolicy Bypass -File register_autostart.ps1          # create the task
#   powershell -ExecutionPolicy Bypass -File register_autostart.ps1 -Remove  # delete it
# Needs no administrator rights (the task runs as you, only while you are logged in).
param([switch]$Remove)
$name = "ApartmentBot app"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$name'."
    exit 0
}
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# run_app.cmd keeps the server running (it restarts it if it stops) and logs to app_server.log.
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument ("/c start ""ApartmentBot app"" /min """ + (Join-Path $here "run_app.cmd") + """") -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Created '$name': run_app.cmd starts (minimized) when you log in. Remove it with -Remove."
