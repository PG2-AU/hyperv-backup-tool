# Installation & Deployment

Diese Anleitung beschreibt alle Schritte von einem frischen, domain-gejointen
**Windows Server 2025** bis zur lauffähigen Applikation. Sie basiert auf einer
real gegen eine solche Umgebung verifizierten Ersteinrichtung (siehe
[INSTALL.md](INSTALL.md) für das dazugehörige Feldprotokoll mit allen dabei
gefundenen Stolpersteinen) und fasst diese Erkenntnisse zu einer
allgemeingültigen, wiederholbaren Anleitung zusammen.

## Architekturüberblick

Die Applikation läuft nicht nativ unter Windows, sondern als **rootless
Podman-Container innerhalb von WSL2** auf dem Windows Server. Das
Container-Image (Rocky Linux 9 Basis) enthält nur die Runtime (Python,
Node.js, nginx, git, supervisord) — der eigentliche Anwendungscode wird beim
Containerstart per `git pull` aus einem konfigurierten Repository geladen,
Backend- und Frontend-Abhängigkeiten installiert und das Frontend gebaut
(siehe `docker/entrypoint.sh`). `supervisord` betreibt darin drei Prozesse:

- **uvicorn** — FastAPI-Backend, lauscht intern auf `127.0.0.1:8000`
- **nginx** — terminiert TLS, liefert das gebaute Frontend aus, reverse-proxyt
  `/api/*` auf uvicorn
- **updater** — optional, prüft periodisch auf neue Commits und deployt sie
  automatisch nach (`HVNB_AUTO_UPDATE_ENABLED=true`)

Warum WSL2 statt einer nativen Windows-Installation: Podman/systemd/nginx
sind Linux-Werkzeuge, die App selbst muss aber auf einem Windows-Host laufen
können, der per WinRM mit den Hyper-V-Clusterknoten und dem Restore-Proxy-
Host spricht — WSL2 bringt beides auf eine Maschine.

## 1. Voraussetzungen

- [ ] Windows Server 2025, Mitglied der Domäne, lokale Administratorrechte
      auf dem Server
- [ ] Ausgehender Netzwerkzugriff von diesem Server zu:
  - dem NetApp-ONTAP-Cluster-Management-LIF (TCP 443, HTTPS/REST)
  - allen zu verwaltenden Hyper-V-Clusterknoten sowie dem geplanten
    Restore-Proxy-Host (WinRM — TCP 5986 bei HTTPS/Default, TCP 5985 bei
    HTTP)
  - dem Domain Controller, falls Active-Directory-Login aktiviert werden
    soll (LDAP/LDAPS — TCP 389 bzw. 636)
  - dem Git-Server/-Repository, aus dem der Anwendungscode bezogen wird
    (siehe Abschnitt 4 zu den zwei möglichen Modellen, falls kein direkter
    Netzwerkpfad besteht)
- [ ] Eingehender Netzwerkzugriff auf den gewählten HTTPS-Port (Beispiel in
      dieser Anleitung: 8443) von den Rechnern/Netzen, aus denen die Web-GUI
      erreichbar sein soll
- [ ] Ein NetApp-Cluster und mindestens ein Hyper-V-Cluster sind bereits
      grundsätzlich erreichbar — beide werden **nicht** in dieser Anleitung,
      sondern nach dem ersten Login komplett über die Web-GUI eingerichtet
      (siehe Abschnitt 10)

### WinRM auf jedem Hyper-V-Host aktivieren

Die Applikation spricht mit den Hyper-V-Clusterknoten ausschließlich per
WinRM/PowerShell-Remoting (`winrm.Session`, siehe
`backend/app/services/hyperv_service.py`) — nie per SMB oder RPC direkt.
Ohne einen laufenden, erreichbaren WinRM-HTTPS-Listener lässt sich der
Cluster in **Settings > Hyper-V-Hosts** nicht hinzufügen; ein typischer
Fehler dabei: `Host '<IP>' ist auf Port 5986 nicht erreichbar: timed out`
— das ist ein reiner TCP-Verbindungsfehler, tritt also auf, **bevor**
überhaupt Zugangsdaten geprüft werden (Listener fehlt, Firewall blockiert,
oder Netzwerkpfad/VLAN-Trennung).

**Auf JEDEM Clusterknoten** (nicht nur einem — welcher Knoten gerade den
Cluster Name Object (CNO) besitzt, kann wechseln), als Administrator:

```powershell
# WinRM-Dienst aktivieren (meist bereits per Default aktiv)
Enable-PSRemoting -Force

Get-ChildItem -Path Cert:\LocalMachine\My
```

Ein Failover-Cluster legt dort bereits automatisch erzeugte Zertifikate an
(z.B. `CN=<GUID>.TLS` oder `CN=CLIUSR`) — die sind **nicht** geeignet, das
sind interne Cluster-Kommunikations-/Dienstkonto-Zertifikate, keine
Host-Zertifikate für einen WinRM-Listener.

**Wichtig bei einem Failover-Cluster:** Wird der Cluster in der GUI über
die Adresse des **Cluster Name Object (CNO)** angesprochen (empfohlen,
statt eines einzelnen physischen Knotens — sonst fällt die Verwaltung
beim Ausfall/Failover dieses einen Knotens komplett aus), landet jede
WinRM-Verbindung bei genau dem Knoten, der die Cluster-Group gerade
besitzt — je nach Failover-Status kann das **jeder** der Knoten sein. Das
Zertifikat **jedes einzelnen** Knotens muss deshalb zusätzlich zur eigenen
Identität auch die CNO-Adresse als Subject Alternative Name (SAN)
enthalten, sonst schlägt die Verbindung fehl, sobald die Cluster-Group auf
einen anderen Knoten wechselt. Beispiel: CNO `10.93.70.110`, Knoten
`10.93.70.111`/`10.93.70.112` — **beide** Knoten-Zertifikate brauchen
`.110` zusätzlich zur eigenen Adresse als SAN.

**Stolperstein bei Verbindung per IP-Adresse:**
`New-SelfSignedCertificate -DnsName <ip-literal>` schreibt eine IP-Adresse
als **DNS-Typ**-SAN-Eintrag (`DNS:10.93.70.110`), nicht als
**IP-Address-Typ**-Eintrag. Die von dieser App verwendete TLS-Validierung
(Python/OpenSSL) akzeptiert für eine Verbindung per IP-Adresse aber
ausschließlich echte `IP Address:`-Einträge — ein optisch identischer
`DNS:`-Eintrag genügt **nicht** und führt zu
`SSLCertVerificationError: IP address mismatch`, obwohl die IP scheinbar
korrekt im Zertifikat steht (live verifiziert). Wird der Cluster in der
GUI stattdessen per **Hostname** angesprochen, tritt das Problem nicht auf
— dann genügt `New-SelfSignedCertificate -DnsName $hostname,
$cnoHostname`. Bei Verbindung per IP-Adresse (z.B. weil für den CNO kein
DNS-Eintrag existiert) muss stattdessen `certreq` mit einer `.inf`-Datei
verwendet werden, die echte `ipaddress=`-SAN-Einträge erzeugt:

```powershell
# Auf JEDEM Knoten einzeln ausfuehren, jeweils mit der eigenen $ownIp:
$hostname = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName
$ownIp    = "10.93.70.111"   # auf dem jeweils anderen Knoten: 10.93.70.112 usw.
$cnoIp    = "10.93.70.110"   # IP/Hostname des Cluster Name Object

New-Item -ItemType Directory -Path C:\temp -Force | Out-Null
$infPath = "C:\temp\winrm-cert.inf"
$cerPath = "C:\temp\winrm-cert.cer"

@"
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
_continue_ = "dns=$hostname&"
_continue_ = "ipaddress=$ownIp&"
_continue_ = "ipaddress=$cnoIp&"

[EnhancedKeyUsageExtension]
OID=1.3.6.1.5.5.7.3.1
"@ | Set-Content -Path $infPath -Encoding ASCII

certreq -new $infPath $cerPath
$cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Subject -eq "CN=$hostname" } |
    Sort-Object NotBefore -Descending | Select-Object -First 1
$cert.Thumbprint
```

(Kein Failover-Cluster, oder Verbindung per Hostname statt IP: einfacher
via `$cert = New-SelfSignedCertificate -DnsName $hostname, $cnoHostname
-CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(5)`
— dann direkt mit dem Listener-Schritt unten weitermachen.)

Von der internen PKI ausgestellte Zertifikate sind gegenüber beiden
selbstsignierten Varianten vorzuziehen (siehe Kasten weiter unten) —
solange auch sie sowohl die eigene Knoten-Identität als auch die
CNO-Adresse als SAN tragen.

**Listener einrichten** — falls bereits einer existiert (z.B. von einem
vorherigen Versuch mit falschem Zertifikat), erst entfernen:

```powershell
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -match "Transport=HTTPS" } |
    Remove-Item -Recurse -Force
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
    -CertificateThumbprint $cert.Thumbprint -Force

# Eingehende WinRM-HTTPS-Verbindungen zulassen
Enable-NetFirewallRule -DisplayGroup "Windows Remote Management"
New-NetFirewallRule -DisplayName "WinRM HTTPS (5986)" -Direction Inbound -Protocol TCP -LocalPort 5986 -Action Allow

# Nur noetig, wenn HVNB_WINRM_TRANSPORT=credssp (der in dieser App
# empfohlene Standard, siehe .env-Beispiel in Abschnitt 5 -- CredSSP
# vermeidet das klassische WinRM-"Double-Hop"-Problem, falls ein
# Remote-Befehl seinerseits auf ein weiteres Netzwerkziel zugreifen muss):
Enable-WSManCredSSP -Role Server
```

**Zertifikat-Vertrauen im Container einrichten:** die App validiert das
WinRM-Zertifikat bei HTTPS strikt (`server_cert_validation="validate"`,
siehe `hyperv_service.py`) — der Container kennt eine interne CA oder ein
selbstsigniertes Zertifikat aber standardmäßig nicht, die Verbindung
schlägt sonst trotz korrekt eingerichtetem Listener fehl. Lösung:
`HVNB_WINRM_CA_TRUST_PATH` auf eine PEM-Datei zeigen lassen, die das
Zertifikat als zusätzlich vertrauenswürdig hinterlegt (pywinrm
`ca_trust_path`, additiv zum normalen System-Truststore).

Bei einer internen CA reicht es, einmalig nur den CA-ROOT zu exportieren
— das deckt dann automatisch ALLE damit ausgestellten Knoten-Zertifikate
ab, statt jeden Knoten einzeln zu pflegen. Bei selbstsignierten
Zertifikaten muss dagegen **jeder Knoten** sein eigenes (öffentliches!)
Zertifikat exportieren.

**Stolperstein bei `certreq`:** `$cerPath` (die beim `certreq -new`-Aufruf
oben angegebene Ausgabedatei) **nicht** direkt für `certutil -encode`
verwenden — live beobachtet, dass diese Datei statt des fertigen
Zertifikats eine unfertige Zertifikatsanforderung (CSR) enthielt, obwohl
das Zertifikat selbst korrekt im Speicher erzeugt und am Listener
gebunden wurde. `certutil -encode` darauf angewendet erzeugt eine
strukturell gültig aussehende, aber für OpenSSL unlesbare PEM-Datei
(`SSLError: [X509] PEM lib`), und zwar erst beim nächsten
Verbindungsversuch sichtbar, nicht beim Export selbst. Immer stattdessen
frisch aus dem tatsächlich installierten Zertifikatsobjekt exportieren —
unabhängig davon, ob es per `certreq` oder `New-SelfSignedCertificate`
erzeugt wurde (`$cert` kommt aus dem `Get-ChildItem`-Lookup weiter oben):

```powershell
Export-Certificate -Cert $cert -FilePath C:\temp\winrm-host111.cer
certutil -encode C:\temp\winrm-host111.cer C:\temp\winrm-host111.pem

# Vor dem Uebertragen IMMER lokal verifizieren, dass die Datei ein
# echtes Zertifikat mit der erwarteten SAN enthaelt -- haette beide
# oben beschriebenen Fehlerbilder (DNS- statt IP-Typ, kaputte PEM aus
# certreq) sofort hier erkannt, statt erst beim fehlgeschlagenen
# Verbindungsversuch:
certutil -dump C:\temp\winrm-host111.cer | Select-String "10.93.70"
```

Erwartete Ausgabe: `IP Address=10.93.70.111` (eigene Adresse) und
`IP Address=10.93.70.110` (CNO-Adresse) — als `IP Address=`, nicht als
reiner Text im DNS-Feld.

.pem-Datei(en) per RDP-Dateitransfer o.ae. auf den WSL2-Host übertragen.

`/etc/hvnb/certs` im Container ist bereits ein **persistentes benanntes
Volume** (`hvnb-certs`, dort liegt auch schon das TLS-Zertifikat der GUI)
— dafür ist also keine zusätzliche Zeile in `docker-compose.yml` nötig,
die Datei kann direkt per `podman cp` in den laufenden Container gelegt
werden:

```bash
# Auf dem WSL2-Host: Datei(en) z.B. per Windows-Explorer unter
# \\wsl.localhost\<Distro-Name>\home\<Benutzer>\ ablegen, dann:
podman cp ~/winrm-ca.pem hvnb-backup:/etc/hvnb/certs/winrm-ca.pem
```

```bash
# .env, im Projektverzeichnis:
echo "HVNB_WINRM_CA_TRUST_PATH=/etc/hvnb/certs/winrm-ca.pem" >> .env
systemctl --user restart hvnb-backup.service
```

Der Container-Betrieb läuft seit Abschnitt 6 über eine Quadlet-Unit, die bei
jedem `restart` unbedingt neu erstellt wird (kein `--force-recreate`-
Sonderfall mehr nötig, siehe Kasten dort) — `.env`-Änderungen wie diese
kommen dadurch zuverlässig an.

Da `hvnb-certs` ein benanntes Volume ist, übersteht die Datei den
Container-Neustart in Schritt 2 unabhängig von der Reihenfolge der beiden
Befehle. Settings > Updates zeigt anschließend unter "WinRM
CA-Trust-Datei" den konfigurierten Pfad zur Kontrolle an.

**Mehrere Knoten mit jeweils eigenem, selbstsigniertem Zertifikat** (kein
gemeinsamer CA-Root): `HVNB_WINRM_CA_TRUST_PATH` zeigt auf genau EINEN
Pfad — die einzelnen PEMs müssen daher vorher zu einer einzigen
Bundle-Datei zusammengefügt werden (eine PEM-Datei kann beliebig viele
aneinandergehängte Zertifikate enthalten, genau wie ein öffentliches
CA-Bundle). Die obigen Befehle also **nicht** pro Host wiederholen
(überschreibt sonst jedes Mal die vorherige Datei) — stattdessen einmalig:

```bash
# Alle einzeln uebertragenen Host-PEMs in einem Ordner sammeln, z.B.
# ~/winrm-certs/host1.pem, host2.pem, ... , dann zu einer Datei
# zusammenfassen:
cat ~/winrm-certs/*.pem > ~/winrm-ca-bundle.pem
podman cp ~/winrm-ca-bundle.pem hvnb-backup:/etc/hvnb/certs/winrm-ca.pem
```

Mit einer internen CA reicht dagegen der eine, einmalig exportierte
CA-Root für alle Knoten — kein Zusammenführen nötig.

**Verifizieren, dass die CNO-Adresse jetzt als echter IP-Address-SAN-Typ
ausgeliefert wird** (nicht als `DNS:`-Eintrag, siehe Stolperstein oben) —
von der WSL2-Distribution aus, gegen die CNO-Adresse selbst:

```bash
openssl s_client -connect 10.93.70.110:5986 -showcerts </dev/null 2>/dev/null | \
    openssl x509 -noout -text | grep -A2 "Subject Alternative Name"
```

Erwartete Ausgabe enthält `IP Address:10.93.70.110` — erscheint
stattdessen `DNS:10.93.70.110`, wurde das Zertifikat auf dem gerade
antwortenden Knoten noch mit `New-SelfSignedCertificate -DnsName
<ip-literal>` statt der `certreq`-Methode oben erzeugt.

Zusätzlich:

- Der verbindende Account (in der GUI beim Hinzufügen des Clusters
  hinterlegt) braucht **lokale Administratorrechte** auf jedem Knoten
  (Details und Begründung im Kasten unten).
- `HVNB_WINRM_TRANSPORT` (Abschnitt 5) muss zum serverseitig aktivierten
  Verfahren passen — `credssp` erfordert exakt den obigen
  `Enable-WSManCredSSP -Role Server`-Schritt, `ntlm` kommt ohne diesen
  Schritt aus (nur Listener + Firewall nötig), unterstützt aber keine
  Double-Hop-Szenarien.

### Das Cluster-Konto: welche Rechte genau, und keine mehr

Der Account, der beim Hinzufügen eines Clusters in der GUI hinterlegt wird,
sollte **nicht** das eingebaute `Administrator`-Konto der Domäne sein (in
Testumgebungen oft bequem der Fall, real angetroffen z. B. als
`HYPERVDEMO\Administrator` mit Mitgliedschaft in Domain Admins, Enterprise
Admins und Schema Admins) — das ist um Größenordnungen mehr Rechteumfang,
als die App tatsächlich braucht, und macht diesen Server bei Kompromittierung
zu einem Sprungbrett für die gesamte Domäne bzw. den gesamten Forest.

> **Warum lokale Administratorrechte trotzdem nötig sind:** die
> "Hyper-V-Administratoren"-Gruppe, die für reines VM-Management ausreichen
> würde, genügt hier nicht. Die App nutzt über WinRM neben reinen
> Hyper-V-Cmdlets (`Get-VM`, `New-VM`, `Checkpoint-VM`, `Add/Remove-VMHardDiskDrive`
> usw. — dafür würde Hyper-V-Administratoren reichen) auch
> Disk-/iSCSI-/Partitions-Cmdlets für den Restore-Workflow (`Connect-IscsiTarget`,
> `Mount-VHD`/`Mount-DiskImage`, `Set-Disk`, `Add/Remove-PartitionAccessPath`)
> sowie Cluster-Abfragen (`Get-ClusterSharedVolume`, `Get-ClusterNode`,
> `Add-ClusterVirtualMachineRole`). Für die Disk-/iSCSI-Verwaltung gibt es unter
> Windows **keine** eigene, schmalere eingebaute Gruppe (anders als bei
> Hyper-V) — diese Cmdlets verlangen lokale Administratorrechte. Volle
> Cluster-Verwaltung ist davon i. d. R. bereits mit abgedeckt, da Failover
> Clustering lokale Administratoren der Knoten standardmäßig als
> Cluster-Administratoren behandelt; im Zweifel nach Einrichtung mit
> `(Get-Cluster).GetAccessAllowed()` bzw. in Failover Cluster Manager unter
> "Cluster-Berechtigungen" verifizieren.

Das eigentliche Least-Privilege-Prinzip liegt also nicht darin, lokale
Adminrechte zu vermeiden (technisch für den Restore-Workflow nicht möglich),
sondern darin, **denselben Rechteumfang auf den kleinstmöglichen
Geltungsbereich zu begrenzen** — konkret:

1. **Dediziertes Konto** anlegen, ausschließlich für diese App, z. B.
   `HYPERVDEMO\svc-hvnb-backup` — kein Personenkonto, kein für andere Zwecke
   mitgenutztes Konto.
2. **Keine** Mitgliedschaft in Domain Admins, Enterprise Admins, Schema
   Admins oder einer sonstigen domänenweit privilegierten Gruppe. Das Konto
   ist ein ganz gewöhnliches Domänenkonto ohne besondere AD-Rechte.
3. Lokale Administratorrechte **nur** auf den tatsächlich verwalteten
   Maschinen — allen Hyper-V-Clusterknoten sowie dem Restore-Proxy-Host —,
   nicht auf sonstigen Servern oder Arbeitsplätzen. Am saubersten über eine
   Sicherheitsgruppe (z. B. `HVNB-Backup-Hosts`) mit genau diesen Rechnern
   als Mitglieder, kombiniert mit einer GPO über **Restricted Groups**
   (Computer-Konfiguration → Richtlinien → Sicherheitseinstellungen →
   Restricted Groups → `Administratoren` → `HYPERVDEMO\svc-hvnb-backup`
   hinzufügen), die per Sicherheitsfilterung nur auf diese Gruppe wirkt.
   Reine manuelle `net localgroup Administratoren /add`-Pflege pro Host
   funktioniert ebenso, ist aber bei mehreren Knoten fehleranfälliger.
4. **Interaktive Anmeldung verweigern**, da das Konto ausschließlich über
   WinRM verwendet wird (GPO: "Anmelden als Batchauftrag verweigern" bzw.
   "Lokal anmelden verweigern" / "Anmelden über Remotedesktopdienste
   verweigern" für dieses Konto auf denselben Zielrechnern) — reduziert den
   Nutzen eines gestohlenen Passworts für alles außer dem WinRM-Zugriff
   selbst, den die App ohnehin schon hat.
5. **Kein gMSA** (Group Managed Service Account): CredSSP übergibt das
   tatsächliche Passwort zur Delegation, und dieses wird der App selbst aus
   ihrer eigenen, hinterlegten Konfiguration übermittelt — ein gMSA verwaltet
   sein Passwort selbst und macht es nicht in dieser Form auslesbar, ist also
   für dieses Zugriffsmuster nicht geeignet. Stattdessen: starkes, für dieses
   eine Konto einzigartiges Passwort, regelmäßig rotiert.
6. Nach Einrichtung verifizieren, dass das Konto tatsächlich **nur** auf den
   vorgesehenen Hosts als Administrator eingetragen ist (`net localgroup
   Administratoren` auf jedem Knoten) und in keiner der drei genannten
   Domain-/Enterprise-/Schema-Admin-Gruppen steckt (`Get-ADUser
   svc-hvnb-backup -Properties MemberOf`).

### Firewall auf die IP des Backup-Hosts einschränken (empfohlen)

Die oben angelegte Firewall-Regel erlaubt WinRM-HTTPS (5986) bislang von
**jeder** erreichbaren Adresse aus — dabei braucht diese Verbindung
niemals mehr als ein einziger Host: der Windows Server, auf dem der
Container läuft. Eine einzige zusätzliche Zeile pro Knoten schließt diese
Lücke, ohne die App in irgendeiner Weise einzuschränken:

```powershell
# Auf JEDEM Hyper-V-Knoten (und dem Restore-Proxy-Host) ausfuehren --
# <Backup-Host-IP> durch die tatsaechliche IP-Adresse des Windows Servers
# ersetzen, auf dem der Container laeuft (nicht die interne WSL2-Guest-IP
# -- siehe Hinweis unten). Mehrere erlaubte Adressen durch Komma trennen.
Set-NetFirewallRule -DisplayName "WinRM HTTPS (5986)" -RemoteAddress <Backup-Host-IP>
```

> **Welche IP-Adresse gehört hier hin:** die IP-Adresse des Windows
> Servers selbst (dieselbe, die z. B. für die GUI unter Abschnitt 8 als
> `<Server-IP>` verwendet wird) — **nicht** die interne, nur
> WSL2-intern gültige und bei jedem Neustart wechselnde Guest-IP. Für
> ausgehende Verbindungen aus WSL2 heraus (wie hier: der Container baut
> die WinRM-Verbindung zum Hyper-V-Host auf) übersetzt Windows die
> Quelladresse ohnehin auf die physische Server-IP, bevor der Datenverkehr
> das Netzwerk verlässt — unabhängig davon, ob NAT- oder Mirrored-Modus
> aktiv ist (Abschnitt 8). Die Server-IP ist stabil; nur sie eignet sich
> hier als dauerhafte Einschränkung.

**Verifizieren:**

- Vom Windows Server aus (bzw. aus der WSL2-Distribution heraus, siehe
  Testbefehl weiter unten): Verbindung funktioniert unverändert.
- Von einem beliebigen anderen Host im Netz: `Test-NetConnection
  -ComputerName <Hyper-V-Host-IP> -Port 5986` liefert jetzt
  `TcpTestSucceeded : False` (Firewall blockiert, statt vorher `True`).

**Bei einem Failover-Cluster** muss dieselbe Einschränkung auf **jedem**
Knoten einzeln gesetzt werden, nicht nur auf dem gerade aktiven — welcher
Knoten eine über die CNO-Adresse aufgebaute Verbindung tatsächlich
bedient, kann jederzeit wechseln (siehe Kasten weiter oben). Die interne
Cluster-Kommunikation zwischen den Knoten (Heartbeat, CSV, Failover) läuft
über eigene Ports/Regeln und ist von dieser Einschränkung nicht betroffen.

**Verbindung isoliert testen**, bevor der Cluster in der GUI hinzugefügt
wird — zuerst lokal auf dem Hyper-V-Host selbst:

```powershell
Test-NetConnection -ComputerName localhost -Port 5986
```

Danach von der WSL2-Distribution aus (dort, wo der Container läuft), um
den tatsächlichen Netzwerkpfad zu prüfen:

```bash
timeout 3 bash -c "echo > /dev/tcp/<Hyper-V-Host-IP>/5986" && echo "erreichbar" || echo "NICHT erreichbar"
```

Schlägt nur der zweite Test fehl (lokal auf dem Host aber funktioniert es):
Firewall oder Netzwerksegmentierung (VLAN) zwischen WSL2-Host und
Hyper-V-Cluster prüfen — genau dieses Muster (Server erreichbar,
aber durch eine VLAN-Trennung vom App-Host aus nicht) trat bereits beim
Code-Bezug in der Referenzumgebung auf, siehe Abschnitt 4c.

## 2. WSL2 aktivieren und Linux-Distribution einrichten

PowerShell als Administrator:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
Restart-Computer
```

Nach dem Neustart eine Distribution installieren. Jede systemd-fähige,
aktuelle Distribution funktioniert — am einfachsten per eingebautem
Ein-Zeiler:

```powershell
wsl --install -d Ubuntu-22.04
```

(Die real verifizierte Referenzumgebung nutzte Rocky Linux 10.2 statt
Ubuntu — das betrifft nur den WSL2-**Host**, nicht das Container-Image
selbst, das unabhängig davon immer auf `rockylinux:9` basiert. Die
folgenden Befehle sind für Rocky/RHEL-artige Distributionen (`dnf`)
formuliert; unter Ubuntu/Debian `apt` statt `dnf` und `openssl`/`git`/
`podman` über die dort üblichen Paketnamen verwenden.)

**Wichtige Voraussetzung für Schritt 6 (Container-Persistenz):** systemd muss
innerhalb der WSL2-Distribution aktiv sein. Prüfen bzw. aktivieren:

```bash
# In der WSL2-Distro:
cat /etc/wsl.conf 2>/dev/null
```

Falls kein `[boot]`-Abschnitt mit `systemd=true` vorhanden ist:

```bash
sudo tee -a /etc/wsl.conf > /dev/null << 'EOF'
[boot]
systemd=true
EOF
```

Danach aus PowerShell einmal `wsl --shutdown` und die Distro neu öffnen,
damit die Änderung greift.

## 3. Basis-Pakete installieren

In der WSL2-Distribution:

```bash
sudo dnf install -y git podman
sudo dnf install -y python3-pip || true
sudo pip3 install podman-compose
```

Verifizieren:

```bash
git --version
podman --version
podman-compose --version
```

## 4. Anwendungscode beziehen

Zwei Ebenen sind hier zu unterscheiden, die leicht durcheinandergeraten:

- **Interaktive Git-Nutzung auf dem Server** (manuell `git clone`/`git pull`
  ausführen, z. B. um nachzuschauen oder das Repo für den ersten
  `podman-compose`-Aufruf lokal zu haben) — läuft über deinen eigenen
  SSH-Zugang (Abschnitt 4a).
- **Der Container selbst** klont/pullt den Anwendungscode unabhängig davon
  bei jedem Start und jedem Auto-Update (`entrypoint.sh`/`updater.sh`,
  gesteuert über `HVNB_GIT_REPO_URL`) — dafür empfiehlt sich bei einem
  privaten Repository **HTTPS mit einem Personal Access Token** (Abschnitt
  4b), da dafür kein SSH-Schlüsselmaterial in den Container gemountet werden
  muss.

### 4a. SSH-Zugriff für interaktive Nutzung auf dem Server einrichten

Auf dem Windows-Server, in der WSL2-Distribution:

```bash
ssh-keygen -t ed25519 -C "<servername>-interactive" -f ~/.ssh/hyperv_backup_github -N ""

cat >> ~/.ssh/config << 'EOF'
Host github-hvnb
    HostName github.com
    User git
    IdentityFile ~/.ssh/hyperv_backup_github
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config

cat ~/.ssh/hyperv_backup_github.pub
```

Den ausgegebenen öffentlichen Schlüssel bei GitHub hinterlegen — entweder
unter dem eigenen Account (Settings > SSH and GPG keys, falls der Server-
Login einer Person zugeordnet ist) oder repo-gebunden als Deploy Key
(Repository > Settings > Deploy keys > Add deploy key, Lesezugriff reicht).

Test und Erstklon:

```bash
ssh -T git@github-hvnb
git clone git@github-hvnb:<ORG>/<REPO>.git ~/hyperv-netapp-backup
cd ~/hyperv-netapp-backup
```

Dieser Checkout liefert die Compose-/Dockerfile-Dateien, um den Container
in Abschnitt 6 überhaupt bauen und starten zu können — er ist **nicht**
dieselbe Quelle, aus der der Container selbst später pullt (das regelt
`HVNB_GIT_REPO_URL`, siehe 4b).

### 4b. HTTPS + Personal Access Token für den Container-eigenen Pull

Empfohlener Weg, damit die Instanz direkt von GitHub aktualisiert, ohne
SSH-Schlüsselmaterial in den Container mounten zu müssen (Container läuft
als eigener, vom Host-SSH-Setup unabhängiger Prozess).

1. GitHub > Settings > Developer settings > Fine-grained personal access
   tokens > Generate new token.
2. **Repository access:** nur auf das eine Repository beschränken (nicht
   "All repositories").
3. **Permissions:** unter "Repository permissions" nur **Contents: Read-only**
   setzen — mehr wird für einen reinen Pull nicht gebraucht.
4. Token erzeugen und den Wert einmalig kopieren (wird danach nicht mehr
   angezeigt).

In der `.env` (Abschnitt 5):

```bash
HVNB_GIT_REPO_URL=https://<GITHUB-BENUTZERNAME>:<TOKEN>@github.com/<ORG>/<REPO>.git
HVNB_GIT_BRANCH=master
HVNB_AUTO_UPDATE_ENABLED=true
```

`entrypoint.sh`/`updater.sh` führen intern nur einen normalen `git clone`/
`git fetch` gegen diese URL aus — Zugangsdaten in der URL eingebettet werden
dabei von Git nativ unterstützt, es ist keine zusätzliche Konfiguration im
Container nötig.

**Wichtig bei einer nachträglichen Änderung von `HVNB_GIT_REPO_URL`**
(z. B. Umstieg von Modell 4c, oder Token-Rotation): `entrypoint.sh` nutzt
diese Variable nur für den **erstmaligen** Klon (`/opt/app/.git` existiert
noch nicht) — ein bereits geklonter Checkout wird bei jedem weiteren Start
per `git fetch origin` aktualisiert, unabhängig von einer geänderten
Umgebungsvariable, da `origin` in der beim ersten Klon geschriebenen
`.git/config` fest verdrahtet ist. `/opt/app` liegt NICHT in einem
persistenten Volume (nur `/data` und `/etc/hvnb/certs`, siehe
`docker-compose.yml`) — der Container muss also tatsächlich **neu
erstellt** werden, damit `/opt/app` leer beginnt und `entrypoint.sh` den
"erstmaligen Klon"-Pfad mit der neuen URL erneut durchläuft.

```bash
systemctl --user restart hvnb-backup.service
```

Ein reines `podman restart hvnb-backup` reicht dafür **nicht** aus (startet
denselben, bereits geklonten Container neu, `/opt/app` bleibt unverändert)
— `systemctl --user restart hvnb-backup.service` dagegen schon: die
Container-Verwaltung läuft seit Abschnitt 6 über eine Quadlet-Unit, deren
generiertes `ExecStart` den Container bei jedem Start bedingungslos per
`--replace --rm` neu erstellt (kein `--force-recreate`-Sonderfall mehr
nötig wie beim vorherigen `podman-compose up -d`, das eine reine
`.env`-Änderung nicht zuverlässig erkannte). Mit `podman inspect
hvnb-backup --format '{{.Created}}'` vor und nach dem Befehl lässt sich
trotzdem prüfen, ob tatsächlich neu erstellt wurde.

Die `.env`-Datei enthält damit ein Secret im Klartext und
ist bereits über `.gitignore` von Commits ausgeschlossen — trotzdem
zusätzlich die Dateiberechtigung einschränken:

```bash
chmod 600 .env
```

**Rotation/Widerruf:** Token bei Bedarf jederzeit unter GitHub > Settings >
Developer settings > Fine-grained tokens widerrufen und durch ein neues
ersetzen (`.env` aktualisieren, danach `systemctl --user restart
hvnb-backup.service` um den Container mit der neuen URL neu zu erstellen).

### 4c. Kein Netzwerkpfad zum Git-Server (abgeschottetes Netz)

Wurde in der Referenzumgebung genau so benötigt: eine VLAN-Trennung
verhinderte jede Verbindung vom Server zum eigentlichen Git-Server. Lösung:
ein lokales Bare-Repository auf dem WSL2-Host, das per
`docker-compose.dev.yml`-Override read-only in den Container gemountet wird
— der Container braucht dann gar keinen externen Netzwerkzugriff für den
Code:

```bash
# Einmalig: Bare-Repo aus einem erreichbaren Uebertragungsweg befuellen,
# z.B. per 'git bundle' + Dateikopie (RDP, USB, ...), wenn auch kein
# Zwischenschritt-Host existiert:
git clone --bare <PFAD-ODER-URL-MIT-ZUGRIFF> ~/hyperv-repo.git
git clone ~/hyperv-repo.git ~/hyperv-netapp-backup
cd ~/hyperv-netapp-backup

podman-compose -f docker-compose.yml -f docker-compose.dev.yml build
```

Danach wie in Abschnitt 6 beschrieben die Quadlet-Datei anlegen, **inklusive**
der dort gezeigten drei zusätzlichen Zeilen für Modell 4c (Bare-Repo-Mount +
`HVNB_GIT_REPO_URL`/`HVNB_GIT_BRANCH`/`HVNB_AUTO_UPDATE_ENABLED`) — diese
stecken normalerweise in `docker-compose.dev.yml`, das aber nur noch beim
Bauen gilt, nicht mehr für den laufenden Betrieb.

`HVNB_GIT_REPO_URL` zeigt dabei auf `file:///srv/git/hyperv-netapp-backup.git`
— das lokale Bare-Repo wird dafür nach `/srv/git/...` in den Container
gemountet.

**Code-Updates in diesem Modell:** das Bare-Repo muss erneut befüllt
werden, z. B. per `git bundle create update.bundle --all` auf einem Host mit
Zugriff, Transfer der Bundle-Datei, und auf dem Server:

```bash
git -C ~/hyperv-repo.git fetch update.bundle 'refs/*:refs/*'
```

Der Updater-Prozess im Container erkennt die neuen Commits beim nächsten
Intervall automatisch (`HVNB_AUTO_UPDATE_ENABLED=true`) bzw. sofort nach
einem manuellen `systemctl --user restart hvnb-backup.service`.

## 5. Konfiguration (`.env`)

```bash
cat > .env << 'EOF'
HVNB_ENVIRONMENT=production
HVNB_SECRET_KEY=<zufaelliger, langer String -- z.B. `openssl rand -hex 32`>
HVNB_INITIAL_ADMIN_PASSWORD=<einmaliges Startpasswort, sofort nach dem ersten Login aendern>

# Nur relevant, falls Active-Directory-Login genutzt werden soll -- sonst
# HVNB_AD_ENABLED=false lassen, lokale Benutzerverwaltung reicht dann aus.
HVNB_AD_ENABLED=false
HVNB_AD_SERVER=dc01.example.local
HVNB_AD_DOMAIN=EXAMPLE
HVNB_AD_BASE_DN=DC=example,DC=local
HVNB_AD_BIND_USER=svc-hvnb-ad
HVNB_AD_BIND_PASSWORD=<Passwort des Bind-Kontos>
HVNB_AD_USE_SSL=true

HVNB_WINRM_TRANSPORT=credssp
HVNB_WINRM_USE_HTTPS=true
HVNB_WINRM_PORT=5986

# Git-basiertes Deployment (siehe Abschnitt 4) -- Beispiel fuer 4b
# (HTTPS + Personal Access Token). Bei Modell 4c (Bare-Repo) wird dieser
# Wert stattdessen automatisch von docker-compose.dev.yml gesetzt.
HVNB_GIT_REPO_URL=https://<GITHUB-BENUTZERNAME>:<TOKEN>@github.com/<ORG>/<REPO>.git
HVNB_GIT_BRANCH=master
HVNB_AUTO_UPDATE_ENABLED=true
HVNB_AUTO_UPDATE_INTERVAL_MINUTES=15
EOF
chmod 600 .env
```

**Wichtig:** NetApp-Cluster, Hyper-V-Hosts, der Restore-Proxy-Host,
SnapMirror-Policies, Backup-Policies, E-Mail-Alerting usw. werden **nicht**
per `.env` konfiguriert, sondern vollständig über die Web-GUI nach dem
ersten Login (Settings, Storage, Restore > Setup, Backup) — dort auch
verschlüsselt in der Datenbank statt im Klartext einer `.env`-Datei
gespeichert.

Die vollständige Liste aller unterstützten Variablen mit Erläuterung steht
in `.env.example` im Projektwurzelverzeichnis.

## 6. Container bauen und starten

`docker-compose.yml` wird ab hier nur noch zum **Bauen** des Images
verwendet — der laufende Betrieb wird über eine **Quadlet**-Unit von
systemd verwaltet (Abschnitt 9 erklärt, warum: rootless Podman hat anders
als Docker keinen Dauer-Daemon, der einen abgestürzten Container von
selbst neu starten würde; `restart: unless-stopped` in der Compose-Datei
greift dafür nicht zuverlässig).

**Image bauen:**

Modell 4b (HTTPS + Token, Regelfall):

```bash
podman-compose -f docker-compose.yml build
```

Modell 4c (lokales Bare-Repo):

```bash
podman-compose -f docker-compose.yml -f docker-compose.dev.yml build
```

**Quadlet-Unit anlegen** (einmalig, unabhängig vom gewählten Modell) unter
`~/.config/containers/systemd/hvnb-backup.container` — Werte (Image-Tag,
Portmapping, Volume-Namen) exakt aus `docker-compose.yml` übernommen, da
Quadlet die Compose-Datei selbst nicht einliest:

```ini
# ~/.config/containers/systemd/hvnb-backup.container
[Unit]
Description=Hyper-V NetApp Backup Tool
After=network-online.target
Wants=network-online.target

[Container]
Image=localhost/hyperv-netapp-backup:local
ContainerName=hvnb-backup
PublishPort=8443:443
EnvironmentFile=%h/hyperv-netapp-backup/.env
Environment=HVNB_DATABASE_URL=sqlite:////data/app.db
Volume=hyperv-netapp-backup_hvnb-data:/data
Volume=hyperv-netapp-backup_hvnb-certs:/etc/hvnb/certs

[Service]
Restart=always
TimeoutStartSec=900

[Install]
WantedBy=default.target
```

Die beiden Volume-Namen sind **projekt-präfigiert** (podman-compose hängt
den Projektnamen — den Namen des Projektverzeichnisses — vor den in
`docker-compose.yml` angegebenen Namen). Falls das Projektverzeichnis
nicht `hyperv-netapp-backup` heißt, oder bei einem bereits laufenden
Compose-Container zur Kontrolle, die tatsächlichen Namen anzeigen:

```bash
podman volume ls --filter name=hvnb
```

Modell 4c braucht zusätzlich den Bare-Repo-Mount und die drei
Umgebungsvariablen aus `docker-compose.dev.yml` — im `[Container]`-Block
ergänzen:

```ini
Volume=%h/hyperv-repo.git:/srv/git/hyperv-netapp-backup.git:ro
Environment=HVNB_GIT_REPO_URL=file:///srv/git/hyperv-netapp-backup.git
Environment=HVNB_GIT_BRANCH=master
Environment=HVNB_AUTO_UPDATE_ENABLED=true
```

**Aktivieren und starten** (Quadlet-Units werden NICHT per `systemctl
enable` aktiviert, sondern automatisch anhand der `[Install]`-Zeile in
`default.target` eingehängt, sobald die Datei existiert):

```bash
systemctl --user daemon-reload
systemctl --user start hvnb-backup.service
```

Prüfen:

```bash
systemctl --user status hvnb-backup.service
podman logs --tail 50 hvnb-backup
```

Erwartet: `uvicorn`, `nginx` (und bei aktiviertem Auto-Update `updater`)
laufen ohne Fehlermeldungen. Der erste Start dauert spürbar länger (Klonen
des Repos, `npm ci`, Frontend-Build).

> **Für jede spätere `.env`-Änderung (oder Änderung an der Quadlet-Datei
> selbst, z. B. Zertifikats-Volume in Abschnitt 7) gilt:** anders als beim
> vorherigen `podman-compose up -d` (das eine reine `.env`-Änderung nicht
> zuverlässig als Grund fürs Neuerstellen erkannte, live bestätigt) baut
> die Quadlet-Unit den Container bei JEDEM Start unbedingt neu auf
> (`--replace --rm` im generierten `ExecStart`, sichtbar per `systemctl
> --user cat hvnb-backup.service`) — ein einfaches
>
> ```bash
> systemctl --user daemon-reload   # nur noetig, wenn sich die .container-Datei selbst aenderte
> systemctl --user restart hvnb-backup.service
> ```
>
> reicht daher jetzt IMMER zuverlässig aus, ganz ohne `--force-recreate`-
> Sonderfall. Die beiden Volumes bleiben davon unberührt (`/data`,
> `/etc/hvnb/certs`), nur der Container selbst wird frisch erstellt.

## 7. TLS-Zertifikat

Beim allerersten Start erzeugt der Container automatisch ein
selbstsigniertes Zertifikat (`docker/gen-selfsigned-cert.sh`), damit die GUI
sofort per HTTPS erreichbar ist. Für den Produktivbetrieb ein von der
internen PKI ausgestelltes Zertifikat einbinden, statt das benannte Volume
`hvnb-certs` zu nutzen — seit Abschnitt 6 nicht mehr in `docker-compose.yml`
(wird nur noch fürs Bauen genutzt), sondern in der Quadlet-Datei
`~/.config/containers/systemd/hvnb-backup.container` die `Volume`-Zeile für
die Zertifikate durch einen Bind-Mount ersetzen:

```ini
Volume=%h/hyperv-netapp-backup/certs:/etc/hvnb/certs # server.crt + server.key ablegen
```

Danach den Container neu erstellen (siehe Kasten in Abschnitt 6):

```bash
systemctl --user daemon-reload
systemctl --user restart hvnb-backup.service
```

## 8. Externe Erreichbarkeit von WSL2 aus

Zwei WSL2-Netzwerkmodi kommen infrage:

### Mirrored Networking (falls auf der Plattform unterstützt)

Der Container ist dann direkt unter der Windows-Host-IP erreichbar, ganz
ohne Portweiterleitung. `.wslconfig` im Windows-Benutzerprofil:

```ini
[wsl2]
networkingMode=mirrored
```

Danach `wsl --shutdown` und neu starten. **Bekannte Einschränkung:** auf
manchen Windows-Server-2025-Builds schlägt die Aktivierung mit
`CreateInstance/CreateVm/ConfigureNetworking/0x803b0015` fehl und WSL2 fällt
komplett netzwerklos zurück (`networkingMode=None`) — in diesem Fall
`.wslconfig` wieder entfernen und den NAT-Modus (unten) verwenden.

### NAT-Modus (Standard, funktioniert immer)

Im Standard-NAT-Modus bekommt die WSL2-Distribution eine eigene, nur
Windows-intern erreichbare IP, die sich bei jedem `wsl --shutdown` oder
Windows-Neustart **ändert**. Eine Portweiterleitung von der Windows-
Server-IP auf die jeweils aktuelle WSL2-Guest-IP ist nötig:

```powershell
New-NetFirewallRule -DisplayName "HVNB HTTPS (8443)" -Direction Inbound -Protocol TCP -LocalPort 8443 -Profile Domain,Private,Public -Action Allow
netsh interface portproxy add v4tov4 listenport=8443 listenaddress=0.0.0.0 connectport=8443 connectaddress=<WSL2-Guest-IP>
```

Da sich die WSL2-Guest-IP bei jedem Neustart ändert, liegt im Repository ein
Skript, das die Regel automatisch aktuell hält:
[`docs/windows/Update-HvnbPortProxy.ps1`](windows/Update-HvnbPortProxy.ps1).
Als wiederkehrende Aufgabe registrieren (PowerShell als Administrator):

```powershell
$scriptPath = "C:\hvnb\Update-HvnbPortProxy.ps1"  # Skript vorher dorthin kopieren

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$atStartup = New-ScheduledTaskTrigger -AtStartup
$atStartup.Delay = "PT2M"   # WSL2-Netzwerk braucht nach dem Boot etwas Zeit
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration ([TimeSpan]::MaxValue)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "HVNB-PortProxy-Refresh" `
    -Action $action -Trigger $atStartup, $periodic -Principal $principal `
    -Description "Haelt die netsh-Portweiterleitung fuer den HVNB-Container synchron mit der WSL2-Guest-IP."
```

Die wiederkehrende 15-Minuten-Ausführung fängt auch den Fall ab, dass jemand
`wsl --shutdown` ausführt, ohne den Windows-Server neu zu starten.

> Das Skript ist nicht live gegen eine echte Windows-Server-2025-Instanz
> verifiziert (nur das darin verwendete `netsh`-Befehlspaar selbst wurde in
> der Referenzumgebung manuell bestätigt) — vor dem produktiven Einsatz
> einmal manuell ausführen und mit `netsh interface portproxy show v4tov4`
> kontrollieren.

Verifizieren von einem externen Host:

```powershell
Test-NetConnection -ComputerName <Server-IP> -Port 8443
```

## 9. Container-Persistenz absichern (rootless Podman + WSL2)

**Hintergrund:** `docker-compose.yml` setzt `restart: unless-stopped`, was
bei **rootless** Podman (kein dauerhafter Root-Daemon wie bei Docker) nicht
zuverlässig durchgesetzt wird. Zwei getrennte Probleme:

1. Ein Windows-Sleep/Ruhezustand oder `wsl --shutdown` kann die
   `systemd --user`-Instanz des Benutzers komplett beenden — ohne die
   folgende Einstellung startet sie beim nächsten Login zwar neu, aber ohne
   den Container automatisch mitzunehmen.
2. **Wichtiger, live bestätigt:** rootless Podman hat KEINEN Dauerprozess,
   der einen laufenden Container fortlaufend überwacht — stürzt der
   Container-Hauptprozess ab oder wird er anderweitig beendet, während die
   `systemd --user`-Instanz selbst durchgehend weiterläuft, kommt er von
   selbst **nicht** wieder hoch. `restart: unless-stopped` in
   `docker-compose.yml` bzw. das früher hier dokumentierte
   `podman-restart.service` decken nur Fall 1 ab (ein einmaliger Check beim
   (Neu-)Start der `systemd --user`-Instanz), nicht Fall 2.

Beide Fälle werden durch dieselbe **Quadlet**-Unit abgedeckt, die bereits in
Abschnitt 6 für den normalen Betrieb angelegt wurde
(`~/.config/containers/systemd/hvnb-backup.container`, `Restart=always` im
`[Service]`-Block) — systemd überwacht den Container darüber laufend
selbst und startet ihn innerhalb von Sekunden neu, unabhängig vom Grund des
Stopps. Zusätzlich nötig, damit die `systemd --user`-Instanz überhaupt
unabhängig von einer aktiven Login-Session existiert:

```bash
# Einmalig, in der WSL2-Distribution:
sudo loginctl enable-linger <benutzername>
```

**Live verifiziert** (2026-09-03): Container per `podman kill hvnb-backup`
hart beendet (simuliert einen Absturz) — systemd hat ihn ohne manuelles
Eingreifen innerhalb weniger Sekunden neu erstellt (`systemctl --user
status hvnb-backup.service` zeigte zwischenzeitlich `activating`, danach
wieder `active`), Datenbank-/Zertifikats-Inhalt unverändert (liegt in den
Volumes, nicht im Container selbst).

Status prüfen / manuell eingreifen bei Bedarf:

```bash
systemctl --user status hvnb-backup.service    # Status (auch: seit wann aktiv, letzte Log-Zeilen)
journalctl --user -u hvnb-backup.service -f    # Log live verfolgen
systemctl --user restart hvnb-backup.service   # manueller Neustart, falls je noetig
podman ps -a --filter name=hvnb-backup         # alternativ direkt ueber podman
podman logs --tail 50 hvnb-backup
```

`podman-restart.service` (falls aus einer älteren Einrichtung noch aktiv)
kann parallel bestehen bleiben, ist aber für `hvnb-backup` selbst
überflüssig geworden — die Quadlet-Unit deckt denselben Fall zusätzlich mit
ab und startet den Container außerdem bei jedem anderen Stopp-Grund neu.

## 10. Erste Anmeldung

```
https://<Server-IP-oder-Name>:8443
Benutzer: admin
Passwort: <HVNB_INITIAL_ADMIN_PASSWORD aus Schritt 5>
```

Sofort nach dem ersten Login unter **Settings > Benutzer & Rollen** das
Admin-Passwort ändern.

Health-Check (auch ohne Login abrufbar, praktisch für Monitoring):

```bash
curl -sk https://<Server-IP>:8443/api/health
# {"status":"ok","app":"Hyper-V NetApp Backup"}
```

## 11. Nächste Schritte (in der GUI)

Die Applikation ist jetzt lauffähig, aber fachlich noch leer. Über die
Web-GUI folgen (in dieser Reihenfolge sinnvoll):

1. **Settings > Hyper-V-Hosts** — Hyper-V-Cluster hinzufügen
2. **Storage > Cluster** — NetApp-Cluster hinzufügen
3. **Restore > Setup** — Restore-Proxy-Host + iSCSI-Infrastruktur einrichten
   (Voraussetzung für jeden Restore-Vorgang)
4. **Backup > Policies / Protection Groups / Zeitpläne** — Backup-Regeln
   definieren
5. **Settings > Active-Directory-Integration** (falls gewünscht) /
   **Settings > E-Mail** (Alerting) — optional

Eine funktionale Architekturübersicht ist direkt in der Applikation unter
dem Dokumentations-Link in der Seitenleiste verlinkt.

## 12. Betrieb

**Updates:** bei `HVNB_AUTO_UPDATE_ENABLED=true` vollautomatisch (siehe
Abschnitt 4 für die beiden Code-Bezugsmodelle). Manuell erzwingen:

```bash
podman exec hvnb-backup supervisorctl restart uvicorn nginx
```

**Logs:**

```bash
podman logs --tail 100 hvnb-backup           # supervisord-Gesamtausgabe
podman exec hvnb-backup tail -f /var/log/hvnb/uvicorn.log
podman exec hvnb-backup tail -f /var/log/hvnb/updater.log
```

Innerhalb der Applikation zusätzlich das **System Log** (Kopfzeile,
Terminal-Symbol) für Backup-/Restore-/Scheduler-Ereignisse mit wählbarem
Zeitraum.

**Troubleshooting-Kurzreferenz:**

| Symptom | Wahrscheinliche Ursache | Abschnitt |
|---|---|---|
| GUI von aussen nicht erreichbar, Container läuft | WSL2-Guest-IP hat sich geändert, Portproxy zeigt ins Leere | 8 |
| Container nach Server-Neustart als `Exited`/gar nicht gestartet | `loginctl enable-linger` fehlt, oder die Quadlet-Datei fehlt/wurde nicht per `daemon-reload` eingelesen | 6, 9 |
| Container stoppt/stürzt ab und kommt nicht von selbst wieder hoch | Betrieb läuft noch über `podman-compose up -d` statt der Quadlet-Unit (kein Dauer-Daemon in rootless Podman) | 6, 9 |
| `git`-Fehler beim Deploy trotz erreichbarem Server | Zugangsdaten/Deploy-Key für die Repository-URL fehlen | 4 |
| Health-Check liefert `502 Bad Gateway` kurz nach Neustart | uvicorn/nginx starten noch, wenige Sekunden abwarten | — |
