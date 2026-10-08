# Installation & Deployment

Diese Anleitung beschreibt alle Schritte von einem frischen, domain-gejointen
**Windows Server 2025** bis zur lauffähigen Applikation **AU Storage Manager for
Hyper-V** (vormals "Hyper-V NetApp Backup"; technische Bezeichner wie
`HVNB_*`-Variablen, Container `hvnb-backup` und Volumes `hvnb-data`/`hvnb-certs`
behalten das Kürzel HVNB). Sie basiert auf einer
real gegen eine solche Umgebung verifizierten Ersteinrichtung (siehe
[INSTALL.md](INSTALL.md) für das dazugehörige, historische Feldprotokoll mit
allen dabei gefundenen Stolpersteinen) und fasst diese Erkenntnisse zu einer
allgemeingültigen, wiederholbaren Anleitung zusammen.

## Architekturüberblick

Die Applikation läuft nicht nativ unter Windows, sondern als **rootless
Podman-Container innerhalb von WSL2** auf dem Windows Server. Ausgeliefert
wird sie als **Release-Image**: ein fertiges, versioniertes Container-Image
(Rocky Linux 9 Basis) mit Backend, gebautem Frontend und fest
vorgegebenen Python-Abhängigkeiten. Der Container lädt zur Laufzeit
**nichts** nach — kein Git, kein `pip`, kein `npm`, kein Compiler im Image.
`supervisord` betreibt darin zwei Prozesse:

- **uvicorn** — FastAPI-Backend, lauscht intern auf `127.0.0.1:8000`
- **nginx** — terminiert TLS, liefert das Frontend aus, reverse-proxyt
  `/api/*` auf uvicorn

Aktualisiert wird von außen: der Server tauscht das Image gegen eine neue
Version aus (Skript `hvnb-update`). Die neue Version kommt als
**Paketdatei** (ohne Internetzugang) oder aus einer **Registry**; für
Entwicklungsumgebungen kann der Server sie auch selbst aus Git bauen. Alle
Wege stehen in Abschnitt 13.

Warum WSL2 statt einer nativen Windows-Installation: Podman/systemd/nginx
sind Linux-Werkzeuge, die App selbst muss aber auf einem Windows-Host laufen
können, der per WinRM mit den Hyper-V-Clusterknoten und dem Restore-Proxy-
Host spricht — WSL2 bringt beides auf eine Maschine.

> **Bestehende Installationen mit dem früheren Git-Mechanismus** (der
> Container holte den Code beim Start selbst per `git pull`) laufen weiter,
> bis sie umgestellt werden — siehe Abschnitt 4b.

## 1. Voraussetzungen

- [ ] Windows Server 2025, Mitglied der Domäne, lokale Administratorrechte
      auf dem Server
- [ ] **Läuft dieser Server selbst als VM** (z. B. auf Hyper-V, VMware oder
      einem anderen Hypervisor): verschachtelte Virtualisierung
      (nested virtualization) muss auf Host-Ebene freigeschaltet sein —
      WSL2 startet selbst wieder eine VM darin (die Linux-Distribution) und
      scheitert sonst mit `HCS_E_HYPERV_NOT_INSTALLED`, live beim
      Rocky-VM-Deployment aufgetreten. Je nach Hypervisor, **VM vorher
      ausschalten**:
  - **Hyper-V:** auf dem Hyper-V-**Host** (nicht in der VM selbst):
    `Set-VMProcessor -VMName <VMName> -ExposeVirtualizationExtensions $true`
  - **VMware:** vSphere Client/vCenter → VM auswählen → Edit Settings → CPU
    → Häkchen bei "Expose hardware assisted virtualization to the guest OS"
    (teils auch "Virtualize Intel VT-x/EPT or AMD-V/RVI" benannt)
  - **Physische Maschine ohne Hypervisor darunter:** betrifft das nicht —
    dann stattdessen Virtualisierung (VT-x/AMD-V) im BIOS/UEFI aktivieren,
    falls dort noch deaktiviert
- [ ] Ausgehender Netzwerkzugriff von diesem Server zu:
  - dem NetApp-ONTAP-Cluster-Management-LIF (TCP 443, HTTPS/REST)
  - allen zu verwaltenden Hyper-V-Clusterknoten sowie dem geplanten
    Restore-Proxy-Host (WinRM — TCP 5986 bei HTTPS/Default, TCP 5985 bei
    HTTP)
  - dem Domain Controller, falls Active-Directory-Login aktiviert werden
    soll (LDAP/LDAPS — TCP 389 bzw. 636)
  - **keinem** Git-Server und **keiner** Paketquelle: Installation und
    Updates per Paketdatei brauchen keinen Internetzugang. Nur bei Bedarf:
    der Registry `ghcr.io` (TCP 443) für Online-Updates, bzw. Git-Server,
    `quay.io`, npm und PyPI für das Update aus Git einer
    Entwicklungsumgebung (Abschnitt 13)
  - dem Domain Controller (KDC) auf TCP/UDP 88, falls Kerberos als
    WinRM-Transport genutzt werden soll (siehe Abschnitt 10)
  - dem Fileserver mit der Freigabe für die DB-Sicherung (SMB — TCP 445),
    falls die automatische DB-Sicherung eingerichtet wird (Abschnitt 13)
  - dem Mailserver (SMTP, Port laut Settings > E-Mail), falls Alarme oder
    Reports per Mail verschickt werden sollen
- [ ] Für die Remote-Sitzung auf eine VM (Inventory > VMs): Zugriff **vom PC
      des Benutzers** auf die Hyper-V-Knoten (TCP 2179, Konsole) bzw. auf
      die VM selbst (TCP 3389, RDP) — nicht vom HVNB-Server aus
- [ ] Eingehender Netzwerkzugriff auf den gewählten HTTPS-Port (Beispiel in
      dieser Anleitung: 8443) von den Rechnern/Netzen, aus denen die Web-GUI
      erreichbar sein soll
- [ ] Ein NetApp-Cluster und mindestens ein Hyper-V-Cluster sind bereits
      grundsätzlich erreichbar — beide werden **nicht** in dieser Anleitung,
      sondern nach dem ersten Login komplett über die Web-GUI eingerichtet
      (siehe Abschnitt 12)

Diese Anleitung ist in **drei Teile** gegliedert, die auf unterschiedlichen
Maschinen laufen:

| Teil | Abschnitte | Läuft auf | Inhalt |
|---|---|---|---|
| **Teil 1** | 2–9 | **dem neuen Windows Server 2025** (der eigentliche Backup-Host, im Rest dieser Anleitung „HVNB-Server" genannt) | WSL2, Podman-Container, Konfiguration, Betrieb |
| **Teil 2** | 10 | **jedem Hyper-V-Clusterknoten** | WinRM aktivieren, Zertifikat, Servicekonto, Firewall |
| **Teil 3** | 11–12 | **beiden zusammen**, über die Web-GUI auf dem HVNB-Server — braucht Teil 1 UND Teil 2 abgeschlossen | Erste Anmeldung, Cluster/Storage in der App hinzufügen |

Abschnitt 13 (Betrieb) ist laufender Betrieb, kein Einrichtungsschritt mehr.

---

**Teil 1: Auf dem HVNB-Server**

> Ab hier bis einschließlich Abschnitt 9 laufen alle Befehle auf dem neuen
> Windows Server 2025 (PowerShell bzw. einer WSL2-Shell darauf) — **nicht**
> auf einem Hyper-V-Clusterknoten (dafür siehe Teil 2).

## 2. WSL2 aktivieren und Linux-Distribution einrichten

PowerShell als Administrator:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart
Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart
Restart-Computer
```

Nach dem Neustart eine Distribution installieren. Empfohlen und für den
Rest dieser Anleitung durchgehend verwendet: **Rocky Linux** (matcht die
folgenden `dnf`-Befehle direkt, keine Anpassung nötig) — Microsoft stellt
dafür kein fertiges Store-Paket bereit, das offizielle Rocky-WSL-Basisimage
kommt stattdessen als `.wsl`-Datei direkt von Rocky:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
# Rocky-10-WSL-Base.latest.x86_64.wsl vorher von
# https://dl.rockylinux.org/vault/rocky/10.0/images/x86_64/Rocky-10-WSL-Base.latest.x86_64.wsl
# herunterladen, z.B. nach %UserProfile%\Downloads:
wsl --install --from-file "$env:USERPROFILE\Downloads\Rocky-10-WSL-Base.latest.x86_64.wsl"
```

> Dieser `--from-file`-Weg für `.wsl`-Paketdateien ist neuer als der
> klassische `wsl --install -d <Name-aus-dem-Store>`-Ein-Zeiler, wurde aber
> live gegen eine echte Windows-Server-2025-Instanz verifiziert (Rocky
> erscheint danach als Standard-Distribution in `wsl -l`). Läuft der Server
> selbst als VM, zuerst Abschnitt 1 ("verschachtelte Virtualisierung")
> beachten — sonst scheitert der Befehl mit `HCS_E_HYPERV_NOT_INSTALLED`.

Danach in die Distribution wechseln:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
wsl -d rocky
```

Beim allerersten Start dieser Art fragt Rocky nach einem Standard-Benutzer
(muss nicht mit dem Windows-Benutzernamen übereinstimmen, landet automatisch
in der `wheel`-Gruppe und kann direkt ohne Passwort `sudo` nutzen):

```
Please create a default user account. The username does not need to match your Windows username.
Enter new UNIX username: admin
Your user has been created, is included in the wheel group, and can use sudo without a password.
```

Alle folgenden `bash`-Befehle dieser Anleitung laufen in dieser Shell
(bzw. bei einem späteren erneuten Login wieder per `wsl -d rocky`).

(Jede andere systemd-fähige, aktuelle Distribution funktioniert
grundsätzlich ebenso, z.B. per `wsl --install -d Ubuntu-22.04` — betrifft
nur den WSL2-**Host**, nicht das Container-Image selbst, das unabhängig
davon immer auf `rockylinux:9` basiert. Bei Ubuntu/Debian dann aber `apt`
statt `dnf` und `openssl`/`podman` über die dort üblichen Paketnamen
verwenden, abweichend von den folgenden Befehlen dieser Anleitung.)

**Wichtige Voraussetzung für Abschnitt 9 (Container-Persistenz):** systemd
muss innerhalb der WSL2-Distribution aktiv sein. Prüfen bzw. aktivieren:

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
sudo dnf install -y podman
```

Verifizieren (`curl`, `openssl` und `sha256sum` bringt das Rocky-Basisimage
bereits mit):

```bash
podman --version
curl --version | head -n1
openssl version
```

`git` wird auf dem Server **nicht** gebraucht — nur, wenn eine
Entwicklungsumgebung sich selbst aus Git aktualisieren soll (Abschnitt 13,
dann zusätzlich `sudo dnf install -y git`).

## 4. Release-Paket beziehen

Ein Release besteht aus diesen Dateien:

| Datei | Inhalt |
|---|---|
| `hvnb-<Version>.tar.gz` | das Container-Image als Archiv (rund 170 MB) |
| `hvnb-<Version>.tar.gz.sha256` | SHA-256-Prüfsumme des Archivs |
| `hvnb-update` | Skript zum Einspielen, Zurückrollen und für den Update-Dienst |
| `hvnb-git-autoupdate` | Skript für das Update aus Git (nur Entwicklungsumgebungen) |

Woher sie kommen:

- **Vom GitHub-Release** der gewünschten Version (Repository > Releases),
  sobald Versionen dort veröffentlicht werden (Abschnitt 13, „Ein Release
  erzeugen").
- **Selbst gebaut** auf einem Rechner mit Internetzugang, siehe 4a.

Die Dateien auf den Server bringen — z. B. per RDP-Dateitransfer oder über
den Windows-Explorer unter `\\wsl.localhost\rocky\home\<Benutzer>\` — und
in einem eigenen Ordner ablegen:

```bash
mkdir -p ~/hvnb/release
# hvnb-<Version>.tar.gz, .sha256, hvnb-update, hvnb-git-autoupdate nach ~/hvnb/release kopieren
cd ~/hvnb/release
sha256sum -c hvnb-<Version>.tar.gz.sha256
```

Erwartet: `hvnb-<Version>.tar.gz: OK`. Bei jeder anderen Ausgabe die Datei
erneut übertragen — nicht installieren.

> Die Prüfsumme erkennt beschädigte oder vertauschte Dateien. Sie schützt
> **nicht** vor einem absichtlich ausgetauschten Paket (wer das Archiv
> ersetzt, kann auch die Prüfsummendatei ersetzen) — maßgeblich ist der
> Weg, auf dem das Paket zum Server kommt.

### 4a. Paket selbst bauen

Auf einem Rechner mit `git`, `podman`, `python3` und Internetzugang
(`quay.io`, npm, PyPI) — **nicht** auf dem Produktivserver:

```bash
git clone <Repository-Adresse> ~/hyperv-netapp-backup
cd ~/hyperv-netapp-backup
scripts/build-release.sh 1.2.0
```

Die Versionsnummer hat die Form `X.Y.Z`. Gebaut wird der committete Stand;
nicht committete Änderungen brechen den Build ab. Nach etwa fünf Minuten
liegen die vier Dateien aus der Tabelle oben in `dist/`.

Die Python-Abhängigkeiten sind in `backend/requirements.lock` mit
Prüfsummen festgeschrieben — ein Build installiert immer exakt diese
Versionen. Geändert wird die Datei nur bewusst mit
`scripts/update-lock.sh` (bzw. `--upgrade` für neuere Versionen).

### 4b. Bestehende Git-Installation umstellen

Für Server, die noch mit dem früheren Mechanismus laufen (Image
`localhost/hyperv-netapp-backup:local`, der Container holt den Code per
`git pull`). Datenbank, Zertifikate und `.env` bleiben unverändert; die
Abschnitte 5 und 6 entfallen.

```bash
cd ~/hvnb/release
bash hvnb-update --status
```

Erwartet: Image `localhost/hyperv-netapp-backup:local`, Version „ohne
Versionsnummer (bisherige Auslieferung per git)".

```bash
bash hvnb-update hvnb-<Version>.tar.gz
```

Das Skript prüft die Prüfsumme, bricht ab, solange Backups oder Restores
laufen, sichert die Datenbank nach `/data/update-backups`, stellt in der
Quadlet-Unit nur die `Image=`-Zeile um und wartet auf den Health-Check. Die
GUI ist dabei etwa eine halbe Minute nicht erreichbar. Am Ende steht
„Fertig. Laufende Version: <Version>".

Danach:

- Update-Dienst einrichten wie am Ende von Abschnitt 6 beschrieben.
- Aus der `.env` die nicht mehr ausgewerteten Zeilen entfernen:
  `HVNB_GIT_REPO_URL` (enthält ggf. ein Zugriffstoken), `HVNB_GIT_BRANCH`,
  `HVNB_AUTO_UPDATE_ENABLED`, `HVNB_AUTO_UPDATE_INTERVAL_MINUTES`.

Zurück auf den alten Stand geht mit `hvnb-update --rollback`. Das alte
Image braucht für seinen Start mehrere Minuten (Git-Klon und
Frontend-Build), länger als das Skript standardmäßig wartet — deshalb mit
verlängerter Wartezeit:

```bash
HVNB_HEALTH_TIMEOUT=900 hvnb-update --rollback
```

## 5. Konfiguration (`.env`)

Die `.env` liegt neben dem Release-Ordner unter `~/hvnb/.env`. Sie ist eine
**Dotfile** (Dateiname beginnt mit einem Punkt) — ein normales `ls` zeigt
sie **nicht** an, dafür `ls -la` verwenden.

```bash
mkdir -p ~/hvnb
cat > ~/hvnb/.env << 'EOF'
HVNB_ENVIRONMENT=production
HVNB_SECRET_KEY=<zufaelliger, langer String -- z.B. `openssl rand -hex 32`>
HVNB_INITIAL_ADMIN_PASSWORD=<einmaliges Startpasswort, sofort nach dem ersten Login aendern>

HVNB_WINRM_TRANSPORT=credssp
HVNB_WINRM_USE_HTTPS=true
HVNB_WINRM_PORT=5986
EOF
chmod 600 ~/hvnb/.env
```

**Wichtig:** NetApp-Cluster, Hyper-V-Hosts, der Restore-Proxy-Host,
SnapMirror-Policies, Backup-Policies, E-Mail-Alerting usw. werden **nicht**
per `.env` konfiguriert, sondern vollständig über die Web-GUI nach dem
ersten Login (Settings, Storage, Restore > Setup, Backup) — dort auch
verschlüsselt in der Datenbank statt im Klartext einer `.env`-Datei
gespeichert.

> **`HVNB_SECRET_KEY` getrennt verwahren.** Mit diesem Schlüssel sind alle
> in der Datenbank gespeicherten Kennwörter verschlüsselt. Geht er verloren,
> sind sie unbrauchbar (siehe Abschnitt 13, DB-Sicherung).

Die Liste der unterstützten Variablen mit Erläuterung steht in
`backend/.env.example` im Repository. Die dort genannten Intervalle für
Health-Check, Discovery und Snapshot-Abgleich sind nur Startwerte einer
frischen Installation; danach gilt Settings > Hintergrundjobs.

## 6. Container einrichten und starten

Der Container wird von systemd über eine **Quadlet**-Unit verwaltet
(Abschnitt 9 erklärt, warum: rootless Podman hat anders als Docker keinen
Dauer-Daemon, der einen abgestürzten Container von selbst neu starten
würde).

**Image laden:**

```bash
podman load -i ~/hvnb/release/hvnb-<Version>.tar.gz
```

Die letzte Zeile der Ausgabe nennt den Image-Namen, z. B.
`Loaded image: localhost/hvnb-backup:1.2.0` (bei einem auf GitHub gebauten
Paket `ghcr.io/<konto>/hvnb-backup:1.2.0`). Genau dieser Name gehört in die
`Image=`-Zeile der folgenden Datei.

**Quadlet-Unit anlegen** (einmalig) unter
`~/.config/containers/systemd/hvnb-backup.container`:

```bash
mkdir -p ~/.config/containers/systemd
cat > ~/.config/containers/systemd/hvnb-backup.container << 'EOF'
[Unit]
Description=AU Storage Manager for Hyper-V
After=network-online.target
Wants=network-online.target

[Container]
Image=localhost/hvnb-backup:<Version>
ContainerName=hvnb-backup
PublishPort=8443:443
EnvironmentFile=%h/hvnb/.env
Environment=HVNB_DATABASE_URL=sqlite:////data/app.db
Volume=hvnb-data:/data
Volume=hvnb-certs:/etc/hvnb/certs

[Service]
Restart=always
TimeoutStartSec=900

[Install]
WantedBy=default.target
EOF
```

Die beiden Volumes (`hvnb-data` für Datenbank, Reports und Sicherungen,
`hvnb-certs` für das TLS-Zertifikat) legt Podman beim ersten Start von
selbst an. Bestehende Installationen aus der Zeit vor dem Release-Image
behalten ihre bisherigen Namen und Pfade (Volumes
`hyperv-netapp-backup_hvnb-data` und `hyperv-netapp-backup_hvnb-certs`,
`EnvironmentFile=%h/hyperv-netapp-backup/.env`) — dort an der Unit nichts
ändern, `hvnb-update` stellt nur die `Image=`-Zeile um.

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

Erwartet: eine Zeile `[entrypoint] ... AU Storage Manager for Hyper-V,
Release <Version>`, danach `uvicorn` und `nginx` im Zustand `RUNNING` ohne
Fehlermeldungen. Der Start dauert nur wenige Sekunden; beim allerersten
Start kommt das Anlegen der Datenbank hinzu.

Abschließender Funktionscheck:

```bash
curl -sk https://127.0.0.1:8443/api/health
```

Erwartet: `{"status":"ok","app":"AU Storage Manager for Hyper-V"}`. Bewusst
`127.0.0.1` statt `localhost` — `localhost` löst auf vielen Systemen
zuerst zu IPv6 (`::1`) auf, `podman port` mapped den Port aber nur auf
IPv4 (`0.0.0.0:8443`), sodass der Verbindungsversuch über `localhost`
scheitern kann, obwohl der Container einwandfrei läuft (siehe auch die
Troubleshooting-Tabelle in Abschnitt 13). Unmittelbar nach dem Start kann
kurz `502 Bad Gateway` kommen, bis uvicorn bereit ist.

**Update-Werkzeuge einrichten** (einmalig, empfohlen):

```bash
bash ~/hvnb/release/hvnb-update --install-agent
hvnb-update --status
```

Das kopiert `hvnb-update` und `hvnb-git-autoupdate` nach `~/.local/bin` und
richtet den **Update-Dienst** ein: einen systemd-Timer des Benutzers
(`hvnb-backup-update-agent.timer`), der alle 30 Sekunden nachsieht, ob aus
der GUI ein Update-Auftrag vorliegt. Erst damit lassen sich Updates unter
Settings > Updates hochladen und einspielen (Abschnitt 13). Wer Updates
ausschließlich per Befehl auf dem Server einspielen will, lässt
`--install-agent` weg und kopiert nur das Skript:

```bash
install -D -m 0755 ~/hvnb/release/hvnb-update ~/.local/bin/hvnb-update
```

> **Für jede spätere `.env`-Änderung (oder Änderung an der Quadlet-Datei
> selbst, z. B. Zertifikats-Volume in Abschnitt 7) gilt:** die Quadlet-Unit
> baut den Container bei JEDEM Start neu auf (`--replace --rm` im
> generierten `ExecStart`, sichtbar per `systemctl --user cat
> hvnb-backup.service`) — ein einfaches
>
> ```bash
> systemctl --user daemon-reload   # nur noetig, wenn sich die .container-Datei selbst aenderte
> systemctl --user restart hvnb-backup.service
> ```
>
> reicht daher immer aus. Die beiden Volumes bleiben davon unberührt
> (`/data`, `/etc/hvnb/certs`), nur der Container selbst wird frisch
> erstellt.

## 7. TLS-Zertifikat

Beim allerersten Start erzeugt der Container automatisch ein
selbstsigniertes Zertifikat (`docker/gen-selfsigned-cert.sh`), damit die GUI
sofort per HTTPS erreichbar ist. Für den Produktivbetrieb ein von der
internen PKI ausgestelltes Zertifikat einbinden, statt das benannte Volume
`hvnb-certs` zu nutzen: in der Quadlet-Datei
`~/.config/containers/systemd/hvnb-backup.container` die `Volume`-Zeile für
die Zertifikate durch einen Bind-Mount ersetzen:

```ini
Volume=%h/hvnb/certs:/etc/hvnb/certs # server.crt + server.key ablegen
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
ohne Portweiterleitung. `.wslconfig` im Windows-Benutzerprofil anlegen, in
PowerShell (kein Administrator nötig):

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
@"
[wsl2]
networkingMode=mirrored
"@ | Set-Content -Path "$env:USERPROFILE\.wslconfig" -Encoding ascii
```

> Überschreibt die Datei komplett. Existiert dort bereits eine
> `.wslconfig` mit anderen Einstellungen (`Get-Content
> "$env:USERPROFILE\.wslconfig"` vorher prüfen), stattdessen nur die
> `[wsl2]`-Sektion/Zeile `networkingMode=mirrored` von Hand ergänzen.

Danach:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
wsl --shutdown
```

...und die Distro einmal neu öffnen, damit die Änderung greift. **Bekannte
Einschränkung:** auf manchen Windows-Server-2025-Builds schlägt die
Aktivierung mit `CreateInstance/CreateVm/ConfigureNetworking/0x803b0015`
fehl und WSL2 fällt komplett netzwerklos zurück (`networkingMode=None`) —
in diesem Fall `.wslconfig` wieder entfernen und den NAT-Modus (unten)
verwenden.

### NAT-Modus (Standard, funktioniert immer)

Im Standard-NAT-Modus bekommt die WSL2-Distribution eine eigene, nur
Windows-intern erreichbare IP, die sich bei jedem `wsl --shutdown` oder
Windows-Neustart **ändert**. Eine Portweiterleitung von der Windows-
Server-IP auf die jeweils aktuelle WSL2-Guest-IP ist nötig.

Aktuelle WSL2-Guest-IP ermitteln (in der WSL2-Shell -- **nicht** `hostname
-I` verwenden, das Paket `hostname` ist auf manchen Minimal-Distributionen
wie RockyLinux nicht installiert und der Befehl schlägt mit "command not
found" fehl):

```bash
ip -4 addr show eth0
# Interface-unabhaengige Alternative, falls die Schnittstelle nicht eth0 heisst:
ip route get 1.1.1.1 | awk '{print $7; exit}'
```

Damit dann die Portweiterleitung einrichten:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
New-NetFirewallRule -DisplayName "HVNB HTTPS (8443)" -Direction Inbound -Protocol TCP -LocalPort 8443 -Profile Domain,Private,Public -Action Allow
netsh interface portproxy add v4tov4 listenport=8443 listenaddress=0.0.0.0 connectport=8443 connectaddress=<WSL2-Guest-IP>
```

Da sich die WSL2-Guest-IP bei jedem Neustart ändert, liegt im Repository ein
Skript, das die Regel automatisch aktuell hält:
[`docs/windows/Update-HvnbPortProxy.ps1`](windows/Update-HvnbPortProxy.ps1).
Als wiederkehrende Aufgabe registrieren (PowerShell als Administrator):

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
$scriptPath = "C:\hvnb\Update-HvnbPortProxy.ps1"  # Skript vorher dorthin kopieren

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
$atStartup = New-ScheduledTaskTrigger -AtStartup
$atStartup.Delay = "PT2M"   # WSL2-Netzwerk braucht nach dem Boot etwas Zeit
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "HVNB-PortProxy-Refresh" `
    -Action $action -Trigger $atStartup, $periodic -Principal $principal `
    -Description "Haelt die netsh-Portweiterleitung fuer den HVNB-Container synchron mit der WSL2-Guest-IP."
```

> **Stolperstein:** `[TimeSpan]::MaxValue` (statt der obigen begrenzten
> 10-Jahres-Dauer) scheitert bei `Register-ScheduledTask` mit "The task
> XML contains a value which is incorrectly formatted or out of range"
> (`P99999999DT23H59M59S` liegt außerhalb des von der Task-Scheduler-XML
> akzeptierten Bereichs) — live beim Rocky-VM-Deployment aufgetreten.

Die wiederkehrende 15-Minuten-Ausführung fängt auch den Fall ab, dass jemand
`wsl --shutdown` ausführt, ohne den Windows-Server neu zu starten.

> Das Skript ist nicht live gegen eine echte Windows-Server-2025-Instanz
> verifiziert (nur das darin verwendete `netsh`-Befehlspaar selbst wurde in
> der Referenzumgebung manuell bestätigt) — vor dem produktiven Einsatz
> einmal manuell ausführen und mit `netsh interface portproxy show v4tov4`
> kontrollieren.

Verifizieren von einem externen Host:

**Host: ein beliebiger anderer Rechner im Netz (Kontrollzugriff)**

```powershell
Test-NetConnection -ComputerName <Server-IP> -Port 8443
```

## 9. Container-Persistenz absichern (rootless Podman + WSL2)

**Voraussetzung, bevor alles Folgende überhaupt greifen kann: die WSL2-VM
selbst muss durchgehend laufen — unabhängig davon, ob überhaupt jemand am
Server angemeldet ist.** Live beobachtet (zweimal, mit unterschiedlichem
Bild): WSL2 kann seine komplette VM herunterfahren, sobald keine Verbindung
mehr zu ihr besteht (z. B. die letzte RDP-Sitzung/das letzte Terminal
geschlossen wird) — unabhängig von allem, was innerhalb von Linux per
`loginctl enable-linger`/Quadlet eingerichtet ist (dazu unten mehr), da hier
die **gesamte VM** verschwindet, nicht nur der Linux-Login. Nach außen
sichtbar als: GUI sofort nach dem Abmelden vom Server nicht mehr erreichbar,
nach der nächsten Anmeldung dauert es rund eine Minute (VM-Boot +
Container-Neustart), bis sie wieder da ist.

**Zuverlässigste Lösung: eine Windows-Systemaufgabe, die eine dauerhafte
Verbindung zur WSL2-VM offen hält** — dadurch hat die VM unabhängig von
jeder Benutzeranmeldung immer mindestens einen aktiven "Client" und wird
nie als inaktiv eingestuft; als Nebeneffekt startet dieselbe Aufgabe die VM
außerdem automatisch bei jedem Windows-Systemstart, noch bevor sich
überhaupt jemand anmeldet. Wichtig: **NICHT** als `SYSTEM`-Konto ausführen
(live fehlgeschlagen — SYSTEM hat keinen Zugriff auf die WSL-Distribution,
die unter dem interaktiven Benutzerkonto registriert ist, der Task lief
scheinbar, ohne dass jemals ein Prozess in der VM startete) — stattdessen
`LogonType S4U` mit dem tatsächlichen Windows-Benutzerkonto: läuft ebenso
ohne aktive Anmeldung und ohne gespeichertes Passwort, aber mit Zugriff auf
dessen registrierte WSL-Distribution.

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
$action = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d rocky -e sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit 0 -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "HVNB-WSL-KeepAlive" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description `
    "Haelt die WSL2-VM dauerhaft am Leben, unabhaengig von Benutzer-An-/Abmeldung, damit der HVNB-Backup-Container jederzeit erreichbar bleibt."
Start-ScheduledTask -TaskName "HVNB-WSL-KeepAlive"
```

`-d rocky` an den tatsächlichen Distributionsnamen anpassen (`wsl --status`
zeigt die Standard-Distribution). **`-UserId "Administrator"` auf das
tatsächliche, interaktiv angemeldete Windows-Benutzerkonto anpassen** —
`LogonType S4U` funktioniert nur für genau das Konto, unter dem die
WSL-Distribution registriert wurde (`whoami` in PowerShell zeigt den
aktuellen Benutzer). `-ExecutionTimeLimit 0` verhindert, dass
Windows die Aufgabe nach der sonst üblichen Standard-Laufzeit (3 Tage)
automatisch beendet; `-RestartCount`/`-RestartInterval` starten sie neu,
falls der `sleep infinity`-Prozess doch einmal enden sollte.

**Verifizieren -- PFLICHTSCHRITT, nicht überspringen** (live beobachtet:
`Register-ScheduledTask` legt die Aufgabe nur an, startet sie aber NICHT --
ohne den folgenden Check bleibt unbemerkt, dass `Start-ScheduledTask` am
Ende des Registrierungsblocks oben vergessen/übersprungen wurde und der
Task bis zum nächsten Windows-Neustart schlicht nie läuft):

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
Get-ScheduledTask -TaskName "HVNB-WSL-KeepAlive" | Select-Object State
Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Select-Object CommandLine
```

Ein `wsl.exe -d rocky -e sleep infinity`-Prozess (mit genau diesem
Kommandozeilen-Argument, nicht nur ein beliebiger `wsl.exe`-Prozess -- ein
interaktives WSL-Terminal erzeugt ebenfalls einen `wsl.exe`-Eintrag ohne
`-e sleep infinity`, der leicht damit verwechselt wird) muss dauerhaft in
der Liste erscheinen. Fehlt er, hilft `Start-ScheduledTask -TaskName
"HVNB-WSL-KeepAlive"` sofort nach; verschwindet er später wieder (z. B.
nach einem manuellen `wsl --shutdown`), startet ihn `RestartCount`/der
nächste Windows-Start automatisch neu.

> **Stolperstein, live beobachtet:** ein manuelles `Start-ScheduledTask`
> direkt nach der Registrierung (ohne zwischenzeitlichen Neustart) reicht
> **nicht** zuverlässig aus, damit die GUI ein anschließendes Abmelden
> übersteht — obwohl der `wsl.exe -e sleep infinity`-Prozess laut obigem
> Check bereits läuft, kann die GUI trotzdem sofort nach dem Logout
> verschwinden. Ursache nicht abschließend geklärt (vermutlich eine noch
> an die interaktive Sitzung gebundene Restzuordnung der WSL2-VM). Erst
> nach einem echten `Restart-Computer` (siehe Praxistest am Ende dieses
> Abschnitts) greift der `AtStartup`-Trigger unabhängig von jeder
> Anmeldesitzung und die GUI bleibt zuverlässig nach dem Abmelden
> erreichbar — ein Logout-Test direkt nach `Start-ScheduledTask`, ohne
> vorherigen Neustart, kann also fälschlich wie ein Fehlschlag aussehen.

**Zusätzlich, als zweite Absicherungsebene** (schadet nicht, auch wenn die
Aufgabe oben bereits verhindert, dass die VM je als inaktiv gilt): in
`%UserProfile%\.wslconfig` (auf dem Windows Server, NICHT innerhalb von
WSL) das automatische Herunterfahren bei Inaktivität ebenfalls explizit
deaktivieren, in PowerShell:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
@"
[wsl2]
vmIdleTimeout=-1
"@ | Set-Content -Path "$env:USERPROFILE\.wslconfig" -Encoding ascii

wsl --shutdown
```

> Überschreibt die Datei komplett (gleiches Vorgehen wie beim
> Mirrored-Networking-Setup in Abschnitt 8). Existiert dort bereits eine
> `.wslconfig` mit anderen Einstellungen — `Get-Content
> "$env:USERPROFILE\.wslconfig"` vorher prüfen — stattdessen nur die Zeile
> `vmIdleTimeout=-1` unter der bestehenden `[wsl2]`-Sektion ergänzen.

Wird erst nach dem `wsl --shutdown` wirksam (WSL liest `.wslconfig` nur
beim Start einer neuen VM-Instanz) — das beendet kurzzeitig auch den
Container; die obige Systemaufgabe fängt den Neustart automatisch wieder
auf.

**Danach greifen erst die beiden folgenden, rein Linux-internen
Absicherungen** — sie setzen voraus, dass die WSL2-VM (wie oben
sichergestellt) durchgehend läuft. Eine reine Neustart-Richtlinie am
Container (`restart: unless-stopped`) wird bei **rootless** Podman (kein
dauerhafter Root-Daemon wie bei Docker) nicht zuverlässig durchgesetzt.
Zwei getrennte Probleme:

1. Ein Windows-Sleep/Ruhezustand oder `wsl --shutdown` kann die
   `systemd --user`-Instanz des Benutzers komplett beenden — ohne die
   folgende Einstellung startet sie beim nächsten Login zwar neu, aber ohne
   den Container automatisch mitzunehmen.
2. **Wichtiger, live bestätigt:** rootless Podman hat KEINEN Dauerprozess,
   der einen laufenden Container fortlaufend überwacht — stürzt der
   Container-Hauptprozess ab oder wird er anderweitig beendet, während die
   `systemd --user`-Instanz selbst durchgehend weiterläuft, kommt er von
   selbst **nicht** wieder hoch. Eine Neustart-Richtlinie am Container
   bzw. das früher hier dokumentierte
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
# Einmalig, in der WSL2-Distribution -- <benutzername> ist der LINUX-
# Benutzer INNERHALB der Distribution (aus Abschnitt 2, "whoami" zeigt
# ihn), NICHT das Windows-Konto aus Schritt 1 oben (dort z.B.
# "Administrator")!
sudo loginctl enable-linger <benutzername>
```

**Verifizieren -- PFLICHTSCHRITT, nicht überspringen** (live beobachtet:
dieser Schritt wird leicht vergessen, weil die App zu diesem Zeitpunkt
bereits laeuft und alles funktionierend aussieht -- der Fehler zeigt sich
erst bei der naechsten Ab-/Anmeldung):

```bash
loginctl show-user <benutzername> --property=Linger   # muss 'Linger=yes' zeigen
```

Steht dort `Linger=no`, bleibt die `systemd --user`-Instanz (und damit der
Container) nur so lange am Leben, wie eine aktive Anmeldesitzung dieses
Benutzers besteht -- meldet er sich ab, wird die gesamte Instanz beendet,
bei der naechsten Anmeldung startet der Container neu (erkennbar an einer
frischen Zeile "[entrypoint] ... Release <Version>" im Log mit dem
Zeitpunkt der Anmeldung statt des Server-Starts, siehe Troubleshooting-
Tabelle unten). Das ist unabhaengig vom WSL-Keep-Alive-Task oben: selbst
wenn die WSL2-VM selbst durchgehend laeuft, faengt das den fehlenden
Linger-Schalter NICHT auf, da es ein rein Linux-internes systemd-/logind-
Verhalten ist.

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

**Abschließender Praxistest — PFLICHTSCHRITT, nicht überspringen:** die
drei Absicherungen oben (Task, `vmIdleTimeout`, `enable-linger`) wurden
bisher nur einzeln geprüft, nie im Zusammenspiel über einen echten
Windows-Neustart hinweg — genau diese Kombination war beim ersten
Kunden-Deployment die eigentliche Fehlerquelle (Task registriert, aber nie
gestartet; `enable-linger` vergessen), obwohl jeder Einzel-Check für sich
unauffällig aussah. Einmal real durchspielen:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
Restart-Computer
```

Nach dem Hochfahren, **ohne** dich am Server anzumelden (z. B. per
`Test-NetConnection`/Browser von einem anderen Rechner aus) — die GUI muss
innerhalb weniger Minuten von selbst wieder erreichbar sein, ganz ohne
manuelles Eingreifen (kein `wsl -d rocky`, kein `systemctl --user start`):

**Host: ein beliebiger anderer Rechner im Netz (Kontrollzugriff)**

```powershell
Test-NetConnection -ComputerName <Server-IP> -Port 8443
```

Erst danach am Server anmelden und zur Kontrolle:

**Host: HVNB-Server (Windows-Host mit WSL2)**

```powershell
Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Select-Object CommandLine
```

```bash
podman logs --tail 30 hvnb-backup
```

Erwartet: der `wsl.exe -d rocky -e sleep infinity`-Prozess läuft bereits
(vom `AtStartup`-Trigger des Tasks), und die Container-Logs zeigen genau
**eine** Startmeldung `[entrypoint] ... Release <Version>` mit dem
Zeitpunkt des Server-Starts — ein zweiter Start erst beim Anmelden wäre ein
Hinweis, dass `enable-linger` doch nicht griff.

---

**Teil 2: Auf jedem Hyper-V-Clusterknoten**

> Maschine: der jeweilige Hyper-V-Host — alle **PowerShell**-Befehle in
> diesem Teil laufen lokal auf dem Cluster-Knoten selbst (als
> Administrator), **nicht** auf dem HVNB-Server aus Teil 1 — jeder
> PowerShell-Codeblock trägt dafür zusätzlich eine eigene
> **Host:**-Markierung direkt darüber. Eine Ausnahme: der Unterabschnitt
> „Zertifikat-Vertrauen im Container einrichten" weiter unten läuft
> (Bash-Blöcke, ebenfalls markiert) wieder auf dem HVNB-Server, da dort
> der Container selbst konfiguriert wird.

## 10. WinRM auf jedem Hyper-V-Host aktivieren

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
Cluster Name Object (CNO) besitzt, kann wechseln), als Administrator. Die
folgenden vier Schritte wurden live gegen einen echten produktiven
6-Knoten-Cluster durchgespielt — nach jedem Schritt einmal verifizieren,
bevor der nächste beginnt, und den ganzen Ablauf danach für jeden weiteren
Knoten wiederholen.

> **Wichtiger Fund, live an einem echten Cluster gemacht:** die App
> verbindet sich zum CNO zwar mit der Adresse, die beim Hinzufügen des
> Clusters in der GUI eingetragen wurde (Hostname **oder** IP) — für
> **jede tatsächliche VM-Operation** (Discovery, Checkpoints, VHD-Abfragen)
> verbindet sie sich aber zusätzlich **direkt zu jedem einzelnen Knoten,
> und zwar bewusst über dessen Management-IP-Adresse, nie über dessen
> Hostnamen** (`HyperVService._node_management_ips`/`run_discovery` in
> `backend/app/services/hyperv_service.py` — Knotennamen sind vom
> Container aus oft nicht auflösbar, die Management-IPs aus
> `Get-ClusterNetworkInterface` dagegen schon). Das gilt **unabhängig**
> davon, ob der Cluster in der GUI per Hostname oder IP angesprochen
> wurde. Live beobachtet: die CNO-Verbindung per Hostname funktionierte
> einwandfrei (Cluster-Übersicht zeigte "Healthy 6/6"), aber die
> zusätzliche proaktive Erreichbarkeitsprüfung pro Knoten schlug für
> **alle** Knoten mit `SSLCertVerificationError: IP address mismatch, certificate is not valid for '<Knoten-IP>'`
> fehl — weil die zuvor rein hostnamenbasierten Zertifikate keine
> IP-Address-SAN-Einträge enthielten. Konsequenz: **jedes** Knoten-
> Zertifikat braucht immer eine echte IP-Address-SAN für die eigene
> Management-IP, egal welcher Verbindungsweg für den CNO gewählt wird —
> der reine Hostname-Pfad unten ist deshalb nur für einen **einzelnen,
> nicht geclusterten** Hyper-V-Host ausreichend (dort gibt es kein
> Pro-Knoten-Fan-out). Für jeden Failover-Cluster gilt Schritt 2 in der
> `certreq`-Variante als Standard, nicht als Sonderfall.

**Wichtig bei einem Failover-Cluster:** Wird der Cluster in der GUI über
die Adresse des **Cluster Name Object (CNO)** angesprochen (empfohlen,
statt eines einzelnen physischen Knotens — sonst fällt die Verwaltung
beim Ausfall/Failover dieses einen Knotens komplett aus), landet jede
WinRM-Verbindung zum CNO bei genau dem Knoten, der die Cluster-Group
gerade besitzt — je nach Failover-Status kann das **jeder** der Knoten
sein. Das Zertifikat **jedes einzelnen** Knotens muss deshalb zusätzlich
zur eigenen Identität auch die CNO-Adresse als Subject Alternative Name
(SAN) enthalten, sonst schlägt die CNO-Verbindung fehl, sobald die
Cluster-Group auf einen anderen Knoten wechselt. Beispiel: CNO
`svhvclu01.rvm.local`/`10.10.2.10`, Knoten
`svhvhost01.rvm.local`/`10.10.2.11`,
`svhvhost02.rvm.local`/`10.10.2.12`, usw. — **jedes einzelne**
Knoten-Zertifikat braucht sowohl den CNO-Namen als auch die
CNO-IP-Adresse zusätzlich zur eigenen Identität (Hostname **und**
Management-IP) als SAN.

### Schritt 1: WinRM aktivieren, vorhandene Zertifikate prüfen

**Host: Hyper-V-Clusterknoten**

```powershell
# WinRM-Dienst aktivieren (meist bereits per Default aktiv)
Enable-PSRemoting -Force

Get-ChildItem -Path Cert:\LocalMachine\My
```

Ein Failover-Cluster legt dort bereits automatisch erzeugte Zertifikate an
(z.B. `CN=<GUID>.TLS` oder `CN=CLIUSR`) — die sind **nicht** geeignet, das
sind interne Cluster-Kommunikations-/Dienstkonto-Zertifikate, keine
Host-Zertifikate für einen WinRM-Listener. Zertifikate mit dem eigenen
Hostnamen als `CN` (z. B. von einer früheren WinRM-Quick-Config oder
internen PKI) können dagegen bereits brauchbar sein — vor dem Erzeugen
eines neuen Zertifikats lohnt sich ein Blick auf Aussteller, Gültigkeit
und vor allem die SAN-Einträge jedes Kandidaten:

```powershell
Get-ChildItem -Path Cert:\LocalMachine\My | Where-Object { $_.Subject -match $env:COMPUTERNAME } |
    Format-List Subject, Issuer, NotBefore, NotAfter, Thumbprint

# Fuer jeden gefundenen Thumbprint die SAN-Eintraege pruefen:
$candidate = Get-Item Cert:\LocalMachine\My\<Thumbprint>
$candidate.Extensions | Where-Object { $_.Oid.FriendlyName -eq "Subject Alternative Name" } |
    ForEach-Object { $_.Format($true) }
```

Trägt keines der vorhandenen Zertifikate sowohl den eigenen Hostnamen als
auch den CNO-Namen als SAN (live beobachtet: selbst ein frisches,
selbstsigniertes `CN=<hostname>`-Zertifikat hatte nur den eigenen
Hostnamen als SAN, kein zweites ganz ohne SAN-Erweiterung — beide
ungeeignet), weiter mit Schritt 2.

### Schritt 2: Zertifikat erzeugen

**Standardweg für einen Failover-Cluster** (jeder Knoten braucht sowohl
Hostname als auch Management-IP als SAN, siehe Fund oben) — Management-IP
und CNO-IP vorher ermitteln:

```powershell
# Eigene Management-IP (im 'ClusterAndClient'-Netz):
Get-ClusterNetworkInterface | Where-Object { $_.Network.Role -eq 'ClusterAndClient' -and $_.Node -eq $env:COMPUTERNAME } |
    Select-Object Node, Address
# CNO-IP:
Resolve-DnsName <CNO-Hostname>   # oder direkt (Get-ClusterResource -Name "Cluster Name" | Get-ClusterParameter -Name Address).Value
```

**Host: Hyper-V-Clusterknoten**, `certreq` mit einer `.inf`-Datei, die
echte `ipaddress=`-SAN-Einträge erzeugt (`New-SelfSignedCertificate`
schreibt IP-Adressen als **DNS-Typ**-SAN, z. B. `DNS:10.10.2.11`, nicht
als **IP-Address-Typ** — die von dieser App verwendete TLS-Validierung
(Python/OpenSSL) akzeptiert für eine Verbindung per IP aber ausschließlich
echte `IP Address:`-Einträge, ein optisch identischer `DNS:`-Eintrag
genügt **nicht** und führt zu `SSLCertVerificationError: IP address
mismatch`, obwohl die IP scheinbar korrekt im Zertifikat steht — live
zweimal in zwei unterschiedlichen Kundenumgebungen verifiziert):

```powershell
# Auf JEDEM Knoten einzeln ausfuehren, jeweils mit der eigenen $ownIp:
$hostname    = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName
$cnoHostname = "<CNO-Hostname, z.B. svhvclu01.rvm.local>"
$ownIp       = "<eigene Management-IP, z.B. 10.10.2.11>"   # je Knoten unterschiedlich
$cnoIp       = "<CNO-IP, z.B. 10.10.2.10>"                 # bei allen Knoten gleich

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
_continue_ = "dns=$cnoHostname&"
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

**Verifizieren** — `certreq -new` gibt die erzeugten SANs direkt in der
`Installed Certificate`-Ausgabe aus (`Subject: CN=... (DNS Name=...,
DNS Name=..., IP Address=..., IP Address=...)`), zusätzlich zur Kontrolle
per `certutil`:

```powershell
Export-Certificate -Cert $cert -FilePath C:\temp\winrm-$($env:COMPUTERNAME).cer
certutil -dump C:\temp\winrm-$($env:COMPUTERNAME).cer | Select-String "IP Address"
```

Erwartete Ausgabe: **zwei** `IP Address=`-Zeilen (eigene Management-IP und
CNO-IP) — nicht als reiner Text im DNS-Feld, sondern als echter
IP-Address-SAN-Typ.

**Sonderfall: ein einzelner, nicht geclusterter Hyper-V-Host** (kein
Failover-Cluster, kein Pro-Knoten-Fan-out über
`_node_management_ips`/`run_discovery` — der Fund oben betrifft nur echte
Cluster). Hier reicht der einfachere Weg per Hostname, sofern der Host in
der GUI ebenfalls per Hostname angesprochen wird:

```powershell
$hostname = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName
$cert = New-SelfSignedCertificate -DnsName $hostname `
    -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(5)
$cert.Thumbprint
```

Von der internen PKI ausgestellte Zertifikate sind gegenüber einem
selbstsignierten vorzuziehen (siehe Kasten weiter unten) — solange auch
sie die jeweils benötigten SANs tragen (Hostname/IP je nach Szenario oben).

### Schritt 3: HTTPS-Listener anlegen

**Host: Hyper-V-Clusterknoten**

Erst prüfen, ob bereits ein HTTPS-Listener existiert (z. B. von einem
vorherigen Versuch mit falschem Zertifikat):

```powershell
Get-ChildItem WSMan:\localhost\Listener | Format-List Keys, Address, Transport
```

Existiert bereits einer (`Transport=HTTPS`), erst entfernen:

```powershell
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -match "Transport=HTTPS" } |
    Remove-Item -Recurse -Force
```

Neu anlegen:

```powershell
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address * `
    -CertificateThumbprint $cert.Thumbprint -Force
```

**Verifizieren** — die kompakte `Get-ChildItem`-Ansicht zeigt das
gebundene Zertifikat nicht zuverlässig an, deshalb stattdessen:

```powershell
winrm enumerate winrm/config/listener
```

Beim `Transport = HTTPS`-Eintrag muss `CertificateThumbprint` genau dem
in Schritt 2 erzeugten Wert entsprechen.

### Schritt 4: Firewall + CredSSP

**Host: Hyper-V-Clusterknoten**

```powershell
Enable-NetFirewallRule -DisplayGroup "Windows Remote Management"
New-NetFirewallRule -DisplayName "WinRM HTTPS (5986)" -Direction Inbound -Protocol TCP -LocalPort 5986 -Action Allow
```

**Verifizieren:**

```powershell
Get-NetFirewallRule -DisplayName "WinRM HTTPS (5986)"   # Enabled: True erwartet
```

> **Stolperstein:** ein lokaler `Test-NetConnection -ComputerName
> localhost -Port 5986` beweist **nicht** zuverlässig, dass die
> Firewall-Regel für externe Verbindungen tatsächlich greift —
> Loopback-Verkehr (`::1`/`127.0.0.1`) wird von eingehenden
> Windows-Firewall-Regeln in aller Regel gar nicht gefiltert, live
> beobachtet. Immer zusätzlich `Get-NetFirewallRule` selbst kontrollieren,
> oder von einem anderen Rechner im Netz aus testen.

CredSSP nur nötig, wenn `HVNB_WINRM_TRANSPORT=credssp` (der in dieser App
empfohlene Standard, siehe `.env`-Beispiel in Abschnitt 5 — CredSSP
vermeidet das klassische WinRM-„Double-Hop"-Problem, falls ein
Remote-Befehl seinerseits auf ein weiteres Netzwerkziel zugreifen muss).
**Erst prüfen, ob es nicht schon aktiv ist**, bevor der Schritt erneut
ausgeführt wird:

```powershell
Get-WSManCredSSP
```

Steht dort bereits „This computer is configured to receive credentials
from a remote client computer", ist dieser Schritt schon erledigt und
kann übersprungen werden. Andernfalls:

```powershell
Enable-WSManCredSSP -Role Server
```

**Damit ist dieser Knoten fertig.** Schritt 1–4 für JEDEN weiteren
Clusterknoten wiederholen — jeweils mit dessen eigenem Hostnamen, aber
demselben CNO-Namen als zweitem SAN-Eintrag.

> **Host-Wechsel: ab hier zurück auf dem HVNB-Server** (Windows-Host mit
> WSL2, Teil 1) — nicht mehr auf dem Hyper-V-Host. Die folgenden
> Bash-Blöcke laufen in der `rocky`-Shell des HVNB-Servers, da hier der
> Backup-Container selbst konfiguriert wird (nicht der Hyper-V-Host). Nach
> diesem Unterabschnitt geht es unten wieder auf dem Hyper-V-Host weiter.

**Zertifikat-Vertrauen im Container einrichten:** die App validiert das
WinRM-Zertifikat bei HTTPS strikt (`server_cert_validation="validate"`,
siehe `hyperv_service.py`) — der Container kennt eine interne CA oder ein
selbstsigniertes Zertifikat aber standardmäßig nicht, die Verbindung
schlägt sonst trotz korrekt eingerichtetem Listener fehl. Lösung:
`HVNB_WINRM_CA_TRUST_PATH` auf eine PEM-Datei zeigen lassen, die das
Zertifikat als zusätzlich vertrauenswürdig hinterlegt (pywinrm
`ca_trust_path`, additiv zum normalen System-Truststore).

> **Der Restore-Proxy-Host braucht dieselbe WinRM-Einrichtung** wie ein
> Cluster-Knoten (die App spricht ihn per WinRM an, siehe Abschnitt 12,
> Schritt 3): WinRM aktiviert, und je nach `use_https`-Einstellung in der
> GUI entweder ein HTTPS-Listener (5986) mit einem Zertifikat, dessen
> `.pem` in **dasselbe** CA-Trust-Bundle wandert (die Bundle-Datei bzw.
> die GUI-Sektion „WinRM-Zertifikate" gilt für **alle**
> WinRM-HTTPS-Verbindungen, nicht nur die Hyper-V-Hosts), oder — einfacher
> — ein HTTP-Listener (5985) und in der GUI „WinRM über HTTPS" abgewählt,
> dann ist gar kein Zertifikat nötig. Der Proxy ist ein einzelner Host
> ohne CNO/Failover, sein Zertifikat braucht daher nur den einen
> Namen/die eine IP als SAN (Skript-Assistent: Typ „Einzelner Host"),
> und CredSSP wird für ihn nicht benötigt.

> **Komfortweg über die GUI (empfohlen):** unter **Settings →
> WinRM-Zertifikate** gibt es einen Assistenten, der das PowerShell-Skript
> aus Schritt 2–4 erzeugt, die pro Knoten exportierten `.pem`-Dateien per
> Upload entgegennimmt und daraus per Knopfdruck das Bundle schreibt — ohne
> `podman cp`, ohne `.env`-Eintrag und ohne Neustart (das Bundle landet
> unter `/data/winrm-trust/bundle.pem` und wird sofort übernommen, sofern
> `HVNB_WINRM_CA_TRUST_PATH` leer ist). Nutzbar bereits bei einer
> Erstinstallation, bevor der erste Cluster hinzugefügt wird. Der manuelle
> Weg unten bleibt als Fallback und ist für eine **interne CA** weiterhin
> der einfachere (nur den CA-Root einmalig hinterlegen, statt jeden Knoten
> einzeln hochzuladen). Ist `HVNB_WINRM_CA_TRUST_PATH` bereits gesetzt,
> schreibt der GUI-Button in genau diese Datei.

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

**Host: Hyper-V-Clusterknoten**

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
— dafür ist also keine zusätzliche Zeile in der Quadlet-Datei nötig,
die Datei kann direkt per `podman cp` in den laufenden Container gelegt
werden:

```bash
# Auf dem WSL2-Host: Datei(en) z.B. per Windows-Explorer unter
# \\wsl.localhost\<Distro-Name>\home\<Benutzer>\ ablegen, dann:
podman cp ~/winrm-ca.pem hvnb-backup:/etc/hvnb/certs/winrm-ca.pem
```

```bash
# .env der Installation (Abschnitt 5; bei aelteren Installationen ~/hyperv-netapp-backup/.env):
echo "HVNB_WINRM_CA_TRUST_PATH=/etc/hvnb/certs/winrm-ca.pem" >> ~/hvnb/.env
systemctl --user restart hvnb-backup.service
```

Der Container-Betrieb läuft seit Abschnitt 6 über eine Quadlet-Unit, die bei
jedem `restart` unbedingt neu erstellt wird (kein `--force-recreate`-
Sonderfall mehr nötig, siehe Kasten dort) — `.env`-Änderungen wie diese
kommen dadurch zuverlässig an.

Da `hvnb-certs` ein benanntes Volume ist, übersteht die Datei den
Container-Neustart im vorherigen Schritt unabhängig von der Reihenfolge der
beiden Befehle. Settings > Updates zeigt anschließend unter "WinRM
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
> sowie Cluster-Abfragen und -Aktionen (`Get-ClusterSharedVolume`, `Get-ClusterNode`,
> `Add-ClusterVirtualMachineRole`, für "VM verschieben" zusätzlich
> `Move-ClusterVirtualMachineRole`/`Move-ClusterGroup` und `Move-VMStorage`,
> für "CSV vergrößern" `Update-HostStorageCache` und `Resize-Partition`,
> für "Neue CSV" `Get-InitiatorPort`, `Initialize-Disk`/`New-Partition`/`Format-Volume`,
> `Add-ClusterDisk` und `Add-ClusterSharedVolume` (Rückfall auf CredSSP wie beim Host-Move),
> für "CSV löschen" `Remove-ClusterSharedVolume` und `Remove-ClusterResource`,
> für "Neue VM" `New-VM`, `New-VHD`, `Set-VMFirmware`, `Add-VMDvdDrive`, `Get-VMSwitch`, optional
> `Set-VMKeyProtector`/`Enable-VMTPM` (bei SMB3-Ablage per CredSSP),
> für "Remote-Sitzung" `Get-VMNetworkAdapter` (die Konsole selbst öffnet der PC des Benutzers direkt
> zum Knoten auf TCP 2179 -- dieser Port muss vom Benutzer-PC aus erreichbar sein, nicht von der App),
> für "VM-Einstellungen ändern" `Set-VMProcessor`, `Set-VMMemory`, `Add/Remove/Connect/Disconnect-VMNetworkAdapter`,
> `Set-VMNetworkAdapterVlan`, `Resize-VHD`, `New-VHD` und `Update-ClusterVirtualMachineConfiguration`,
> für "VM starten/herunterfahren" `Start-VM`/`Resume-VM`/`Stop-VM`,
> für "VM löschen" `Remove-ClusterGroup` und `Remove-VM` sowie das Löschen der Festplatten-Dateien,
> für die VM-Performance lesend die WMI-Klasse `MSFT_StorageQoSFlow` (Storage QoS des Clusters),
> für die RAM-Anzeige beim Host-Move `Get-CimInstance Win32_OperatingSystem`
> direkt auf jedem Knoten). Für die Disk-/iSCSI-Verwaltung gibt es unter
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

**Host: Hyper-V-Clusterknoten**

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

### Listener auf das Management-Interface binden (empfohlen)

Der Listener wurde oben mit `-Address *` angelegt — er lauscht damit auf
**jedem** Netzwerkadapter des Knotens, nicht nur dem für die App
gedachten Management-Netz. Live auf einem echten Cluster-Knoten geprüft
(`Get-NetAdapter` + `Get-NetIPAddress`): das betrifft in der Praxis
typischerweise auch das iSCSI-Netz und ggf. das Live-Migration-Netz, die
jeweils eigene Adapter mit eigener IP haben — WinRM ist dort also
unnötig erreichbar, obwohl die App nur das Management-Netz braucht.

Eine einzelne IP-Adresse eignet sich als Bindung dabei **nicht** direkt:
bei einem Failover-Cluster liegen auf dem Management-Adapter *zwei*
Adressen gleichzeitig — die eigene, feste Adresse des Knotens **und**
(sofern dieser Knoten die Cluster-Group gerade besitzt) die CNO-Adresse
als zusätzliche IP auf demselben Adapter. `-Address` unterstützt dafür
gezielt eine Bindung **per Netzwerkadapter** (per MAC-Adresse) statt per
einzelner IP — deckt dadurch beide Adressen auf diesem einen Adapter ab,
und bleibt auch nach einem Failover korrekt, da die CNO-Adresse laut
Cluster-Netzwerk-Konfiguration immer auf demselben Adapter erscheint:

**Host: Hyper-V-Clusterknoten**

```powershell
# MAC-Adresse des Management-Adapters ermitteln (der Adapter, der sowohl
# die eigene Knoten-IP als auch -- bei aktivem Besitz -- die CNO-Adresse
# traegt; live am Beispiel eines echten Clusterknotens: Get-NetAdapter |
# Get-NetIPAddress zeigte hier "vNIC-MGMT" mit beiden Adressen 10.93.70.101
# und 10.93.70.100 gleichzeitig, waehrend "iSCSI-1" und "vNIC-LiveMig"
# eigene, davon getrennte Adressen auf anderen Adaptern trugen):
Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object Name, MacAddress, ifIndex
Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress

# Bestehenden Listener entfernen und mit MAC-Bindung neu anlegen (MAC-
# Adresse aus dem obigen Befehl einsetzen):
Get-ChildItem WSMan:\localhost\Listener | Where-Object { $_.Keys -match "Transport=HTTPS" } |
    Remove-Item -Recurse -Force
New-Item -Path WSMan:\localhost\Listener -Transport HTTPS -Address 'MAC:00-15-5D-FB-EE-00' `
    -CertificateThumbprint $cert.Thumbprint -Force
```

> **Vor dem Rollout auf allen Knoten einmal gegenprüfen:** die
> MAC-Adressbindung ist nicht Teil dieser Referenzumgebung-Verifikation
> (das Erzeugen/Ersetzen eines produktiven Listeners war hier bewusst
> nicht risikofrei genug für einen Live-Test) — nach dem Anlegen mit
> `Get-ChildItem WSMan:\localhost\Listener` kontrollieren, dass der
> Listener existiert und mit `Test-NetConnection -ComputerName localhost
> -Port 5986` sowie einmal über die CNO-Adresse testen, **bevor** die
> alte `-Address *`-Regel auf weiteren Knoten ersetzt wird. Schlägt die
> MAC-Syntax fehl, ersatzweise `-Address 'IP:<eigene-IP>'` **und**
> `-Address 'IP:<CNO-IP>'` als zwei separate Listener auf demselben Port
> anlegen (WSMan erlaubt mehrere Listener auf unterschiedlichen
> Adressen) — funktional gleichwertig, nur etwas mehr Pflegeaufwand bei
> einer IP-Änderung.

**Verbindung isoliert testen**, bevor der Cluster in der GUI hinzugefügt
wird — zuerst lokal auf dem Hyper-V-Host selbst:

**Host: Hyper-V-Clusterknoten**

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
aber durch eine VLAN-Trennung vom App-Host aus nicht) trat in der
Referenzumgebung bereits an anderer Stelle auf.

### Alternative zu CredSSP/NTLM: Kerberos

`HVNB_WINRM_TRANSPORT=kerberos` ist eine dritte Option neben `ntlm`
(Standard) und `credssp` — im Gegensatz zu CredSSP ohne dessen
protokollbedingte Empfindlichkeit gegenüber Windows-Sicherheitsupdates
("Encryption Oracle Remediation"), im Gegensatz zu NTLM mit dem
moderneren, von Sicherheits-Audits meist erwarteten Verfahren.

**Kein Domain-Join des Containers nötig.** Die Ticket-Beschaffung
erfolgt zur Laufzeit mit den ohnehin hinterlegten Zugangsdaten (kein
Keytab, keine Sonderbehandlung bei einer Kennwort-Rotation). Das Tool
geht davon aus, dass alle registrierten Hyper-V-Cluster in **derselben**
AD-Domäne liegen — es gibt bewusst nur ein einziges, globales Realm/KDC-
Paar (Settings → Kerberos), keine Konfiguration pro Cluster.

Voraussetzungen:
- Der HVNB-Server erreicht den Domain Controller/KDC über das Netzwerk
  (Port 88 TCP/UDP).
- Uhrzeit-Synchronität zwischen HVNB-Server und KDC (Kerberos toleriert
  standardmäßig nur wenige Minuten Abweichung).
- Die SPNs der Hyper-V-Hosts (`WSMAN/<hostname>`) sind registriert — bei
  aktiviertem WinRM in der Regel automatisch der Fall, im Zweifel per
  `setspn -L <hostname>` auf dem jeweiligen Host prüfen.
- Falls ein Restore-Proxy-Host (Settings → Restore → Setup) im Einsatz
  ist: dessen `address`-Feld ist meist eine IP — Kerberos-SPNs sind aber
  hostnamenbasiert. Dafür zusätzlich das optionale **Hostname**-Feld
  dort mit dem DNS-Namen des Proxy-Hosts befüllen, sonst schlägt die
  Proxy-Verbindung (und damit jeder Restore) unter Kerberos fehl, auch
  wenn der Verbindungstest gegen die Hyper-V-Cluster selbst erfolgreich
  war.

**Kein eigener Image-Build nötig.** Das Release-Image enthält die für
Kerberos nötigen Bibliotheken (`krb5-libs`, `gssapi`) bereits. Kontrolle:

```bash
podman exec hvnb-backup python3 -c "import gssapi; print('Kerberos-Bibliothek vorhanden')"
```

**Einrichtung in der GUI:** Settings → Kerberos → Cluster auswählen →
"Automatisch erkennen" (fragt Realm/KDC live über den aktuell
funktionierenden Transport des gewählten Clusters ab) oder manuell
eintragen → "Verbindung testen" → Speichern.

**Bekannte Einschränkung bei geclusterten VMs (automatisch behandelt,
keine Aktion nötig):** manche Failover-Cluster-Operationen
(`Add-/Remove-VMHardDiskDrive` beim Restore/der VM-Neuerstellung an
einer bereits geclusterten VM, `Add-ClusterVirtualMachineRole` am
Ende einer VM-Neuerstellung, `Move-VMStorage` beim Storage-Move sowie
`Move-ClusterVirtualMachineRole` bei der Live-Migration über "VM
verschieben") brauchen intern einen zweiten Hop zum
Cluster-Dienst, den weder NTLM noch Kerberos ohne AD Constrained
Delegation unterstützen ("Access is denied" bzw.
"Update-ClusterVirtualMachineConfiguration could not be completed").
Die App weicht dafür automatisch temporär auf NTLM bzw. gezielt CredSSP
aus (nur für diese einzelnen Schritte, nicht global) — live verifiziert,
keine zusätzliche Konfiguration nötig. Die Live-Migration versucht es
zuerst mit dem eingestellten Transport und wiederholt nur bei "Access is
denied" einmal per CredSSP; welcher Weg gegriffen hat, steht im
Schritt-Protokoll des Dialogs. Beim Storage-Move entscheidet die App
anhand der tatsächlichen Dateipfade danach, ob er gelungen ist — ein
Fehler, den `Move-VMStorage` erst nach dem Kopieren meldet (Cluster-
Update), wird dort nur als Hinweis angezeigt. Die eigentliche, vollständige
Lösung wäre Kerberos Constrained Delegation in AD für die
WinRM-Dienstkonten der Hyper-V-Knoten (braucht AD-Admin-Zugriff auf den
Domain Controller, aktuell nicht eingerichtet).

**Empfohlener Test vor der produktiven Umstellung:** einen echten
Checkpoint-Erstellen/Entfernen-Zyklus gegen eine unkritische Test-VM
ohne aktive Policy (oder deren Policy vorher kurz pausieren) fahren,
bevor der Transport global umgestellt wird — vermeidet Kollisionen mit
einem parallel laufenden echten Backup-Lauf auf derselben VM. Ad-hoc-
Testskript (Cluster-ID vorher per kurzer DB-Abfrage ermitteln):

```bash
podman exec -i hvnb-backup python3 - <<'EOF'
import sys
sys.path.insert(0, "/opt/app/backend")
import app.main
from app.db.session import SessionLocal
from app.models.hyperv_cluster import HyperVCluster
db = SessionLocal()
for c in db.query(HyperVCluster).all():
    print(c.id, c.name, c.hyperv_cluster_name, c.management_address)
EOF
```

```bash
podman exec -i hvnb-backup python3 - <<'EOF'
import sys, copy
sys.path.insert(0, "/opt/app/backend")
from app.core.config import get_settings
from app.core.crypto import decrypt_secret
from app.core.kerberos_config import ensure_krb5_config_env
from app.core.kerberos_auth import ensure_ccache_env
from app.services.hyperv_service import HyperVService, ConsistencyType
from app.db.session import SessionLocal
from app.models.hyperv_cluster import HyperVCluster

CLUSTER_ID = "HIER-CLUSTER-ID-EINTRAGEN"
VM_NAME = "HIER-TEST-VM-EINTRAGEN"

settings = get_settings()
ensure_krb5_config_env(settings)
ensure_ccache_env(settings)
settings = copy.copy(settings)
settings.winrm_transport = "kerberos"

db = SessionLocal()
cluster = db.query(HyperVCluster).filter(HyperVCluster.id == CLUSTER_ID).first()
password = decrypt_secret(cluster.encrypted_password) if cluster.encrypted_password else ""

cno_service = HyperVService(settings, cluster.management_address, use_https=cluster.use_https, node_hostname=cluster.hyperv_cluster_name)
cno_session = cno_service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)
owner_node = cno_service.get_vm_owner_node(cno_session, VM_NAME)
node_address = cno_service.resolve_node_address(cno_session, owner_node)

node_service = HyperVService(settings, node_address, use_https=cluster.use_https, node_hostname=owner_node)
node_session = node_service.connect(cluster.username, password, read_timeout_sec=15, operation_timeout_sec=10)

cp_name = "hvnb_kerberos_adhoc_test"
info = node_service.create_checkpoint(node_session, VM_NAME, cp_name, ConsistencyType.CRASH_CONSISTENT)
print("Checkpoint erstellt:", info)
result = node_service.remove_checkpoint(node_session, VM_NAME, cp_name)
print("Checkpoint entfernt, success=", result.success, result.error or "")
EOF
```

**Produktiv umstellen:** erst danach `HVNB_WINRM_TRANSPORT=kerberos` in
der `.env` setzen und den Container neu erstellen (`systemctl --user
restart hvnb-backup.service`, siehe Abschnitt 6/9 — eine reine
`.env`-Änderung wird sonst nicht automatisch übernommen). Anschließend
einen echten, planmäßig ausgelösten Backup-Lauf beobachten, bevor das
Ergebnis als endgültig stabil gilt.

**Rückfallebene:** NTLM bleibt jederzeit per Rückstellung von
`HVNB_WINRM_TRANSPORT=ntlm` + Container-Neustart verfügbar, falls an
einem einzelnen Knoten doch etwas nicht greift (z. B. fehlende SPN).

---

**Teil 3: Cluster/Storage in der App registrieren**

> Ab hier braucht es sowohl Teil 1 (Container läuft auf dem HVNB-Server) als
> auch Teil 2 (WinRM auf den betroffenen Hyper-V-Hosts aktiv) abgeschlossen
> — die folgenden Schritte laufen im Browser gegen die Web-GUI auf dem
> HVNB-Server.

## 11. Erste Anmeldung

```
https://<Server-IP-oder-Name>:8443
Benutzer: admin
Passwort: <HVNB_INITIAL_ADMIN_PASSWORD aus Abschnitt 5>
```

Sofort nach dem ersten Login unter **Settings > Benutzer & Rollen** das
Admin-Passwort ändern.

Health-Check (auch ohne Login abrufbar, praktisch für Monitoring):

```bash
curl -sk https://<Server-IP>:8443/api/health
# {"status":"ok","app":"AU Storage Manager for Hyper-V"}
```

## 12. Nächste Schritte (in der GUI)

Die Applikation ist jetzt lauffähig, aber fachlich noch leer.

**Umzug oder Wiederaufbau einer bestehenden Installation:** statt der
folgenden Schritte die Konfiguration des alten Servers importieren
(Settings → System → "Konfiguration importieren", siehe Abschnitt 13) und
danach nur die in der Vorschau aufgelisteten Kennwörter nachtragen.

Für eine komplett neue Einrichtung folgen über die Web-GUI (in dieser
Reihenfolge sinnvoll):

1. **Settings > Hyper-V-Hosts** — Hyper-V-Cluster hinzufügen (braucht Teil 2
   auf jedem betroffenen Knoten abgeschlossen)
2. **Storage > Systeme** — NetApp-Cluster hinzufügen
3. **Restore > Setup** — Restore-Proxy-Host + iSCSI-Infrastruktur einrichten
   (Voraussetzung für jeden Restore-Vorgang)
4. **Backup > Policies / Protection Groups / Zeitpläne** — Backup-Regeln
   definieren. Optional **Backup > Schutzklassen** — Klassen (z. B.
   Gold/Silber/Bronze) mit maximalem Backup-Alter und Aufbewahrung je Stufe
   anlegen und VMs, CSVs und SMB3-Freigaben zuweisen; die App prüft danach,
   ob jedes Objekt seiner Klasse entsprechend gesichert wird
5. **Settings > Active Directory** (falls gewünscht) — Server, Domäne,
   Base DN und ein Lese-Service-Konto für die AD-Benutzersuche werden
   direkt in der GUI konfiguriert (kein `.env`/Neustart nötig). Danach
   können AD-Benutzer über "Benutzer hinzufügen" gesucht und mit einer
   Rolle versehen werden, unabhängig von lokalen Konten, die weiterhin
   funktionieren. **Settings > E-Mail** (Alerting) — optional
6. **Settings > Standorte** (optional, bei zwei Rechenzentren bzw.
   MetroCluster) — Standorte anlegen (z. B. DC1/DC2), jedem Hyper-V-Knoten
   manuell und jedem NetApp-System einen Standort zuweisen; jede CSV erbt
   den Standort ihres NetApp-Systems über die LUN-Seriennummer und kann
   einzeln abweichend festgelegt werden. Danach markiert Inventory > VMs
   jede VM, deren Host an einem anderen Standort steht als ihr Storage,
   inkl. Alarm nach einer Karenzzeit (Settings > Alarms, Standard 120 min;
   während eines MetroCluster-Switchovers ausgesetzt) und "Beheben"-Aktion.
   Damit die Knotenliste vollständig ist, einmal den Health-Check laufen
   lassen (Verify-Button beim Cluster).
7. **Settings > DB-Sicherung** (empfohlen) — tägliche Sicherung der
   App-Datenbank auf eine CIFS-Freigabe einrichten (siehe Abschnitt 13);
   ohne sie geht bei einem Verlust des Servers auch der Backup-Katalog
   verloren
8. **Reports** (optional) — Reports auf Knopfdruck erzeugen oder als
   Vorlage mit Zeitplan und Mailversand speichern (braucht Settings >
   E-Mail)
9. **Settings > Hintergrundjobs** — Intervalle prüfen, u. a. den
   VM-Performance-Sammler (Standard alle 5 min, 0 = aus); die Storage-Seite
   unter Monitoring > Performance braucht keine Einrichtung

Eine funktionale Architekturübersicht ist direkt in der Applikation über
den Link in der Fußzeile erreichbar.

## 13. Betrieb

### Updates

Die App lädt selbst nichts nach. Jedes Update tauscht das komplette Image
gegen eine neue Version; das erledigt auf dem Server das Skript
`hvnb-update`. Für alle Wege gilt:

- Vorher wird die Datenbank im laufenden Betrieb gesichert
  (`/data/update-backups` im Daten-Volume, die letzten fünf bleiben).
- Solange Backups oder Restores laufen, wird nicht aktualisiert.
- Antwortet die neue Version nach dem Start nicht, läuft automatisch wieder
  die vorherige.
- Die GUI ist etwa eine halbe bis eine Minute nicht erreichbar; alle
  Benutzer bleiben angemeldet.

| Weg | Wofür | Internet am Server | Auslöser |
|---|---|---|---|
| Paketdatei auf dem Server | immer möglich, auch als Rückfallweg | nein | `hvnb-update <Paket>` |
| Paketdatei in der GUI hochladen | Produktion ohne Internet, ohne Shell | nein | Settings > Updates > „Jetzt einspielen" |
| Online-Update aus der Registry | Server mit Zugang zu `ghcr.io` | nur zur Registry | Knopf in der GUI, auf Wunsch automatisch |
| Update aus Git | **nur** Entwicklungsumgebungen | ja | Knopf in der GUI, auf Wunsch automatisch bei jedem Commit |

Die Kurzfassung dieser Wege steht auch in der App unter Settings > Updates
(„So wird diese Installation aktualisiert").

**Stand ansehen:**

```bash
hvnb-update --status
```

Zeigt Image, laufende Version, vorheriges Image, vorhandene
Datenbank-Sicherungen, den Zustand des Update-Dienstes und eine hinterlegte
Registry.

**Weg 1 — Paketdatei auf dem Server.** Paket und Prüfsummendatei auf den
Server kopieren (Abschnitt 4), dann:

```bash
hvnb-update ~/hvnb/release/hvnb-<Version>.tar.gz
```

Mit `--force` wird auch eingespielt, wenn noch Backups laufen (nicht
empfohlen: ein unterbrochenes Backup hinterlässt Checkpoints).

**Weg 2 — Paketdatei in der GUI hochladen.** Voraussetzung ist der
Update-Dienst (`hvnb-update --install-agent`, Abschnitt 6); unter
Settings > Updates steht er dann als „aktiv". Dort Paketdatei und
Prüfsummendatei auswählen, „Hochladen und prüfen", die angezeigte Version
kontrollieren und „Jetzt einspielen". Das Ergebnis samt Protokoll erscheint
nach dem Neustart auf derselben Seite; jeder Upload und jedes Einspielen
steht im System-Log und im Änderungsprotokoll. Nötiges Recht:
Settings verwalten (Administrator).

> **Sicherheit:** Pakete sind nicht signiert. Wer ein Administrator-Konto
> der App übernimmt, kann über den Upload ein eigenes Image einspielen, das
> Zugriff auf alle hinterlegten Zugangsdaten hat. Wer das ausschließen
> will, richtet den Update-Dienst nicht ein (`hvnb-update
> --uninstall-agent`) und spielt Updates nur per Befehl auf dem Server ein.

**Weg 3 — Online-Update aus der Registry.** Einmalig auf dem Server:

```bash
podman login ghcr.io          # nur bei privaten Images; Token mit Leserecht auf Packages
hvnb-update --set-registry ghcr.io/<konto>/hvnb-backup
hvnb-update --check-registry
```

Danach erscheint unter Settings > Updates die Karte „Online-Update aus der
Registry" mit „Nach Update suchen" und „Version X einspielen" (braucht
ebenfalls den Update-Dienst). Der Schalter „automatisch einspielen" ist
standardmäßig aus; eingeschaltet prüft der Server stündlich und spielt eine
neuere Version von selbst ein. Auf dem Server direkt:

```bash
hvnb-update --from-registry            # die neueste veroeffentlichte Version
hvnb-update --from-registry 1.2.0      # eine bestimmte Version
```

Die Registry-Anmeldung liegt nur auf dem Server; die App bekommt nie
Zugangsdaten. Eingestellt wird immer ein fester Versions-Tag, nie `latest`.
`hvnb-update --set-registry none` schaltet den Weg wieder ab.

**Weg 4 — Update aus Git (nur Entwicklungsumgebung).** Der Server sieht in
einem Git-Branch nach, baut den neuesten Commit selbst als Image (Version
`<letzter Tag oder 0.0.0>-dev.<Anzahl Commits>.<Commit>`) und spielt es ein.
Von selbst passiert dabei nichts: unter Settings > Updates gibt es „Nach
Updates prüfen" und, sobald ein neuer Commit gefunden wurde, „Jetzt
einspielen" (Build etwa fünf Minuten, die App läuft so lange weiter). Der
Schalter „automatisch einspielen" ist standardmäßig aus; eingeschaltet
prüft der Server im eingestellten Intervall und spielt jeden neuen Commit
von selbst ein. Voraussetzungen: `git` auf dem Server, Internetzugang
(Git-Server, `quay.io`, npm, PyPI), der Update-Dienst.

Einrichten in der GUI: Settings > Updates > „Update aus Git einrichten" — Git-Adresse, Branch und Prüfintervall eintragen. Bei einer
SSH-Adresse (`git@github.com:konto/repo.git`) muss der Server-Benutzer das
Repository per Schlüssel erreichen; alternativ eine HTTPS-Adresse mit
Token (`https://benutzer:token@github.com/konto/repo.git`). Das Token wird
in der GUI nicht wieder angezeigt und liegt auf dem Server in
`~/.config/hvnb/hvnb-backup.git-autoupdate.conf` (nur für den Benutzer
lesbar). Ändern und Entfernen stehen auf derselben Seite. Auf dem Server
direkt:

```bash
hvnb-git-autoupdate --install --repo <Git-Adresse> --branch master --interval 5
hvnb-git-autoupdate --check        # nachsehen, ob es einen neuen Commit gibt
hvnb-git-autoupdate --update-now   # den neuesten Commit bauen und einspielen
hvnb-git-autoupdate --status
hvnb-git-autoupdate --uninstall
```

Im automatischen Betrieb wird ein Commit, dessen Build oder Einspielen
scheitert, nicht von selbst erneut versucht — erst der nächste Commit oder
ein „Jetzt einspielen" löst wieder aus. Arbeitsordner und
Protokoll: `~/.local/share/hvnb/hvnb-backup-git-autoupdate/` (`last-run.log`).

> **Nicht für Produktion:** gebaut wird ungeprüft der jeweils neueste
> Commit, und wer in der App das Recht „Settings verwalten" hat, bestimmt
> damit, aus welchem Repository der Server Code baut. Fehlt `git` auf dem
> Server, bietet die GUI die Einrichtung gar nicht an.

**Zurück auf die vorherige Version:**

```bash
hvnb-update --rollback             # nur das Image
hvnb-update --rollback --with-db   # zusaetzlich die Datenbank von vor dem letzten Update
```

`--with-db` verwirft alles, was seit dem Update in der App passiert ist
(Backup-Läufe, Einstellungen); der Stand unmittelbar vor dem Zurückspielen
wird als `vor-rollback-…sqlite` daneben gesichert. Ist ein automatischer
Weg eingeschaltet (Schalter „automatisch einspielen" bei Registry oder Git), ihn **vor** dem Rollback
ausschalten — sonst spielt der Server die neuere Version beim nächsten Lauf
wieder ein.

**Ein Release erzeugen** (Entwicklungsrechner): entweder lokal mit
`scripts/build-release.sh <Version>` (Abschnitt 4a) oder über GitHub — ein
Versions-Tag startet dort den Build, legt das Image in die Registry
(`ghcr.io/<konto>/hvnb-backup:<Version>`) und die vier Paketdateien am
GitHub-Release ab:

```bash
git tag v1.2.0
git push <remote> v1.2.0
```

Ein Push auf `master` allein löst kein Release aus.

**Konfiguration sichern / auf einen neuen Server übertragen:** Settings →
System → "Konfiguration exportieren" lädt die komplette Einrichtung als ZIP
herunter (Cluster, NetApp-Systeme, Restore-Setup, Policies, Protection
Groups, Zeitpläne, Schutzklassen samt Zuordnungen, Report-Vorlagen,
Standorte, alle Settings, WinRM-Zertifikate, Benutzer und Rollen), optional mit dem Backup-Katalog, damit ältere NetApp-Snapshots
auf dem neuen Server weiter als Wiederherstellungspunkte auswählbar sind.
**Kennwörter, NetApp-Client-Zertifikate und Kennwörter lokaler Benutzer
sind nie enthalten** — der Export ist dadurch unabhängig von
`HVNB_SECRET_KEY`. Import auf dem neuen Server (frische Installation nach
Abschnitt 1–11) unter Settings → System, nur solange dort noch nichts
eingerichtet ist. Danach die in der Vorschau aufgelisteten Kennwörter
nachtragen, Discovery laufen lassen und die beim Import automatisch
pausierten Protection Groups wieder aktivieren. Nicht enthalten sind
Discovery-Daten (holt der neue Server selbst), Alarme, System Log und
Kapazitätsverlauf.

**Automatische DB-Sicherung:** Settings → DB-Sicherung sichert die
komplette Datenbank (Einrichtung, Backup-Katalog, Historie) täglich auf eine
CIFS-Freigabe (UNC-Pfad + Konto, z. B. `DOMAIN\svc-hvnb-dbbackup`) und
behält zusätzlich die letzten Kopien lokal unter `/data/db-backups` im
Volume `hvnb-data`. Die Dateien heißen
`hvnb-db-<kennung>-<JJJJMMTT-HHMMSS>.sqlite.gz`; die **Kennung** wird in den
Einstellungen vergeben (Standard `hvnb`, z. B. `prod`) — bewusst nicht der
Hostname, denn im Container ist das die Container-ID, die sich bei jedem
Neuerstellen ändert. Auf der Freigabe löscht die Aufbewahrung nur Dateien
mit der eigenen Kennung; teilen sich mehrere Installationen einen Ordner,
bekommt jede eine eigene Kennung.
Fehlgeschlagene bzw. seit mehr als 36 h ausbleibende Sicherungen erscheinen
als Alarm. Der Container bindet die Freigabe nicht ein (rootless), sondern
schreibt direkt per SMB 2/3 (Python-Paket `smbprotocol`, wird wie alle
Abhängigkeiten beim Start per `pip` installiert). Die Freigabe sollte nur
für dieses Konto les- und schreibbar sein.

> **`HVNB_SECRET_KEY` getrennt verwahren.** Die Kennwörter in der Sicherung
> sind mit diesem Schlüssel verschlüsselt, der Schlüssel selbst wird
> bewusst nicht mitgesichert. Ohne ihn ist eine Sicherung nur noch ohne
> Kennwörter brauchbar.

**Wiederherstellen per GUI:** Settings → DB-Sicherung → "Sicherungen
anzeigen" → Wiederherstellen (oder eine `.sqlite.gz`-Datei hochladen). Die
App prüft die Datei vorab (Integrität, eigene Datenbank, Kennwörter mit dem
aktuellen `HVNB_SECRET_KEY` lesbar) und sperrt, solange noch Backups,
Restores, Datei-Restore-Sitzungen oder VM-Verschiebungen laufen. Der
aktuelle Stand wird vorher lokal als `…-vor-restore.sqlite.gz` gesichert,
danach startet die App neu und alle Benutzer melden sich neu an. Auf einem
**neuen Server**: frisch installieren (Abschnitt 1–11) **mit dem alten
`HVNB_SECRET_KEY`**, unter DB-Sicherung dieselbe Freigabe eintragen,
Sicherung auswählen, wiederherstellen.

**Wiederherstellen ohne GUI** (falls die App selbst nicht mehr startet):

```bash
# In der WSL2-Distribution; Datei vorher z. B. von der Freigabe holen
gunzip -k hvnb-db-<kennung>-<zeitstempel>.sqlite.gz
systemctl --user stop hvnb-backup.service
DATA=$(podman volume inspect hvnb-data --format '{{.Mountpoint}}')
podman unshare cp "$DATA/app.db" "$DATA/app.db.vor-restore"
podman unshare cp hvnb-db-<kennung>-<zeitstempel>.sqlite "$DATA/app.db"
systemctl --user start hvnb-backup.service
```

Alternative für einen 1:1-Servertausch inklusive aller Historie: die beiden
Podman-Volumes `hvnb-data` und `hvnb-certs` plus die `.env` kopieren — dann
**muss `HVNB_SECRET_KEY` identisch mitkommen**, sonst sind alle
gespeicherten Kennwörter unbrauchbar (äußert sich als "Verbindung
fehlgeschlagen" bei allen Clustern).

**Logs:**

```bash
podman logs --tail 100 hvnb-backup           # supervisord-Gesamtausgabe
podman exec hvnb-backup tail -f /var/log/hvnb/uvicorn.log
journalctl --user -u hvnb-backup-update-agent.service -n 50   # Update-Dienst
cat ~/.local/share/hvnb/hvnb-backup-git-autoupdate/last-run.log   # Update aus Git, falls eingerichtet
```

Innerhalb der Applikation zusätzlich das **System Log** (Menü > Monitoring)
für Backup-/Restore-/Scheduler-Ereignisse mit wählbarem Zeitraum.

**Troubleshooting-Kurzreferenz:**

| Symptom | Wahrscheinliche Ursache | Abschnitt |
|---|---|---|
| GUI sofort nach Abmelden vom Server nicht mehr erreichbar, nach Anmeldung nach ~1min wieder da | `HVNB-WSL-KeepAlive`-Systemaufgabe fehlt, laeuft (faelschlich) als SYSTEM statt per S4U, oder wurde nach `Register-ScheduledTask` nie per `Start-ScheduledTask` tatsaechlich gestartet -- WSL2 faehrt die ganze VM beim Trennen der letzten Verbindung herunter | 9 |
| GUI nach Abmelden weg, OBWOHL der `HVNB-WSL-KeepAlive`-Task nachweislich laeuft (`wsl.exe ... -e sleep infinity`-Prozess vorhanden) -- Log zeigt eine frische Startmeldung "[entrypoint] ... Release <Version>" mit dem Zeitpunkt der naechsten Anmeldung | `loginctl enable-linger <benutzername>` fehlt -- die `systemd --user`-Instanz (und damit der Container) endet trotz laufender WSL2-VM beim Abmelden, weil sie an die Login-Sitzung gebunden ist. Mit `loginctl show-user <benutzername> --property=Linger` pruefen (muss `Linger=yes` zeigen) | 9 |
| `wsl --install ...` (auch `--from-file`) scheitert mit `HCS_E_HYPERV_NOT_INSTALLED` / "WSL2 is unable to start since virtualization is not enabled on this machine" | Der HVNB-Server läuft selbst als VM, und deren Hypervisor exponiert keine verschachtelte Virtualisierung an den Gast. Bei Hyper-V: `Set-VMProcessor -VMName <VMName> -ExposeVirtualizationExtensions $true` auf dem **Host** (VM vorher ausschalten). Bei VMware: Edit Settings → CPU → "Expose hardware assisted virtualization to the guest OS" (VM vorher ausschalten) | 1, 2 |
| GUI von aussen nicht erreichbar, Container läuft | WSL2-Guest-IP hat sich geändert, Portproxy zeigt ins Leere | 8 |
| Container nach Server-Neustart als `Exited`/gar nicht gestartet | `loginctl enable-linger` fehlt, oder die Quadlet-Datei fehlt/wurde nicht per `daemon-reload` eingelesen | 6, 9 |
| Container stoppt/stürzt ab und kommt nicht von selbst wieder hoch | Der Container wurde von Hand gestartet (`podman run`/`podman-compose up -d`) statt über die Quadlet-Unit (kein Dauer-Daemon in rootless Podman) | 6, 9 |
| `hvnb-update` meldet „Prüfsumme stimmt nicht" | Paket beim Übertragen beschädigt, oder Prüfsummendatei gehört zu einer anderen Version | 4 |
| `hvnb-update` bricht mit „In der App laufen noch … Backup-/Restore-Vorgänge" ab | Gewollt: laufende Vorgänge abwarten und erneut starten (ein hochgeladenes Paket bleibt dafür liegen) | 13 |
| Settings > Updates zeigt den Update-Dienst als „nicht aktiv" | `hvnb-update --install-agent` wurde nie ausgeführt, oder der Timer läuft nicht: `systemctl --user status hvnb-backup-update-agent.timer` | 6, 13 |
| `hvnb-update --install-agent` meldet „Job for hvnb-backup-update-agent.service failed" | Ältere Skript-Fassung auf einem Daten-Volume, das vom früheren Image angelegt wurde (dessen Wurzel gehört einem Container-Benutzer). Aktuelles `hvnb-update` aus dem Release verwenden und `--install-agent` erneut ausführen | 6 |
| Update aus Git: Zustand „fehlgeschlagen" in der GUI | Build oder Einspielen des Commits gescheitert; Ursache in `~/.local/share/hvnb/hvnb-backup-git-autoupdate/last-run.log`. Erneut versuchen mit „Jetzt einspielen" | 13 |
| Online-Update: „Registry nicht lesbar" | Kein Netzwerkzugang zu `ghcr.io`, oder bei privatem Image fehlt `podman login ghcr.io` für den Benutzer des Containers | 13 |
| Health-Check liefert `502 Bad Gateway` kurz nach Neustart | uvicorn/nginx starten noch, wenige Sekunden abwarten | — |
| `curl https://localhost:8443/...` bricht mit `TLS connect error`/`SSL_ERROR_SYSCALL` ab, `https://127.0.0.1:8443/...` funktioniert dagegen einwandfrei | `localhost` löst zuerst zu IPv6 (`::1`) auf -- `podman port` mapped nur IPv4 (`0.0.0.0:8443`), auf `[::1]:8443` reagiert etwas anderes (oder eine pasta-Eigenheit). Kein Fehler im Container selbst, immer die IPv4-Adresse explizit testen | — |
