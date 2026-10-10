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
$python = (Get-Command pythonw.exe -ErrorAction Stop).Source
$action = New-ScheduledTaskAction -Execute $python -Argument "app_server.py" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Created '$name': app_server.py starts when you log in. Remove it with -Remove."
