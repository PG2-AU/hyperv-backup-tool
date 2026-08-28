# Installation: Hyper-V NetApp Backup auf dediziertem Windows Server

Diese Anleitung entsteht live beim Ersteinrichten einer produktiven Instanz auf
einem dedizierten, domain-joined Windows Server (statt Entwickler-Workstation).
Jeder Schritt ist gegen die reale Umgebung verifiziert, bevor er hier landet.

## Voraussetzungen (erledigt)

- [x] Windows Server 2025, Domain-Join
- [x] Netzwerkzugriff zu NetApp-Management, iSCSI-Datennetz, Hyper-V-Knoten (WinRM/SMB)
- [x] WSL2 aktiviert, Rocky Linux 10 Distro installiert (Hostname z.B. `svaudemo7-hvnb`)

Hinweis: Die Test-/Referenzumgebung dieser Session lief auf Rocky Linux **9**;
die produktive VM nutzt Rocky Linux **10.2**. Das Container-Image selbst baut
weiterhin auf `rockylinux:9` (im Dockerfile), das ist unabhaengig von der
Host-Rocky-Version -- unkritisch, aber im Hinterkopf behalten, falls
Host-Paketnamen zwischen 9 und 10 abweichen.

Vorgefunden auf der frischen Rocky-10-VM: `git` und `podman` **nicht**
installiert, `python3` (3.12.13) vorhanden.

## Schritt 1: Basis-Pakete installieren

```bash
sudo dnf install -y git podman
sudo dnf install -y python3-pip || true
sudo pip3 install podman-compose
```

Verifiziert: `git version 2.52.0`, `podman version 5.8.2`, `python3 3.12.13`.

## Schritt 2: Repository uebertragen

**Wichtige Erkenntnis:** Der SSH-Weg vom Entwickler-Host (10.81.x, Buero-/
Client-Netz) zur produktiven VM (10.93.70.x, Hyper-V-/Storage-VLAN)
funktioniert nicht -- kein Routing zwischen den Netzsegmenten (kein Ping,
keine TCP-Verbindung), unabhaengig von Windows-Firewall-Profilen. Das ist
dieselbe VLAN-Trennung, die schon beim NetApp-iSCSI-Datennetz aufgefallen
war. Portweiterleitung/Firewall-Regeln auf dem Entwickler-Host sind daher
NICHT der Loesungsweg fuer die Erstbefuellung.

**Verwendeter Workaround:** RDP-Zugriff auf die produktive VM funktioniert
(bestaetigt). Ein `git bundle` (einzelne Datei mit dem kompletten
Repo-Verlauf) wird auf dem Entwickler-Host erzeugt und per RDP-Dateitransfer
auf die VM kopiert -- kein Netzwerkpfad zwischen den Hosts noetig.

Auf dem Entwickler-Host (einmalig, bei jeder gewuenschten Aktualisierung
wiederholbar, bis eine dauerhafte Loesung -- z.B. echter Git-Server oder
Netzwerk-Routing -- steht):

```bash
git clone --bare /home/admin/hyperv-repo.git /tmp/hvnb_bundle_src
cd /tmp/hvnb_bundle_src
git bundle create hyperv-netapp-backup.bundle --all
```

Auf der produktiven VM, nachdem `hyperv-netapp-backup.bundle` per RDP
uebertragen wurde:

```bash
git clone --bare ~/hyperv-netapp-backup.bundle ~/hyperv-repo.git
```

Das erzeugt dort ein eigenes Bare-Repo, aus dem der Container (wie auf dem
Entwickler-Host) per `file:///srv/git/...` auto-updatet. Kuenftige
Code-Updates muessen bis zur Klaerung des Netzwerkpfads bzw. eines echten
Git-Servers erneut per Bundle+RDP eingespielt werden (`git bundle` erneut
erzeugen, uebertragen, in der VM: `git -C ~/hyperv-repo.git fetch
~/hyperv-netapp-backup.bundle 'refs/*:refs/*'`).

Der SSH-Deploy-Key (`hvnb_git_deploy`) wird fuer diesen Weg nicht mehr
gebraucht, bleibt aber fuer eine spaetere Netzwerkloesung nutzbar.

## Schritt 3: Arbeitskopie, .env, Container-Build

```bash
git clone ~/hyperv-repo.git ~/hyperv-netapp-backup
cd ~/hyperv-netapp-backup

cat > .env << 'EOF'
HVNB_ENVIRONMENT=development
HVNB_SECRET_KEY=local-dev-secret-not-for-production
HVNB_INITIAL_ADMIN_PASSWORD=password123

HVNB_AD_ENABLED=false

HVNB_ONTAP_VERIFY_SSL=true
HVNB_ONTAP_IS_METROCLUSTER=true

HVNB_WINRM_TRANSPORT=ntlm
HVNB_WINRM_USE_HTTPS=true
HVNB_WINRM_PORT=5986

HVNB_TLS_CERT_PATH=/etc/hvnb/certs/server.crt
HVNB_TLS_KEY_PATH=/etc/hvnb/certs/server.key
EOF

sudo dnf install -y python3-pip || true
sudo pip3 install podman-compose

podman-compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

Verifiziert auf `svaudemo7-hvnb` (10.93.70.13): Build + Start erfolgreich,
`nginx`/`updater`/`uvicorn` laufen sauber, kein `iscsid` (korrekt entfernt).
Selbstsigniertes TLS-Zertifikat wurde automatisch erzeugt.

## Schritt 4: Externe Erreichbarkeit (WSL2-NAT-Modus)

Getestet: `networkingMode=mirrored` (`.wslconfig`) scheitert auf diesem
Windows Server 2025 mit `CreateInstance/CreateVm/ConfigureNetworking/
0x803b0015` und faellt auf `networkingMode=None` zurueck (WSL2 komplett ohne
Netzwerk) -- **nicht verwenden auf dieser Plattform**. `.wslconfig` wieder
entfernt, `wsl --shutdown`, zurueck auf Standard-NAT.

Im NAT-Modus lauscht der von WSL2 automatisch erzeugte Relay nur auf
`[::1]:8443` (IPv6-Loopback) -- von aussen nicht erreichbar, selbst mit
korrekter Windows-Firewall-Regel. Loesung: manuelle Portweiterleitung direkt
auf die WSL2-Guest-IP, analog zum SSH-Workaround auf dem Entwickler-Host:

```powershell
# WSL2-Guest-IP ermitteln (aendert sich bei jedem wsl --shutdown/Neustart!)
# in der Rocky-Shell: ip -4 addr show eth0

New-NetFirewallRule -DisplayName "HVNB HTTPS (8443)" -Direction Inbound -Protocol TCP -LocalPort 8443 -Profile Domain,Private,Public -Action Allow
netsh interface portproxy add v4tov4 listenport=8443 listenaddress=0.0.0.0 connectport=8443 connectaddress=<WSL2-Guest-IP>
```

Verifiziert von einem externen Host (10.81.50.172) gegen 10.93.70.13:8443:
`TcpTestSucceeded: True`, `/api/health` liefert `{"status":"ok",...}`.

**Bekannte Einschraenkung:** Die `netsh portproxy`-Regel ist an die
WSL2-Guest-IP gebunden, die sich bei jedem `wsl --shutdown` oder
Windows-Neustart der VM aendert. Bis eine dauerhafte Loesung steht
(Mirrored-Networking ist auf dieser Plattform nicht nutzbar), muss die Regel
nach jedem Neustart mit der neuen IP neu gesetzt werden:

```powershell
netsh interface portproxy delete v4tov4 listenport=8443 listenaddress=0.0.0.0
netsh interface portproxy add v4tov4 listenport=8443 listenaddress=0.0.0.0 connectport=8443 connectaddress=<neue-WSL2-IP>
```

## Schritt 5: Container-Persistenz absichern (rootless Podman + WSL2)

**Beobachtetes Problem:** Der Container wurde mehrfach beobachtet als
`Exited (0)` vorgefunden (kein Absturz/OOM, `podman inspect` zeigte
`Error=""`), GUI dadurch nicht erreichbar. `docker-compose.yml` setzt zwar
bereits `restart: unless-stopped` -- das reicht bei **rootless** Podman
(kein dauerhafter Root-Daemon wie bei Docker) aber nicht automatisch aus:
die Restart-Policy wird nur durchgesetzt, solange die systemd--user-Instanz
des Benutzers laeuft. Unter WSL2 kann diese durch einen Windows-Sleep/
Ruhezustand oder einen `wsl --shutdown` unterbrochen werden; ohne aktives
Login-Session-Aequivalent (Lingering) faehrt systemd die Benutzerprozesse
inkl. Container dann herunter, statt sie neu zu starten.

**Fix, verifiziert auf `svaudemo7-hvnb`:**

```bash
# Erlaubt der systemd--user-Instanz von 'admin', unabhaengig von einer
# aktiven Login-Session zu laufen (auch nach Reboot/WSL2-Neustart) --
# ohne das wuerde rootless Podman keine Restart-Policy durchsetzen koennen.
sudo loginctl enable-linger admin

# Startet beim (Re-)Start der systemd--user-Instanz automatisch alle
# Container mit passender Restart-Policy neu (podman-eigener Mechanismus,
# analog zu dockerd's eingebautem Verhalten) -- deckt genau den Fall ab,
# dass die Container-Liste nach einem Aussetzer leer ist.
systemctl --user enable podman-restart.service
```

Danach: `podman ps -a` zeigte den Container weiterhin als gestoppt vor
(Policy greift erst beim naechsten systemd--user-Start/Boot, nicht
rueckwirkend) -- einmalig manuell mit `podman start hvnb-backup`
nachgeholt. Ob das WSL2-Aussetzer-Muster damit vollstaendig behoben ist,
zeigt sich erst beim naechsten tatsaechlichen Host-Sleep/Neustart.

**Status-Check / manueller Eingriff bei Bedarf:**

```bash
podman ps -a --filter name=hvnb-backup   # Status pruefen
podman logs --tail 50 hvnb-backup        # Logs ansehen
podman start hvnb-backup                 # falls doch einmal gestoppt
```

## Offene Schritte

- [x] `/api/health` bestaetigt (lokal und extern)
- [x] Login im Browser (`https://10.93.70.13:8443`, admin/password123) bestaetigt
- [ ] Dauerhafte Loesung fuer externe Erreichbarkeit nach Neustarts (Skript
      das den Portproxy beim VM-Boot automatisch mit der aktuellen WSL2-IP
      neu setzt, z.B. als Scheduled Task)
- [ ] Dauerhafte Loesung fuer Code-Sync klaeren (echter Git-Server oder
      Netzwerk-Routing zwischen 10.81.x und 10.93.70.x) -- aktuell nur
      manueller Bundle+RDP-Transfer
- [ ] WinRM auf dieser VM selbst konfigurieren (fuer den kuenftigen
      WinRM-basierten Restore-Ausfuehrungspfad)
- [ ] NetApp-Cluster, Hyper-V-Cluster etc. in der neuen Instanz einrichten
      (separate DB von der Entwickler-Instanz)
