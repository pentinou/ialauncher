# Tâche planifiée Windows : démarre WSL et le portier d'IA Launcher (ondemand.py) au
# démarrage du PC, SANS ouverture de session (utile après un réveil à distance).
# À lancer dans PowerShell en ADMINISTRATEUR :
#     powershell -ExecutionPolicy Bypass -File installer-portier.ps1
# Pour l'enlever : Unregister-ScheduledTask -TaskName "IA Launcher - portier" -Confirm:$false

param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$Utilisateur = "pentinou",          # utilisateur Linux dans WSL
    [string]$Dossier = "/home/pentinou/ialauncher-portier",  # dossier de travail git sur la branche yunohost
    # adresses acceptées par le portier : le serveur YunoHost et le PC lui-même
    [string]$Autorisees = "192.168.1.179,127.0.0.1,192.168.1.20"
)

$nom = "IA Launcher - portier"
# Le portier tourne au premier plan : tant qu'il vit, WSL reste allumé.
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wsl.exe" `
    -Argument "-d $Distro -u $Utilisateur --cd $Dossier -- python3 ondemand.py --allow $Autorisees"
$declencheurs = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME)   # au cas où le démarrage sans session échoue
)
# S4U : s'exécute même sans session ouverte, sans stocker de mot de passe
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# une version déjà installée tourne peut-être : on l'arrête pour lancer la nouvelle
Stop-ScheduledTask -TaskName $nom -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $nom -Action $action -Trigger $declencheurs -Principal $principal `
    -Settings $reglages -Description "Portier à la demande d'IA Launcher (WSL $Distro, port 8765)" -Force | Out-Null
Start-ScheduledTask -TaskName $nom
Start-Sleep -Seconds 8
Get-ScheduledTask -TaskName $nom | Select-Object TaskName, State | Format-Table -AutoSize
Write-Host "Test : http://localhost:8765 doit afficher IA Launcher (démarrage en quelques secondes)."
