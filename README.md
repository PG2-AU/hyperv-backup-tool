# Hyper-V NetApp Backup Tool

Backup-Tool fuer Hyper-V (Windows Server 2022) auf Basis von NetApp ONTAP
Snapshots und SnapMirror. Laeuft als Container auf Rocky Linux, wird per
Git-Push/Pull deployt und bietet eine Web-GUI mit RBAC, Active-Directory-
Integration und MetroCluster-Unterstuetzung.

## Architektur

```
frontend/   React + TypeScript + Mantine v7  -> Web-GUI (Sidebar-Layout, Kontextmenues,
            Log-Viewer, @mantine/charts fuer Kapazitaetsverlauf)
  src/pages/       Eine Seite je Hauptmenuepunkt (Dashboard, Inventory/VMs, Storage,
                    Jobs, Restore, Alarme, Settings, System-Log, Versionsverlauf, Docs)
  src/components/  ~50 wiederverwendbare Komponenten (Formulare, Modals, Detail-
                    Kopfzeilen, Prozess-/Discovery-Modals)
  src/api/         Typisierte API-Clients (hooks.ts + hooks.settings.ts, types.ts)
backend/    FastAPI (Python)                 -> REST-API, Auth/RBAC, Orchestrierung
  app/core/        Config, Security (JWT), RBAC-Modell (Permissions/Scopes),
                    Kerberos-Konfiguration, Kapazitaetsverlauf-Schluesselableitung,
                    periodischer Scheduler (Discovery/Healthcheck/Alarme/Retention/
                    Kapazitaets-Sammler, siehe app/core/scheduler.py)
  app/models/      SQLAlchemy-Modelle -- Auth/RBAC (User/Role/RoleAssignment),
                    Hyper-V-/NetApp-Discovery (VMs/VHDs/CSVs/SVMs/Volumes/LUNs/
                    Aggregate/SnapMirror), Backup-/Restore-/VM-Neuerstellungs-/Datei-
                    Restore-Laeufe, Resource Groups + Zeitplaene, Alarme, Kapazitaets-
                    verlauf, WinRM-/Kerberos-/AD-Konfiguration, System-Log
  app/services/    NetApp-ONTAP-Client (Snapshot/SnapMirror/MetroCluster), Hyper-V-
                    Client (PowerShell/WinRM ueber NTLM/CredSSP/Kerberos), Active-
                    Directory-Client (Login-Bind + Verzeichnis-Suche), E-Mail-Versand
  app/api/routes/  REST-Endpunkte (VMs/Storage/Jobs/Restore/Datei-Restore/Resource
                    Groups/Zeitplaene/Alarme/Benutzer & Rollen/Settings/AD/Kerberos/
                    WinRM-Zertifikate/Kapazitaetsverlauf/System-Log/Suche)
docker/     Rocky-Linux-Container: nginx (TLS-Terminierung + Static Files),
            uvicorn (Backend), supervisord (Prozessverwaltung),
            Git-Pull-basiertes Deployment (entrypoint.sh / updater.sh)
```

### Wesentliche Subsysteme (Stand dieser Iteration)

- **RBAC + Active Directory** -- Rollen mit granularen Permissions und optional auf
  VM-/CSV-/LUN-/Host-Ebene scopebaren Zuweisungen; Benutzer koennen lokal (Passwort-
  Hash in der DB) oder ueber eine GUI-verwaltete AD-Integration (Settings > Active
  Directory) angelegt werden -- Verzeichnis-Suche + proaktives Hinzufuegen mit Rolle,
  lokale und AD-Benutzer funktionieren nebeneinander (kein Gruppe-zu-Rolle-Mapping,
  bewusst nicht umgesetzt).
- **WinRM-Transport** -- NTLM, CredSSP oder Kerberos konfigurierbar (Settings >
  Kerberos), inkl. selbst-signierter Zertifikatsverwaltung fuer HTTPS-WinRM-Listener
  (Settings > WinRM-Zertifikate). Zwei Hyper-V-Cluster-Operationen (Disk-Attach/
  Detach, Cluster-Rollen-Registrierung) erzwingen aus technischen Gruenden
  weiterhin NTLM bzw. eine gezielt gescopte CredSSP-Sitzung, selbst wenn Kerberos
  als Standard-Transport aktiv ist.
- **Backup/Restore** -- App-konsistente (VSS-Checkpoint) oder crash-konsistente
  Backups auf Ebene VM/CSV/LUN, orchestriert ueber Resource Groups + Zeitplaene mit
  Retention; Restore als Anhaengen oder Ersetzen inkl. automatischem AVHDX-Ketten-
  Merge, VM-Neuerstellung, sowie dateibasierter Restore einzelner Dateien aus einem
  gemounteten Snapshot-Klon.
- **Hintergrund-Scheduler** -- periodische Jobs fuer Health-Checks, Discovery,
  Snapshot-/SnapMirror-Abgleich, geplante Backups, Retention-Cleanup, Alarm-
  Auswertung, taeglichen E-Mail-Report und den taeglichen Kapazitaets-Sammler
  (siehe unten) -- alle Intervalle GUI-konfigurierbar unter Settings > Hintergrund-
  jobs.
- **Alarme** -- Kapazitaets-Schwellwerte (Volume/LUN), SnapMirror-Lag, verpasste
  Backups, Zeitplan-Kollisionen, verwaiste Checkpoints, AVHDX ohne Checkpoint,
  Knoten-Erreichbarkeit; optionaler E-Mail-Versand pro Alarmtyp.
- **Kapazitaetsverlauf** -- taeglicher Messpunkt je VHD/CSV/LUN/Volume/Aggregat
  (`CapacitySample`, ueber einen aus stabilen Objekteigenschaften abgeleiteten
  Schluessel statt der bei jeder Discovery neu vergebenen Zeilen-ID), als
  Liniendiagramm ueber 1/3/6/12 Monate direkt aus der jeweiligen Detailansicht in
  Inventory/Storage abrufbar (mehrere VHDs einer VM als je eigene Linie).
- **MetroCluster/SnapMirror** -- Status-Anzeige, Spiegelobjekte optional aus der
  gesamten Storage-Ansicht ausblendbar, SnapMirror-Beziehungen/-Policies/-Schedules
  verwaltbar inkl. manuellem Update-Trigger.

### Backup-Prinzip

1. Hyper-V-Checkpoint auf den betroffenen VMs erzeugen
   (`ApplicationConsistent` via Production Checkpoint/VSS oder
   `CrashConsistent` via Standard Checkpoint) – Scope: VM, CSV oder LUN.
2. NetApp-Snapshot auf dem zugrunde liegenden Volume erzeugen.
3. SnapMirror-Update zur Replikation des Snapshots ausloesen
   (MetroCluster-Status wird vorher geprueft).
4. Hyper-V-Checkpoint wieder entfernen.
5. Bei Fehlschlag in einem der Schritte: automatisches Aufraeumen aller in
   diesem Lauf erzeugten Checkpoints/Snapshots (`cleanup_checkpoints` /
   `cleanup_snapshots` in den jeweiligen Services).

## Lokale Entwicklung

Backend:

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate  # bzw. .venv\Scripts\activate unter Windows
pip install -e .
cp .env.example .env  # anpassen
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Der Vite-Dev-Server proxyt `/api` auf `http://localhost:8000` (siehe
`frontend/vite.config.ts`).

Initialer lokaler Login: `admin` / Passwort aus
`HVNB_INITIAL_ADMIN_PASSWORD` (Default `ChangeMe123!`, siehe `.env.example`)
— unbedingt vor Produktivbetrieb aendern.

## Deployment (Container)

```bash
cp .env.example .env  # HVNB_GIT_REPO_URL etc. setzen
docker compose up -d --build
```

Für eine vollständige Installationsanleitung ausgehend von einem frischen,
domain-gejointen Windows Server (WSL2-Einrichtung, Netzwerk-/Zertifikats-
/Persistenz-Konfiguration bis zur lauffähigen GUI) siehe
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Der Container klont/pullt beim Start das in `HVNB_GIT_REPO_URL`
konfigurierte Repository nach `/opt/app`, installiert Backend- und
Frontend-Abhaengigkeiten, baut das Frontend und startet Backend + nginx
(HTTPS, Port 443 im Container / 8443 im Compose-Beispiel). Mit
`HVNB_AUTO_UPDATE_ENABLED=true` prueft ein Hintergrundprozess periodisch
auf neue Commits im konfigurierten Branch und aktualisiert automatisch.

## Stand dieser Iteration

Produktiv im Einsatz gegen echte Hyper-V-Failover-Cluster und echte NetApp-
ONTAP-Systeme (inkl. eines realen ~180-VM-6-Node-Clusters) -- alle oben
genannten Subsysteme sind vollstaendig implementiert und live verifiziert,
nicht nur Demo-Daten. Details zu bereits umgesetzten Features, offenen
Backlog-Punkten und der Entstehungsgeschichte einzelner Fixes werden
ausserhalb dieses Repositories gepflegt (Projekt-Gedaechtnis der
Assistenz-Sessions), nicht hier im README.

Fuer eine vollstaendige Installation ausgehend von einem frischen Server
siehe [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md); dort auch die Rollout-
Anleitungen fuer Kerberos, WinRM-Zertifikate und Active-Directory-Login.
