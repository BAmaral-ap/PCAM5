# Execute após configurar .env, login e testar o primeiro alerta.
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$MonitorFile = Join-Path $ProjectDir 'monitor.py'
if (-not (Test-Path $PythonExe)) { throw 'Crie .venv e instale requirements.txt antes de registrar a tarefa.' }
if (-not (Test-Path (Join-Path $ProjectDir '.env'))) { throw 'Configure .env localmente antes de registrar a tarefa.' }
$TaskAction = New-ScheduledTaskAction -Execute $PythonExe -Argument ('"' + $MonitorFile + '" --watch') -WorkingDirectory $ProjectDir
$TaskTrigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$TaskSettings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$TaskPrincipal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$ExistingTask = Get-ScheduledTask -TaskName 'PCAM5 Monitor' -ErrorAction SilentlyContinue
if ($ExistingTask) { throw 'A tarefa PCAM5 Monitor já existe. Edite-a no Agendador ou remova-a explicitamente antes de registrar outra.' }
Register-ScheduledTask -TaskName 'PCAM5 Monitor' -Action $TaskAction -Trigger $TaskTrigger -Settings $TaskSettings -Principal $TaskPrincipal -Description 'Monitor local de ofertas AM5; consulta a cada 2 minutos.' | Out-Null
Write-Host 'Tarefa registrada para o login deste usuário. Para iniciar agora: Start-ScheduledTask -TaskName "PCAM5 Monitor"'
