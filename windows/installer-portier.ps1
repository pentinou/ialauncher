# IA Launcher installé dans WSL : tâche planifiée Windows qui démarre WSL et le portier
# (ondemand.py) avec le PC, même sans ouvrir de session (utile après un réveil à distance).
# Le portier lance le launcher à la première visite et l'arrête après 10 min sans usage.
#
# Le plus simple, depuis le dossier ialauncher DANS WSL (Windows demande l'accord administrateur) :
#   powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w windows/installer-portier.ps1)" -Dossier "$PWD"
# Portail sur une autre machine : ajouter  -Autorisees ADRESSE_DU_PORTAIL  (le port est alors ouvert
# dans le pare-feu de WSL ; WSL doit être en mode réseau « mirrored », vérifié plus bas).
#
# Enlever : Unregister-ScheduledTask -TaskName "IA Launcher - portier" -Confirm:$false
#           Remove-NetFirewallHyperVRule -Name "IA-Launcher-portier"

param(
    [Parameter(Mandatory = $true)][string]$Dossier,   # dossier ialauncher dans WSL (chemin Linux, ex. /home/moi/ialauncher)
    [string]$Autorisees = "",      # adresse(s) du portail, séparées par des virgules ; vide = portail sur ce PC
    [string]$Distro = "",          # vide = distribution WSL par défaut
    [string]$Utilisateur = "",     # utilisateur Linux ; vide = celui par défaut de la distribution
    [int]$Port = 8765,
    [int]$Idle = 600,              # secondes sans usage avant l'arrêt du launcher ; 0 = jamais
    [string]$Compte = $env:USERNAME   # compte Windows qui exécute la tâche (gardé à travers l'élévation)
)
$ErrorActionPreference = "Stop"

# Droits administrateur : le script se relance lui-même via l'invite Windows (UAC).
# Copie locale d'abord : un chemin \\wsl.localhost\… n'est pas toujours lisible une fois élevé.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    $copie = Join-Path $env:TEMP "ialauncher-installer-portier.ps1"
    Copy-Item -LiteralPath $PSCommandPath -Destination $copie -Force
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-File", "`"$copie`"",
                   "-Dossier", "`"$Dossier`"", "-Port", $Port, "-Idle", $Idle, "-Compte", "`"$Compte`"")
    if ($Autorisees) { $arguments += @("-Autorisees", "`"$Autorisees`"") }
    if ($Distro) { $arguments += @("-Distro", "`"$Distro`"") }
    if ($Utilisateur) { $arguments += @("-Utilisateur", "`"$Utilisateur`"") }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $arguments
    Write-Host "Suite dans la fenêtre administrateur qui vient de s'ouvrir."
    exit
}

$nom = "IA Launcher - portier"
$allow = "127.0.0.1" + $(if ($Autorisees) { "," + $Autorisees.Replace(" ", "") } else { "" })
$cible = @()                       # distribution et utilisateur WSL
if ($Distro) { $cible += @("-d", $Distro) }
if ($Utilisateur) { $cible += @("-u", $Utilisateur) }
$wsl = $cible + @("--cd", "`"$Dossier`"", "--", "python3", "ondemand.py", "--port", $Port, "--idle", $Idle, "--allow", $allow)

# Le portier tourne au premier plan : tant qu'il vit, WSL reste allumé.
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\wsl.exe" -Argument ($wsl -join " ")
$declencheurs = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn -User $Compte)   # au cas où le démarrage sans session échoue
)
# S4U : s'exécute même sans session ouverte, sans stocker de mot de passe
$principal = New-ScheduledTaskPrincipal -UserId $Compte -LogonType S4U -RunLevel Limited
$reglages = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

# une version déjà installée tourne peut-être : on l'arrête pour lancer la nouvelle.
# Arrêter la tâche ne coupe que wsl.exe : le portier et le launcher sont arrêtés dans WSL
# (SIGTERM : le launcher arrête aussi ses serveurs), sinon l'ancien launcher garde le port.
Stop-ScheduledTask -TaskName $nom -ErrorAction SilentlyContinue
$arret = "pkill -f '[o]ndemand\.py --port $Port '; pkill -f '[i]alauncher\.py --port 8770 --no-browser'; " +
         "for i in `$(seq 60); do pgrep -f '[i]alauncher\.py --port 8770 --no-browser' >/dev/null || exit 0; sleep 1; done"
& "$env:SystemRoot\System32\wsl.exe" @cible -e sh -c $arret
Register-ScheduledTask -TaskName $nom -Action $action -Trigger $declencheurs -Principal $principal `
    -Settings $reglages -Description "Portier à la demande d'IA Launcher (WSL, port $Port)" -Force | Out-Null

if ($Autorisees) {
    # Portail sur une autre machine : WSL doit partager l'adresse du PC (mode « mirrored ») …
    $conf = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".wslconfig"
    if (-not ((Test-Path $conf) -and (Get-Content -Raw $conf) -match "(?m)^\s*networkingMode\s*=\s*mirrored")) {
        Write-Warning ("WSL n'est pas en mode réseau « mirrored » : le portail ne pourra pas joindre le launcher.`n" +
            "  Ajoutez dans $conf :`n    [wsl2]`n    networkingMode=mirrored`n" +
            "  puis lancez « wsl --shutdown » et relancez ce script (Windows 11 22H2 ou plus récent).")
    }
    # … et le pare-feu de WSL doit laisser entrer le port (le portier, lui, filtre les adresses)
    Remove-NetFirewallHyperVRule -Name "IA-Launcher-portier" -ErrorAction SilentlyContinue
    New-NetFirewallHyperVRule -Name "IA-Launcher-portier" -DisplayName "IA Launcher (portier, port $Port)" `
        -Direction Inbound -VMCreatorId "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}" -Protocol TCP `
        -LocalPorts $Port -Action Allow | Out-Null
    Write-Host "Pare-feu de WSL : port $Port ouvert."
}

Start-ScheduledTask -TaskName $nom
Start-Sleep -Seconds 8
Get-ScheduledTask -TaskName $nom | Select-Object TaskName, State | Format-Table -AutoSize
try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 "http://127.0.0.1:$Port/"
    Write-Host "OK : http://127.0.0.1:$Port répond (code $($r.StatusCode)). Adresses acceptées : $allow"
} catch {
    Write-Warning "http://127.0.0.1:$Port ne répond pas encore : vérifiez -Dossier ($Dossier), puis réessayez dans une minute."
}
