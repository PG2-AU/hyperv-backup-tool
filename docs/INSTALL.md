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

## Offene Schritte

(wird laufend ergänzt)
