<#
.SYNOPSIS
    Haelt die netsh-Portweiterleitung fuer den Hyper-V NetApp Backup
    Container synchron mit der aktuellen WSL2-Guest-IP.

.DESCRIPTION
    Im WSL2-NAT-Netzwerkmodus (Standard, siehe DEPLOYMENT.md Abschnitt
    "Externe Erreichbarkeit") bekommt die WSL2-Distribution bei jedem
    'wsl --shutdown' oder Windows-Neustart eine NEUE IP-Adresse. Eine einmal
    gesetzte 'netsh interface portproxy'-Regel zeigt danach ins Leere, die
    GUI ist von aussen nicht mehr erreichbar, obwohl der Container selbst
    laeuft.

    Dieses Skript ermittelt die aktuelle IP der angegebenen Distribution,
    vergleicht sie mit der bestehenden Portproxy-Regel und setzt die Regel
    nur neu, wenn sich die IP tatsaechlich geaendert hat (idempotent -- ein
    wiederholter Aufruf ohne IP-Aenderung tut nichts).

    Empfohlen als wiederkehrender Scheduled Task (siehe Registrierungs-
    Snippet in DEPLOYMENT.md) -- sowohl beim Windows-Start als auch in
    kurzen Intervallen, da ein reines 'wsl --shutdown' ohne Windows-Neustart
    ebenfalls eine neue IP zur Folge hat.

.PARAMETER Distro
    Name der WSL2-Distribution, in der der Container laeuft
    (siehe 'wsl -l -v').

.PARAMETER ListenPort
    Port, auf dem Windows nach aussen lauscht (Standard: 8443, passend zum
    Port-Mapping "8443:443" in docker-compose.yml).

.PARAMETER ConnectPort
    Port, auf den innerhalb von WSL2 weitergeleitet wird. In der Standard-
    konfiguration identisch zu ListenPort.

.NOTES
    Nicht live gegen eine echte Windows Server 2025/WSL2-Instanz verifiziert
    -- basiert auf dem manuell verifizierten netsh-Befehlspaar aus
    DEPLOYMENT.md. Vor Produktiveinsatz einmal manuell testen
    (Aufruf ohne Scheduled Task, Ergebnis mit 'netsh interface portproxy
    show v4tov4' pruefen).
#>
param(
    [string]$Distro = "hvnb",
    [int]$ListenPort = 8443,
    [int]$ConnectPort = 8443
)

$ErrorActionPreference = "Stop"

function Get-WslIPv4Address {
    param([string]$DistroName)
    $raw = wsl -d $DistroName -- sh -c "hostname -I 2>/dev/null | awk '{print `$1}'"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }
    return $raw.Trim()
}

$wslIp = Get-WslIPv4Address -DistroName $Distro
if (-not $wslIp) {
    Write-Error "Konnte keine IPv4-Adresse fuer WSL2-Distro '$Distro' ermitteln. Laeuft sie? ('wsl -l -v')"
    exit 1
}

$existingLine = netsh interface portproxy show v4tov4 | Select-String ":$ListenPort\s"
$currentTarget = $null
if ($existingLine) {
    # Ausgabeformat: "Adresse         Port        Adresse         Port"
    #                "0.0.0.0         8443        <ip>            8443"
    $fields = ($existingLine.ToString() -split '\s+') | Where-Object { $_ -ne "" }
    if ($fields.Count -ge 3) { $currentTarget = $fields[2] }
}

if ($currentTarget -eq $wslIp) {
    Write-Output "Portproxy fuer Port $ListenPort zeigt bereits korrekt auf $wslIp -- keine Aenderung noetig."
} else {
    netsh interface portproxy delete v4tov4 listenport=$ListenPort listenaddress=0.0.0.0 | Out-Null
    netsh interface portproxy add v4tov4 listenport=$ListenPort listenaddress=0.0.0.0 connectport=$ConnectPort connectaddress=$wslIp | Out-Null
    Write-Output "Portproxy aktualisiert: 0.0.0.0:$ListenPort -> ${wslIp}:$ConnectPort (vorher: $(if ($currentTarget) { $currentTarget } else { '(keine Regel)' }))"
}

$firewallRuleName = "HVNB HTTPS ($ListenPort)"
if (-not (Get-NetFirewallRule -DisplayName $firewallRuleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $firewallRuleName -Direction Inbound -Protocol TCP -LocalPort $ListenPort -Profile Domain,Private,Public -Action Allow | Out-Null
    Write-Output "Firewall-Regel '$firewallRuleName' angelegt."
}
