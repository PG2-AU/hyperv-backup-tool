"""Erzeugt das PowerShell-Skript, das einen Hyper-V-Host fuer WinRM-HTTPS
einrichtet -- 1:1 an DEPLOYMENT.md Kapitel 10 (Schritte 2-4 + Export)
ausgerichtet. Reiner String-Build, es wird nichts ausgefuehrt.

Die drei Formularwerte aus der GUI (Cluster-DNS-Name, Cluster-IP, Host-IP)
werden hart in den Variablen-Kopf eingesetzt; `$hostname` bleibt bewusst
im Skript selbst berechnet (der eigene FQDN des jeweiligen Knotens).
"""

from app.schemas.winrm_cert import SetupScriptRequest

# .inf-Vorlage fuer `certreq -new` -- erzeugt ein selbstsigniertes
# Maschinenzertifikat mit ECHTEN IP-Address-SAN-Eintraegen (New-Self...
# schreibt IP-Literale faelschlich als DNS-Typ, siehe DEPLOYMENT.md).
# Platzhalter __SAN_LINES__ wird durch die konditionalen _continue_-Zeilen
# ersetzt. Bewusst KEIN f-String (die Vorlage enthaelt {text} und $-Refs).
_INF_TEMPLATE = '''@"
[Version]
Signature="`$Windows NT`$"

[NewRequest]
Subject = "CN=$hostname"
KeySpec = 1
KeyLength = 2048
Exportable = TRUE
MachineKeySet = TRUE
SMIME = FALSE
PrivateKeyArchive = FALSE
UserProtected = FALSE
UseExistingKeySet = FALSE
ProviderName = "Microsoft RSA SChannel Cryptographic Provider"
ProviderType = 12
RequestType = Cert
KeyUsage = 0xa0
ValidityPeriod = Years
ValidityPeriodUnits = 5

[Extensions]
2.5.29.17 = "{text}"
__SAN_LINES__

[EnhancedKeyUsageExtension]
OID=1.3.6.1.5.5.7.3.1
"@ | Set-Content -Path $infPath -Encoding ASCII'''

_LISTENER_BLOCK = r'''# --- 2. HTTPS-Listener pruefen / anlegen ---------------------------------
#     Ein vorhandener HTTPS-Listener wird NICHT blind entfernt: ist er schon
#     an das soeben erzeugte Zertifikat gebunden, bleibt alles unveraendert;
#     ist ein anderes Zertifikat gebunden, wird das vorher mit Details
#     ausgegeben und nur dann ersetzt.
$httpsListener = Get-ChildItem WSMan:\localhost\Listener |
    Where-Object { $_.Keys -match "Transport=HTTPS" }
if ($httpsListener) {
    $listenerPath = "WSMan:\localhost\Listener\$($httpsListener.Name)"
    $boundThumb = (Get-Item "$listenerPath\CertificateThumbprint" -ErrorAction SilentlyContinue).Value
    Write-Host "Vorhandener HTTPS-Listener: gebundenes Zertifikat $boundThumb"
    if ($boundThumb -eq $cert.Thumbprint) {
        Write-Host "  -> bereits an das neue Zertifikat gebunden, nichts zu tun."
    } else {
        $boundCert = Get-ChildItem Cert:\LocalMachine\My |
            Where-Object { $_.Thumbprint -eq $boundThumb }
        if ($boundCert) {
            Write-Warning "  -> anderes Zertifikat gebunden (Subject $($boundCert.Subject), gueltig bis $($boundCert.NotAfter))."
        } else {
            Write-Warning "  -> gebundenes Zertifikat nicht (mehr) im Store auffindbar."
        }
        Write-Warning "  -> Listener wird jetzt auf das neue Zertifikat umgestellt."
        Remove-Item -Path $listenerPath -Recurse -Force
        New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
            -CertificateThumbprint $cert.Thumbprint -Force | Out-Null
        Write-Host "  -> neu gebunden an $($cert.Thumbprint)."
    }
} else {
    New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
        -CertificateThumbprint $cert.Thumbprint -Force | Out-Null
    Write-Host "HTTPS-Listener angelegt, gebunden an $($cert.Thumbprint)."
}
Write-Host "`n--- winrm enumerate winrm/config/listener ---"
winrm enumerate winrm/config/listener'''

_FIREWALL_BLOCK = r'''# --- 3. Firewall ----------------------------------------------------------
Enable-NetFirewallRule -DisplayGroup "Windows Remote Management"
if (-not (Get-NetFirewallRule -DisplayName "WinRM HTTPS (5986)" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "WinRM HTTPS (5986)" -Direction Inbound `
        -Protocol TCP -LocalPort 5986 -Action Allow | Out-Null
    Write-Host "Firewall-Regel 'WinRM HTTPS (5986)' angelegt."
} else {
    Write-Host "Firewall-Regel 'WinRM HTTPS (5986)' existiert bereits."
}'''

_CREDSSP_BLOCK = r'''# --- 4. CredSSP (nur noetig bei HVNB_WINRM_TRANSPORT=credssp) --------------
#     Vorher pruefen -- ist die Server-Rolle schon aktiv, wird nichts gesetzt.
$credsspStatus = Get-WSManCredSSP
if ($credsspStatus -match "This computer is configured to receive credentials") {
    Write-Host "CredSSP-Server bereits aktiv -- uebersprungen."
} else {
    Enable-WSManCredSSP -Role Server -Force | Out-Null
    Write-Host "CredSSP-Server aktiviert."
}'''

_EXPORT_BLOCK = r'''# --- 5. Zertifikat exportieren -------------------------------------------
#     Die erzeugte .pem anschliessend in der GUI unter
#     Settings > WinRM-Zertifikate hochladen (eine Datei pro Knoten).
$cerOut = "C:\temp\winrm-$($env:COMPUTERNAME).cer"
$pemOut = "C:\temp\winrm-$($env:COMPUTERNAME).pem"
Export-Certificate -Cert $cert -FilePath $cerOut | Out-Null
certutil -encode $cerOut $pemOut | Out-Null
Write-Host "`nZertifikat exportiert: $pemOut"
Write-Host "--- enthaltene IP-Address-SANs (Kontrolle) ---"
certutil -dump $cerOut | Select-String "IP Address"'''


def _san_lines(req: SetupScriptRequest) -> list[str]:
    is_cluster = req.cluster_type == "failover_cluster"
    lines = ['_continue_ = "dns=$hostname&"']
    if is_cluster:
        lines.append('_continue_ = "dns=$cnoHostname&"')
    if (req.own_ip or "").strip():
        lines.append('_continue_ = "ipaddress=$ownIp&"')
    if is_cluster:
        lines.append('_continue_ = "ipaddress=$cnoIp&"')
    return lines


def build_setup_script(req: SetupScriptRequest) -> str:
    is_cluster = req.cluster_type == "failover_cluster"
    cno_hostname = (req.cno_hostname or "").strip()
    cno_ip = (req.cno_ip or "").strip()
    own_ip = (req.own_ip or "").strip()

    head: list[str] = [
        "# =======================================================================",
        "#  WinRM-HTTPS auf einem Hyper-V-Host einrichten",
        "#  (entspricht docs/DEPLOYMENT.md Kapitel 10, Schritte 2-4)",
        "#  Als Administrator auf dem Host ausfuehren.",
    ]
    if is_cluster:
        head.append("#  Auf JEDEM Clusterknoten wiederholen -- $ownIp je Knoten anpassen!")
    head += [
        "# =======================================================================",
        "",
        "$ErrorActionPreference = 'Stop'",
        "$hostname = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName",
    ]
    if is_cluster:
        head.append(f'$cnoHostname = "{cno_hostname}"')
        head.append(f'$cnoIp       = "{cno_ip}"')
    if own_ip:
        head.append(f'$ownIp       = "{own_ip}"   # <-- pro Knoten anpassen')
    head.append('Write-Host "Host-FQDN: $hostname"')

    inf = _INF_TEMPLATE.replace("__SAN_LINES__", "\n".join(_san_lines(req)))

    cert_block = "\n".join(
        [
            "# --- 1. Zertifikat erzeugen (certreq/.inf -> echte IP-Address-SANs) ------",
            "New-Item -ItemType Directory -Path C:\\temp -Force | Out-Null",
            '$infPath = "C:\\temp\\winrm-cert.inf"',
            '$cerPath = "C:\\temp\\winrm-cert-req.cer"',
            "",
            inf,
            "",
            "certreq -new $infPath $cerPath | Out-Null",
            "$cert = Get-ChildItem Cert:\\LocalMachine\\My |",
            '    Where-Object { $_.Subject -eq "CN=$hostname" } |',
            "    Sort-Object NotBefore -Descending | Select-Object -First 1",
            'if (-not $cert) { throw "Zertifikat wurde nicht erzeugt/gefunden." }',
            'Write-Host "Thumbprint: $($cert.Thumbprint)"',
        ]
    )

    return "\n\n".join(
        [
            "\n".join(head),
            cert_block,
            _LISTENER_BLOCK,
            _FIREWALL_BLOCK,
            _CREDSSP_BLOCK,
            _EXPORT_BLOCK,
            'Write-Host "`nFertig. Danach die Datei C:\\temp\\winrm-<host>.pem in der GUI hochladen."',
        ]
    )
