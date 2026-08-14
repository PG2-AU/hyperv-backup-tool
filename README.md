# Hyper-V NetApp Backup Tool

Backup-Tool fuer Hyper-V (Windows Server 2022) auf Basis von NetApp ONTAP
Snapshots und SnapMirror. Laeuft als Container auf Rocky Linux, wird per
Git-Push/Pull deployt und bietet eine Web-GUI mit RBAC, Active-Directory-
Integration und MetroCluster-Unterstuetzung.

## Architektur

```
frontend/   React + TypeScript + Mantine  -> Web-GUI (Sidebar-Layout, Kontextmenues, Log-Viewer)
backend/    FastAPI (Python)              -> REST-API, Auth/RBAC, Orchestrierung
  app/core/       Config, Security (JWT), RBAC-Modell (Permissions/Scopes)
  app/models/     SQLAlchemy-Modelle (User, Role, RoleAssignment)
  app/services/   NetApp ONTAP Client (Snapshot/SnapMirror/MetroCluster),
                  Hyper-V Client (PowerShell/WinRM), Active-Directory-Client
  app/api/routes/ REST-Endpunkte (aktuell teils mit Demo-Daten, siehe TODOs)
docker/     Rocky-Linux-Container: nginx (TLS-Terminierung + Static Files),
            uvicorn (Backend), supervisord (Prozessverwaltung),
            Git-Pull-basiertes Deployment (entrypoint.sh / updater.sh)
```

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

Der Container klont/pullt beim Start das in `HVNB_GIT_REPO_URL`
konfigurierte Repository nach `/opt/app`, installiert Backend- und
Frontend-Abhaengigkeiten, baut das Frontend und startet Backend + nginx
(HTTPS, Port 443 im Container / 8443 im Compose-Beispiel). Mit
`HVNB_AUTO_UPDATE_ENABLED=true` prueft ein Hintergrundprozess periodisch
auf neue Commits im konfigurierten Branch und aktualisiert automatisch.

## Stand dieser Iteration

Aufgebaut ist das Grundgeruest: Projektstruktur, Auth/JWT, RBAC-Modell
(Rollen + scopebare Rollenzuweisungen auf VM/CSV/LUN/Host-Ebene), AD-
Anbindung, Service-Grundgeruest fuer NetApp ONTAP und Hyper-V, die
GUI-Shell (Sidebar mit Untermenues, kontextbezogene Schnellsuche,
kopierbarer Troubleshooting-Log-Viewer, Kontextmenues) sowie das
Container-/Git-Deployment. Die fachlichen Endpunkte (VMs, Storage, Jobs,
Logs) liefern aktuell Demo-Daten – die Anbindung an echte Hyper-V-/ONTAP-
Umgebungen sowie die Job-Orchestrierung selbst (inkl. automatischem
Cleanup bei Fehlschlag) sind die naechsten Ausbaustufen.
