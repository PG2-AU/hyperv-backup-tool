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

# HTTPS-Listener einrichten -- braucht ein Zertifikat im Speicher
# LocalMachine\My, dessen Subject/SAN auf den Hostnamen lautet, mit dem
# der Node spaeter in der GUI angesprochen wird. Ein Failover-Clustering
# nutzt dort bereits automatisch erzeugte Zertifikate (z.B. "CN=<GUID>.TLS"
# oder "CN=CLIUSR") -- die sind NICHT geeignet, das sind interne
# Cluster-Kommunikations-/Dienstkonto-Zertifikate, kein Hostname-Zertifikat.
Get-ChildItem -Path Cert:\LocalMachine\My

# Eigenes Zertifikat besorgen -- entweder von der internen PKI ausgestellt
# (Subject/SAN = Hostname des Knotens, empfohlen, siehe Kasten unten) oder
# fuer Tests selbstsigniert:
$hostname = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName
$cert = New-SelfSignedCertificate -DnsName $hostname -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(5)
$cert.Thumbprint

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

```powershell
# Auf dem Hyper-V-Host: das (oeffentliche!) Zertifikat erst als .cer
# exportieren -- bei einer internen CA stattdessen nur den CA-ROOT
# einmalig exportieren, das deckt dann automatisch ALLE damit
# ausgestellten Knoten-Zertifikate ab, statt jeden Knoten einzeln zu
# pflegen. $cert ist das oben mit New-SelfSignedCertificate erzeugte
# Zertifikats-Objekt (bzw. das von der internen PKI erhaltene).
New-Item -ItemType Directory -Path C:\temp -Force | Out-Null
Export-Certificate -Cert $cert -FilePath C:\temp\winrm-host.cer

# .cer (DER-binaer) zu .pem (Base64) konvertieren:
certutil -encode C:\temp\winrm-host.cer C:\temp\winrm-ca.pem
# .pem-Datei per RDP-Dateitransfer o.ae. auf den WSL2-Host uebertragen.
```

`/etc/hvnb/certs` im Container ist bereits ein **persistentes benanntes
Volume** (`hvnb-certs`, dort liegt auch schon das TLS-Zertifikat der GUI)
— dafür ist also keine zusätzliche Zeile in `docker-compose.yml` nötig,
die Datei kann direkt per `podman cp` in den laufenden Container gelegt
werden:

```bash
# Auf dem WSL2-Host: Datei z.B. per Windows-Explorer unter
# \\wsl.localhost\<Distro-Name>\home\<Benutzer>\ ablegen, dann:
podman cp ~/winrm-ca.pem hvnb-backup:/etc/hvnb/certs/winrm-ca.pem
```

```bash
# .env, im Projektverzeichnis:
echo "HVNB_WINRM_CA_TRUST_PATH=/etc/hvnb/certs/winrm-ca.pem" >> .env
podman-compose up -d
```

Da `hvnb-certs` ein benanntes Volume ist, übersteht die Datei den
Container-Neustart in Schritt 2 unabhängig von der Reihenfolge der beiden
Befehle. Settings > Updates zeigt anschließend unter "WinRM
CA-Trust-Datei" den konfigurierten Pfad zur Kontrolle an. Bei mehreren
Knoten mit jeweils eigenem, nicht CA-signiertem Zertifikat: alle
Host-Zertifikate hintereinander in dieselbe PEM-Datei einfügen (eine
PEM-Datei kann mehrere Zertifikate enthalten) — mit einer internen CA
reicht dagegen der eine CA-Root für alle Knoten.

Zusätzlich:

- Der verbindende Account (in der GUI beim Hinzufügen des Clusters
  hinterlegt) braucht **lokale Administratorrechte** auf jedem Knoten.
- `HVNB_WINRM_TRANSPORT` (Abschnitt 5) muss zum serverseitig aktivierten
  Verfahren passen — `credssp` erfordert exakt den obigen
  `Enable-WSManCredSSP -Role Server`-Schritt, `ntlm` kommt ohne diesen
  Schritt aus (nur Listener + Firewall nötig), unterstützt aber keine
  Double-Hop-Szenarien.

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
`docker-compose.yml`) — ein `podman-compose up -d` nach einer `.env`-
Änderung erkennt die geänderte Konfiguration jedoch und erstellt den
Container neu, wodurch `/opt/app` leer beginnt und `entrypoint.sh` den
"erstmaligen Klon"-Pfad mit der neuen URL erneut durchläuft. Ein reines
`podman restart hvnb-backup` (ohne vorheriges `up -d`) reicht dafür
**nicht** aus.

Die `.env`-Datei enthält damit ein Secret im Klartext und
ist bereits über `.gitignore` von Commits ausgeschlossen — trotzdem
zusätzlich die Dateiberechtigung einschränken:

```bash
chmod 600 .env
```

**Rotation/Widerruf:** Token bei Bedarf jederzeit unter GitHub > Settings >
Developer settings > Fine-grained tokens widerrufen und durch ein neues
ersetzen (`.env` aktualisieren, danach `podman-compose up -d` um den
Container mit der neuen URL neu zu starten).

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

podman-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

`HVNB_GIT_REPO_URL` wird dabei automatisch von `docker-compose.dev.yml` auf
`file:///srv/git/hyperv-netapp-backup.git` gesetzt (siehe Kommentar in der
Datei) — das lokale Bare-Repo wird dafür nach `/srv/git/...` in den
Container gemountet.

**Code-Updates in diesem Modell:** das Bare-Repo muss erneut befüllt
werden, z. B. per `git bundle create update.bundle --all` auf einem Host mit
Zugriff, Transfer der Bundle-Datei, und auf dem Server:

```bash
git -C ~/hyperv-repo.git fetch update.bundle 'refs/*:refs/*'
```

Der Updater-Prozess im Container erkennt die neuen Commits beim nächsten
Intervall automatisch (`HVNB_AUTO_UPDATE_ENABLED=true`) bzw. sofort nach
einem manuellen `podman-compose restart hvnb-backup`.

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

Modell 4b (HTTPS + Token, Regelfall):

```bash
podman-compose -f docker-compose.yml up -d --build
```

Modell 4c (lokales Bare-Repo):

```bash
podman-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Prüfen:

```bash
podman ps --filter name=hvnb-backup
podman logs --tail 50 hvnb-backup
```

Erwartet: `uvicorn`, `nginx` (und bei aktiviertem Auto-Update `updater`)
laufen ohne Fehlermeldungen. Der erste Start dauert spürbar länger (Klonen
des Repos, `npm ci`, Frontend-Build).

## 7. TLS-Zertifikat

Beim allerersten Start erzeugt der Container automatisch ein
selbstsigniertes Zertifikat (`docker/gen-selfsigned-cert.sh`), damit die GUI
sofort per HTTPS erreichbar ist. Für den Produktivbetrieb ein von der
internen PKI ausgestelltes Zertifikat einbinden, statt das benannte Volume
`hvnb-certs` zu nutzen — in `docker-compose.yml` die Volume-Zeile durch einen
Bind-Mount ersetzen:

```yaml
volumes:
  - ./certs:/etc/hvnb/certs # server.crt + server.key ablegen
```

Danach den Container neu starten (`podman-compose up -d`).

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
bei **rootless** Podman (kein dauerhafter Root-Daemon wie bei Docker) nur so
lange greift, wie die `systemd --user`-Instanz des Benutzers läuft. Ein
Windows-Sleep/Ruhezustand oder `wsl --shutdown` kann diese Instanz beenden —
ohne die folgenden zwei Einstellungen bleibt der Container danach als
`Exited` liegen, statt automatisch neu zu starten.

In der WSL2-Distribution:

```bash
# Erlaubt der systemd--user-Instanz des Benutzers, unabhaengig von einer
# aktiven Login-Session zu laufen (auch nach Reboot/WSL2-Neustart).
sudo loginctl enable-linger <benutzername>

# Startet beim (Re-)Start der systemd--user-Instanz automatisch alle
# Container mit passender Restart-Policy neu.
systemctl --user enable podman-restart.service
```

Status prüfen / manuell eingreifen bei Bedarf:

```bash
podman ps -a --filter name=hvnb-backup   # Status
podman logs --tail 50 hvnb-backup        # Logs
podman start hvnb-backup                 # falls doch einmal gestoppt
```

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
| Container nach Server-Neustart als `Exited` | `loginctl enable-linger` fehlt | 9 |
| `git`-Fehler beim Deploy trotz erreichbarem Server | Zugangsdaten/Deploy-Key für die Repository-URL fehlen | 4 |
| Health-Check liefert `502 Bad Gateway` kurz nach Neustart | uvicorn/nginx starten noch, wenige Sekunden abwarten | — |
