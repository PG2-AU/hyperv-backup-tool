# AU Storage Manager for Hyper-V

Backup, Restore und Storage-Verwaltung fuer Hyper-V (Windows Server 2022/2025) auf Basis
von NetApp ONTAP Snapshots und SnapMirror (vormals "Hyper-V NetApp Backup"; intern
weiterhin kurz HVNB, z.B. in Umgebungsvariablen, Container- und Snapshot-Namen). Laeuft als Container auf Rocky Linux, wird als
versioniertes Release-Image ausgeliefert (Paketdatei oder Registry) und bietet eine Web-GUI mit drei festen Rollen, Active-Directory-
Integration und MetroCluster-Unterstuetzung. In der GUI heissen die Sicherungsgruppen
"Protection Groups"; im Code und in der Datenbank weiterhin `ResourceGroup`.

## Architektur

```
frontend/   React + TypeScript + Mantine v7  -> Web-GUI (Sidebar-Layout, Kontextmenues,
            Log-Viewer, @mantine/charts fuer Kapazitaetsverlauf)
  src/pages/       Eine Seite je Hauptmenuepunkt (Dashboard, Alarme, System-Log,
                    Performance, Inventory/VMs, Backup/Jobs, Restore, Reports, Storage,
                    Settings, Versionsverlauf, Docs)
  src/components/  Wiederverwendbare Komponenten (Formulare, Assistenten, Modals, Detail-
                    Kopfzeilen, Aktivitaeten-Fusszeile, globale Suche)
  src/api/         Typisierte API-Clients (hooks.ts, hooks.*.ts je Bereich, types.ts)
  public/docs/     Statische Doku-Seiten der App: architecture.html (von Hand gepflegt) und
                    deployment.html (bei jedem Frontend-Build aus docs/DEPLOYMENT.md
                    erzeugt, scripts/render-docs.mjs -- nie von Hand aendern)
backend/    FastAPI (Python)                 -> REST-API, Auth/RBAC, Orchestrierung
  app/core/        Config, Security (JWT), RBAC-Modell (Permissions, feste Rollen),
                    Kerberos-Konfiguration, WinRM-Trust, Kapazitaetsverlauf und -prognose,
                    Standort-Auswertung (sites.py), Storage-Move-Pfadabbildung
                    (storage_move.py), Schutzklassen-Pruefung (protection_class.py),
                    Reports (reports/), VM-Performance-Sammler (vm_performance.py),
                    Aenderungsprotokoll (audit.py), Schritt-Protokoll der Ablaeufe
                    (run_steps.py), Konfigurations-Export/-Import (config_transfer.py),
                    DB-Sicherung/-Wiederherstellung (db_backup.py), periodischer Scheduler
                    (Discovery/Healthcheck/Alarme/Retention/Sammler/Reports/DB-Sicherung,
                    siehe app/core/scheduler.py)
  app/models/      SQLAlchemy-Modelle -- Auth/RBAC (User/Role), Hyper-V-/NetApp-Discovery
                    (VMs/VHDs/CSVs/SVMs/Volumes/LUNs/Aggregate/SnapMirror), Backup-/
                    Restore-/VM-Neuerstellungs-/Datei-Restore-Laeufe, Laeufe der
                    Verwaltungsablaeufe (VM anlegen/aendern/verschieben/loeschen, CSV
                    anlegen/vergroessern/loeschen, SMB3-Freigabe), Resource Groups +
                    Zeitplaene, Schutzklassen, Standorte, Alarme, Kapazitaetsverlauf,
                    VM-Performance, Reports, Aenderungsprotokoll, DB-Sicherung, WinRM-/
                    Kerberos-/AD-/E-Mail-Konfiguration, System-Log
  app/services/    NetApp-ONTAP-Client (Snapshot/SnapMirror/MetroCluster/Volumes/LUNs/
                    CIFS), Hyper-V-Client (PowerShell/WinRM ueber NTLM/CredSSP/Kerberos),
                    Active-Directory-Client (Login-Bind + Verzeichnis-Suche), E-Mail-
                    Versand, SMB-Dateizugriff fuer die DB-Sicherung (smb_target.py)
  app/api/routes/  REST-Endpunkte (VMs inkl. Anlegen/Einstellungen/Power/Konsole/
                    Verschieben/Loeschen, CSV und SMB3 anlegen/vergroessern/loeschen,
                    Storage, Jobs, Restore, Datei-Restore, Resource Groups, Zeitplaene,
                    Schutzklassen, Alarme, Reports, Performance, Aktivitaeten, Suche,
                    Benutzer & Rollen, Settings/AD/Kerberos/WinRM-Zertifikate/E-Mail,
                    Kapazitaetsverlauf, Standorte, Konfigurations-Transfer, DB-Sicherung,
                    System-Log)
docker/     Rocky-Linux-Container: nginx (TLS-Terminierung + Static Files),
            uvicorn (Backend), supervisord (Prozessverwaltung).
            Dockerfile.release / entrypoint-release.sh / supervisord-release.conf =
            Release-Image mit fertiger Anwendung (ohne git/pip/npm zur Laufzeit);
            Dockerfile / entrypoint.sh / updater.sh = frueherer Git-Pull-Mechanismus,
            nur noch fuer nicht umgestellte Installationen
scripts/    build-release.sh (Image + Paketdatei bauen), update-lock.sh
            (backend/requirements.lock neu erzeugen), hvnb-update (Paket/Registry-
            Version auf dem Server einspielen, Rollback, Update-Dienst fuer die GUI),
            hvnb-git-autoupdate (Update aus Git, nur Entwicklungsumgebungen)
.github/    workflows/release.yml: baut bei einem Tag vX.Y.Z Image und Paket
```

### Wesentliche Subsysteme (Stand dieser Iteration)

- **Rollen + Active Directory** -- drei feste Rollen (Administrator, Operator, Viewer) mit
  festgelegten Permissions; je Benutzer aenderbar. Benutzer koennen lokal (Passwort-Hash in
  der DB) oder ueber eine GUI-verwaltete AD-Integration (Settings > Active Directory)
  angelegt werden -- Verzeichnis-Suche + proaktives Hinzufuegen mit Rolle, lokale und
  AD-Benutzer funktionieren nebeneinander. Nicht umgesetzt: Zuordnung von AD-Gruppen zu
  Rollen und Einschraenkung auf einzelne VMs/CSVs/Hosts.
- **WinRM-Transport** -- NTLM, CredSSP oder Kerberos konfigurierbar (Settings >
  Kerberos), inkl. selbst-signierter Zertifikatsverwaltung fuer HTTPS-WinRM-Listener
  (Settings > WinRM-Zertifikate). Einige Hyper-V-Cluster-Operationen (Disk-Attach/
  Detach, Cluster-Rollen-Registrierung, Storage-Move, Live-Migration) erzwingen aus
  technischen Gruenden weiterhin NTLM bzw. eine gezielt gescopte CredSSP-Sitzung,
  selbst wenn Kerberos als Standard-Transport aktiv ist.
- **Backup/Restore** -- App-konsistente (VSS-Checkpoint) oder crash-konsistente
  Backups auf Ebene VM, CSV oder SMB3-Freigabe, orchestriert ueber Protection Groups
  (je verknuepfter Policy ein eigener Zeitplan) mit Retention; ein Lauf ist nur dann
  fehlgeschlagen, wenn ein Storage-Snapshot nicht erstellt werden konnte, sonst
  "erfolgreich mit Fehlern"; optional Snapshot-Sperre je Policy (Tage): auf Volumes mit
  ONTAP-Snapshot-Locking manipulationssicher (SnapLock-Ablaufzeit, Tamperproof Snapshot),
  sonst als einfacher Loeschschutz (expiry_time) -- welche Stufe gilt, zeigen Policy-Formular,
  Job-Verlauf und der Backups-Dialog; Restore als Anhaengen oder Ersetzen inkl. automatischem AVHDX-Ketten-
  Merge, VM-Neuerstellung, sowie dateibasierter Restore einzelner Dateien aus einem
  gemounteten Snapshot-Klon.
- **Hintergrund-Scheduler** -- periodische Jobs fuer Health-Checks, Discovery,
  Snapshot-/SnapMirror-Abgleich, geplante Backups, Retention-Cleanup, Alarm-
  Auswertung, taeglichen E-Mail-Report, geplante Reports, DB-Sicherung, den taeglichen
  Kapazitaets-Sammler und den VM-Performance-Sammler (siehe unten) -- alle Intervalle GUI-konfigurierbar unter Settings > Hintergrund-
  jobs.
- **Alarme** -- Kapazitaets-Schwellwerte (Volume/LUN) und Kapazitaetsprognose (Volume/LUN/
  Aggregat), Zustand von Hyper-V- und NetApp-Cluster, SnapMirror-Zustand und -Lag, verpasste
  Backups, Zeitplan-Kollisionen, verwaiste Checkpoints, AVHDX ohne Checkpoint, VM auf
  mehreren CSVs, Standort-Abweichung, Knoten-Erreichbarkeit, Schutzklasse nicht erfuellt,
  DB-Sicherung fehlgeschlagen/ueberfaellig; optionaler E-Mail-Versand pro Alarmtyp.
- **Standorte** -- Kennzeichnung von Hyper-V-Knoten (manuell) und NetApp-Systemen
  (jede CSV erbt ueber die LUN-Seriennummer, einzeln ueberschreibbar) je
  Rechenzentrum (Settings > Standorte); Inventory, Dashboard und Alarm zeigen VMs,
  deren Host an einem anderen Standort steht als ihr Storage. Waehrend eines
  MetroCluster-Switchovers ist die Pruefung ausgesetzt.
- **VM verschieben** -- Aktion in Inventory > VMs: Host-Move per Live-Migration
  (Zielknoten-Vorschlag nach Standort und live abgefragtem freiem RAM) oder
  Storage-Move per `Move-VMStorage` auf eine andere CSV (Ordnerstruktur der Quelle
  bleibt erhalten, CSV-Belegung vor/nach dem Move, Fortschritt + Abbruch, Warnung
  bei geaendertem Backup-Schutz). Gesperrt waehrend eines Backups der VM; Backups
  ueberspringen umgekehrt eine VM, die gerade verschoben wird. "Beheben" an einer
  Standort-Abweichung oeffnet den Dialog mit vorgeschlagenem Ziel.
- **Neue VM anlegen** -- Button "Neue VM" in Inventory > VMs: leere VM auf einer CSV oder
  SMB3-Freigabe (<Ablage>\<VM-Name>\ + Virtual Hard Disks\), Knoten-Vorschlag nach Standort
  der Ablage und freiem RAM, Generation 2/1, vCPU, RAM statisch/dynamisch, Checkpoint-Typ
  Production, System- und Datendisks (dynamisch/fest), vSwitch + VLAN, Secure Boot (Vorlage
  Windows/UEFI CA), vTPM optional, ISO von CSV/SMB3 (Suche bzw. Pfad) mit DVD als erstem
  Boot-Geraet, Cluster-Rolle, optional starten und Protection Group. SMB3-Ablage/-ISO per
  CredSSP. Bei Fehler Rueckfrage Zurueckrollen (VM + Ordner entfernen) oder Behalten.
- **Aktivitaeten-Fusszeile** -- wie "Kuerzlich bearbeitete Aufgaben" im vCenter: einklappbare
  Leiste am unteren Rand mit allen laufenden und den Ablaeufen der letzten 24 Stunden (Backups,
  Restores, VM-Neuerstellung, Datei-Restore, Verschiebungen, CSV/SMB3/VM anlegen und loeschen,
  CSV vergroessern, VM-Power) mit Aufgabe, Ziel, Status bzw. aktuellem Schritt/Fortschritt,
  Initiator (Benutzer oder System), Start, Ende, Dauer; Filter Alle/Laufend/Fehler. Klick zeigt
  das Schritt-Protokoll; fehlgeschlagene Anlage-Laeufe mit offener Rueckfrage bleiben sichtbar
  und lassen sich von dort zurueckrollen oder behalten. GET /api/activities.
- **Kapazitaetsprognose** -- aus dem Kapazitaetsverlauf (lineare Regression ueber die letzten
  30 Tage, mind. 5 Messtage ueber 1 Woche) 4 Wochen voraus: gestrichelte Prognoselinie und
  Kapazitaetslinie im Verlaufsdiagramm, Text "voll in ca. N Tagen" darunter; Alarm
  "Prognose: Volume/LUN/Aggregat laeuft voll", wenn es bei echtem Zuwachs innerhalb von 4
  Wochen vollaeuft (Text mit Rest-Tagen wird bei jedem Check aktualisiert, loest sich von
  selbst). Scope wie die Schwellwert-Alarme.
- **Reports** -- Menuepunkt "Reports" mit 8 Typen, je mit eigener Auswahl: Schutzstatus,
  Backup-Erfolg, Wiederherstellungspunkte, Restore-Nachweis, Kapazitaet und Prognose,
  SnapMirror/DR, Inventar, Audit-Trail (Zeitraum-Reports mit Vergleich zum Vorzeitraum).
  Erzeugt ein PDF (ReportLab, A4 quer, oeffnet im neuen Tab) plus CSV; Inhalts-SHA-256 im
  PDF-Fuss, Datei-SHA-256 in der Historie. Historie 12 Monate (`HVNB_REPORTS_DIR`, Standard
  /data/reports). Vorlagen mit Zeitplan (taeglich/woechentlich/monatlich) und Mailversand,
  optional nur bei Auffaelligkeiten und mit CSV-Anhang; verpasste Termine werden nachgeholt.
  Rechte `report:view` (Erstellen/Ansehen, alle Rollen) und `report:manage` (Vorlagen,
  Versand, Loeschen; Administrator + Operator). API unter /api/reports.
- **Performance** -- Monitoring > Performance mit drei Reitern. *Storage-Seite:* IOPS, Latenz
  und Durchsatz (je Lesen/Schreiben) je CSV (ueber ihre LUN), SMB3-Freigabe (ueber ihr Volume)
  und aller Volumes, live von ONTAP (15-s-Mittel, alle 30 s neu, Backend puffert 20 s); Verlauf
  (1 Std. bis 1 Jahr) aus ONTAPs eigener Statistik. *VM-Seite:* je VM IOPS, Latenz und Durchsatz
  aus Storage QoS des Failover-Clusters (WMI-Klasse MSFT_StorageQoSFlow -- das Cmdlet
  Get-StorageQosFlow ist unter Server 2025 defekt), von der App gesammelt: ein lesender
  WinRM-Aufruf je Cluster alle 5 min (Settings > Hintergrundjobs, 0 = aus), Rohwerte 7 Tage,
  danach Stundenmittel 90 Tage (Tabelle `vm_perf_samples`). Nur VMs auf CSVs (Storage QoS
  erfasst keine SMB3-Freigaben). Zur CSV zusaetzlich "Top-VMs". Latenz orange ab 10 ms, rot ab
  20 ms (ab 10 IOPS). Verlauf auch in Inventory (VM/CSV/SMB3) und Storage (Volumes/LUNs) per
  Tacho-Symbol. API /api/performance/overview, /history, /vms, /vms/history; Recht storage:view.
- **Aenderungsprotokoll** -- jede aendernde API-Anfrage (POST/PUT/PATCH/DELETE) eines
  angemeldeten Benutzers wird zentral protokolliert (Tabelle `audit_events`: wer, wann,
  Bereich, Aktion, Objekt, HTTP-Ergebnis, Adresse; app/core/audit.py). Der Anfrage-Body
  wird nie gespeichert, nur Namensfelder als Objekt. Grundlage des Reports "Audit-Trail",
  Aufbewahrung ca. 13 Monate.
- **Globale Suche** -- Suchfeld in der Kopfzeile (Strg+K oder /) ueber VMs, CSVs, SMB3- und
  CIFS-Freigaben, Volumes, LUNs und die Backup-Snapshots der App; '*'/'?' als Platzhalter.
  Treffer erscheinen beim Tippen sofort: ein kompakter Index (GET /api/search/index, nur
  Objekte mit Leserecht) wird einmal geladen und alle 5 Minuten aufgefrischt, gefiltert
  wird im Browser. Ein Treffer springt auf die passende Seite und waehlt das Objekt aus bzw.
  oeffnet bei Snapshots den Snapshots-Dialog des Volumes.
- **Schutzklassen** -- Backup > Schutzklassen: frei definierbare Klassen (z.B. Gold/Silber/
  Bronze, als Vorschlag per Knopf) mit Rang, maximalem Backup-Alter, Aufbewahrung je Stufe
  (stuendlich/taeglich in Tagen, woechentlich in Wochen, monatlich in Monaten -- jeweils
  primaer und sekundaer, leer = nicht verlangt) und Pflicht zur applikationskonsistenten
  Sicherung. Jede VM, CSV und SMB3-Freigabe bekommt ihre Klasse von Hand (Spalte in Inventory
  oder Sammelzuweisung). Geprueft wird (a) Soll: passen Policies/Zeitplaene zur Klasse
  (groesste Luecke zwischen zwei Laeufen; Aufbewahrung je Stufe einzeln -- die Stufe einer
  Policy ergibt sich aus ihrem Zeitplan, eine feinere Stufe zaehlt fuer eine groebere mit;
  sekundaer aus den Regeln der SnapMirror-Policy am Ziel), (b) Ist: letztes Backup und
  sekundaere Kopie jung genug, dazu als Hinweis ohne Alarm "im Aufbau", solange die
  vorhandenen Sicherungen einer Stufe noch nicht so weit zurueckreichen, wie die Klasse
  verlangt,
  (c) Speicher: VM liegt auf CSV/Freigabe mindestens ihrer Klasse. Ergebnis als Badge mit
  Gruenden, Alarm "Schutzklasse nicht erfuellt" und Spalte im Schutzstatus-Report.
  Protection Groups werden NICHT von Hand zugeordnet: die App berechnet je Gruppe, welche
  Klassen ihre Policies/Zeitplaene erfuellen (Spalte "Erfuellt Schutzklassen"), und nennt
  bei einem Verstoss die Gruppen, die zur Klasse des Objekts passen wuerden.
  API /api/protection-classes, Lesen backup:view, Aendern backup:create.
- **VM-Einstellungen aendern** -- Eintrag im Power-Menue der VM: vCPU und Arbeitsspeicher
  (statisch/dynamisch; nur bei ausgeschalteter VM), Netzwerkadapter (Switch/VLAN umstecken
  oder trennen jederzeit; hinzufuegen/entfernen bei Generation 1 nur ausgeschaltet),
  Festplatten vergroessern (nie verkleinern; nicht mit Checkpoints/Differenz-Disk; Partition
  im Gast bleibt Sache des Gasts) und neue VHDX am SCSI-Controller anlegen. Der Dialog fragt
  den Live-Stand ab, ausgefuehrt werden nur echte Abweichungen; danach Cluster-Konfiguration
  und Inventory aktualisiert. Kein Zurueckrollen. Gesperrt waehrend Backup, Restore,
  Verschiebung, Loeschung oder Power-Aktion. API /api/vm-settings, Recht hyperv:manage.
- **VM loeschen** -- Eintrag im Power-Menue der VM: Cluster-Rolle und VM entfernen, aus allen
  Protection Groups austragen, Festplatten-Dateien inkl. Checkpoint-Ketten loeschen (Opt-out)
  und danach nur leer gewordene Ordner des eigenen VM-Ordners entfernen. Laufende VM nur mit
  "vorher hart ausschalten"; von anderen VMs mitbenutzte Disks bleiben stehen; gesperrt
  waehrend Backup, Restore oder Verschiebung. Backups der VM bleiben erhalten. Bestaetigung
  per VM-Namen.
- **VM starten/herunterfahren** -- Power-Menue je VM in Inventory > VMs: Starten (pausierte
  VM: Fortsetzen), Herunterfahren ueber das Gastbetriebssystem (`Stop-VM -Force`) und
  Ausschalten (hart, `Stop-VM -TurnOff`), beides mit Rueckfrage. Laeuft im Hintergrund,
  Ergebnis als Meldung + System-Log; gesperrt waehrend Backup, Restore oder Verschiebung der VM.
- **Remote-Sitzung auf eine VM** -- Aktion je VM in Inventory > VMs (Recht `vm:console`,
  Administrator + Operator): laedt eine .rdp-Datei fuer die Konsole der VM ueber den
  Hyper-V-Knoten (Port 2179 + VM-ID, wie VMConnect -- geht ohne Netzwerk im Gast) oder
  fuer RDP direkt ins Gastsystem (IP live aus den Integrationsdiensten). Die App gibt
  keine Zugangsdaten weiter; jeder Download steht im System-Log.
- **CSV vergroessern** -- Aktion in Inventory > CSVs: Aggregat, Volume und LUN/CSV live
  als Balken, Volume und LUN im Dialog manuell vergroessern (Balken und Warnungen
  aktualisieren sich sofort: Aggregat-Platz, Ueberbuchung des Volumes, platzreservierte
  LUN, Snapshot-Reserve), danach Volume -> LUN -> Datentraeger auf allen Knoten neu
  einlesen -> Partition auf dem Owner-Knoten erweitern, im laufenden Betrieb; nur
  Vergroessern, erkennt und erweitert auch bereits unpartitionierten Platz.
- **Neue CSV anlegen** -- Button "Neue CSV" in Inventory > CSVs: Volume (thin,
  Snapshot-Policy none, Reserve 0 %, optional Autosize grow, Groesse = LUN + Puffer
  fuer Snapshots) und LUN (hyper_v, thin, Space Allocation an) auf der NetApp anlegen,
  auf die igroup(s) der Knoten mappen (Vorschlag per IQN/WWPN-Abgleich, gleiche LUN-ID),
  auf allen Knoten einlesen, auf einem Knoten GPT + NTFS 64 KB (ReFS mit Warnung),
  Cluster-Disk + CSV, Mount-Ordner optional in den CSV-Namen umbenennen, Inventory
  aktualisieren (Standort erbt die CSV vom NetApp-System), optional direkt einer
  Protection Group zuordnen. Formatiert nur leere (RAW-)Disks. Bei einem Fehler fragt
  der Dialog nach: Zurueckrollen (entfernt genau die angelegten Objekte) oder Behalten.
- **CSV loeschen** -- Papierkorb-Aktion in Inventory > CSVs: CSV und Cluster-Disk
  entfernen, aus allen Protection Groups austragen, optional (Opt-out) LUN samt Mapping
  und -- nur wenn einzige LUN darin -- das Volume auf der NetApp loeschen, danach auf allen
  Knoten neu einlesen. Gesperrt, solange VMs/VM-Dateien auf der CSV liegen (Inventory +
  Live-Scan) oder das Volume SnapMirror-Quelle ist; andere Dateien und die mitgeloeschten
  Backup-Snapshots (Anzahl, Zeitraum) als Warnung, Backup-Eintraege werden danach als nicht
  mehr vorhanden markiert (SnapMirror-Kopien bleiben). Bestaetigung per CSV-Namen.
- **SMB3-Freigabe anlegen/loeschen** -- in Inventory > SMB3-Freigaben: Volume (Junction
  /<volume>, NTFS, thin, Snapshot-Policy none, Reserve 0 %, optional Autosize) + CIFS-Freigabe
  (continuously available, Oplocks) mit Vollzugriff fuer die Computerkonten der Knoten,
  des Clusters (CNO) und des Restore-Proxy-Hosts sowie BUILTIN\Administrators -- ohne
  Everyone (live vorgeschlagen, im Dialog aenderbar); Zugriffstest von jedem Knoten per
  CredSSP, optional Protection Group; bei Fehler Rueckfrage Zurueckrollen/Behalten.
  Loeschen mit denselben Regeln wie bei der CSV (Dateiscan ueber die ONTAP-Datei-API,
  Volume Opt-out und nur, wenn keine weitere Freigabe/LUN darin liegt).
- **Kapazitaetsverlauf** -- taeglicher Messpunkt je VHD/CSV/LUN/Volume/Aggregat
  (`CapacitySample`, ueber einen aus stabilen Objekteigenschaften abgeleiteten
  Schluessel statt der bei jeder Discovery neu vergebenen Zeilen-ID), als
  Liniendiagramm ueber 1/3/6/12 Monate direkt aus der jeweiligen Detailansicht in
  Inventory/Storage abrufbar (mehrere VHDs einer VM als je eigene Linie; bei Volumes
  zusaetzlich die Snapshot-Belegung als gestrichelte Linie).
- **Snapshot-Belegung je Volume** -- Storage > Volumes zeigt im Belegungsbalken den
  Snapshot-Anteil als eigenen Bereich, per Mouseover Daten/Snapshots, Anzahl Snapshots
  (davon Backup-Snapshots dieser App laut Katalog) und Snapshot-Reserve inkl.
  Ueberlauf-Hinweis.
- **Konfiguration exportieren/importieren** -- Settings > System: komplette Einrichtung
  als ZIP (ohne Kennwoerter, optional mit Backup-Katalog), Import in eine frische
  Installation fuer Umzug/Wiederaufbau.
- **DB-Sicherung** -- taegliche, im Betrieb konsistente Sicherung der App-Datenbank auf
  eine CIFS-Freigabe (smbprotocol, kein Kernel-Mount) plus lokale Kopien, Aufbewahrung
  in Tagen, Alarm bei Fehlschlag/Ausbleiben, Wiederherstellen per GUI mit Vorabpruefung
  (Integritaet, HVNB_SECRET_KEY) und automatischer Sicherheitskopie.
- **MetroCluster/SnapMirror** -- Status-Anzeige, Spiegelobjekte optional aus der
  gesamten Storage-Ansicht ausblendbar, SnapMirror-Beziehungen/-Policies/-Schedules
  verwaltbar inkl. manuellem Update-Trigger. Je Regel einer SnapMirror-Policy optional eine
  Sperrfrist (Stunden/Tage/Monate/Jahre): die uebertragenen Snapshots sind am Ziel so lange
  manipulationssicher gesperrt (Tamperproof Snapshot) -- wirkt nur auf Ziel-Volumes mit
  Snapshot-Locking, die Policy-Liste markiert Ziele, auf denen die Frist nicht greift.

### Backup-Prinzip

1. Hyper-V-Checkpoint auf den betroffenen VMs erzeugen
   (`ApplicationConsistent` via Production Checkpoint/VSS oder
   `CrashConsistent` via Standard Checkpoint) – Scope: VM, CSV oder SMB3-Freigabe.
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
`HVNB_INITIAL_ADMIN_PASSWORD` (Default `ChangeMe123!`, siehe `backend/.env.example`)
— unbedingt vor Produktivbetrieb aendern.

## Deployment (Container)

```bash
scripts/build-release.sh 1.2.0     # Image localhost/hvnb-backup:1.2.0 + dist/hvnb-1.2.0.tar.gz
```

Auf dem Zielserver wird das Paket geladen und ueber eine Quadlet-Unit
gestartet; Updates spielt `hvnb-update` ein (Paketdatei, Upload in der GUI
unter Settings > Updates, Online-Update aus einer Registry oder -- nur fuer
Entwicklungsumgebungen -- automatisch aus Git).

Für eine vollständige Installationsanleitung ausgehend von einem frischen,
domain-gejointen Windows Server (WSL2-Einrichtung, Netzwerk-/Zertifikats-
/Persistenz-Konfiguration bis zur lauffähigen GUI) siehe
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Das Release-Image enthaelt Backend, gebautes Frontend und die in
`backend/requirements.lock` festgeschriebenen Abhaengigkeiten; der Container
laedt zur Laufzeit nichts nach und startet Backend + nginx (HTTPS, Port 443
im Container / 8443 auf dem Host). Version und Versionsverlauf liest die App
aus den beim Build erzeugten Dateien `VERSION.json`/`CHANGELOG.json`
(`backend/app/core/release.py`). Der fruehere Mechanismus (Container holt
den Code per `git pull`, `docker-compose.yml`) existiert nur noch fuer nicht
umgestellte Installationen und wird danach entfernt.

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
Dieselbe Anleitung und eine Architektur-Seite sind in der App ueber die Links
in der Fusszeile erreichbar (`frontend/public/docs/`). `docs/INSTALL.md` ist das
historische Feldprotokoll der ersten Einrichtung und keine Anleitung.
