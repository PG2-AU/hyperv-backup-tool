"""Pruefung der Schutzklassen (Backlog #86), siehe app.models.protection_class.

Je VM, CSV und SMB3-Freigabe mit zugewiesener Klasse drei Pruefungen:

1. Soll (Konfiguration): Erfuellen die Policies und Zeitplaene, ueber die das
   Objekt gesichert wird, die Klasse? Sicherungsabstand = groesste Luecke
   zwischen zwei geplanten Laeufen (bei mehreren Uhrzeiten am Tag inkl. der
   ueber Mitternacht); pausierte Gruppen/Zeitplaene und deaktivierte Policies
   zaehlen nicht. Aufbewahrung PRIMAER in Tagen = laengste Aufbewahrung einer
   Policy (Anzahl-Retention = Anzahl x mittlerer Abstand). Aufbewahrung
   SEKUNDAER in Tagen = was das SnapMirror-Ziel laut seiner Policy fuer das
   Label der sichernden Policy behaelt (Regel-Anzahl x Abstand der Stufe),
   je Volume des Objekts; alle Volumes muessen reichen. So faellt eine
   "Gold"-VM mit nur taeglichem Backup auf, auch wenn jeder Lauf klappt.
2. Ist: Ist das letzte erfolgreiche Backup jung genug (10 % bzw. mindestens
   30 min Toleranz fuer die Laufzeit), und gibt es bei Pflicht eine sekundaere
   Kopie, die nicht aelter als max(Klassen-Alter, 26 h) ist?
3. Speicher (nur VMs): Jede CSV/Freigabe, auf der die VM liegt, muss eine
   Klasse haben, die mindestens so hoch ist wie die der VM (rank kleiner =
   hoeher).

Reine DB-Auswertung, keine WinRM-/NetApp-Aufrufe -- genutzt von der Anzeige
(app.api.routes.protection_classes), dem Alarm (scheduler.run_alert_check) und
dem Schutzstatus-Report."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from app.models.backup_policy import ConsistencyType, RetentionType
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.hyperv_discovery import HyperVVm
from app.models.netapp_discovery import NetAppSnapMirrorPolicy, NetAppSnapMirrorRelationship
from app.models.protection_class import ProtectionClass, ProtectionClassAssignment
from app.models.resource_group import ResourceGroup
from app.models.schedule import ScheduleType

SECONDARY_MIN_AGE_HOURS = 26


@dataclass
class ObjectStatus:
    object_type: str  # vm | csv | smb_share
    cluster_id: str | None
    cluster_name: str | None
    name: str  # VM-Name, CSV-Name bzw. 'server|share'
    display_name: str
    class_id: str | None = None
    class_name: str | None = None
    class_color: str | None = None
    # ok | violation | unassigned
    status: str = "unassigned"
    violations: list[str] = field(default_factory=list)
    # nur VMs: Speicherorte mit deren Klasse
    storage: list[dict] = field(default_factory=list)
    resource_group_names: list[str] = field(default_factory=list)
    policy_names: list[str] = field(default_factory=list)
    last_backup_at: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def schedule_gap_hours(schedule) -> tuple[float, float]:
    """(groesste Luecke, mittlerer Abstand) in Stunden zwischen zwei Laeufen."""
    kind = schedule.schedule_type
    if kind == ScheduleType.WEEKLY:
        return 168.0, 168.0
    if kind == ScheduleType.MONTHLY:
        return 744.0, 730.0
    minutes = sorted({int(t[:2]) * 60 + int(t[3:5]) for t in (schedule.times or []) if len(t) >= 5 and t[2] == ":"})
    if len(minutes) <= 1:
        return 24.0, 24.0
    gaps = [b - a for a, b in zip(minutes, minutes[1:])] + [minutes[0] + 1440 - minutes[-1]]
    return max(gaps) / 60, 24.0 / len(minutes)


@dataclass
class _Link:
    policy_name: str
    gap_hours: float
    retention_days: float
    secondary: bool
    app_consistent: bool
    # mittlerer Abstand in Tagen und SnapMirror-Label -- fuer die sekundaere Aufbewahrung
    interval_days: float = 1.0
    label: str | None = None


def _effective_links(groups: list[ResourceGroup]) -> dict[str, list[_Link]]:
    """Je Protection Group die wirksamen Policy-Verknuepfungen (mit Zeitplan,
    nichts pausiert/deaktiviert)."""
    result: dict[str, list[_Link]] = {}
    for group in groups:
        links: list[_Link] = []
        if not group.paused:
            for link in group.policy_links:
                policy, schedule = link.policy, link.schedule
                if policy is None or schedule is None or not policy.enabled or schedule.paused:
                    continue
                gap, mean = schedule_gap_hours(schedule)
                retention = (
                    float(policy.retention_value) if policy.retention_type == RetentionType.DAYS else policy.retention_value * mean / 24
                )
                links.append(_Link(
                    policy_name=policy.name, gap_hours=gap, retention_days=retention, secondary=bool(policy.snapmirror_update),
                    app_consistent=policy.consistency == ConsistencyType.APPLICATION_CONSISTENT,
                    interval_days=mean / 24, label=policy.snapmirror_label.name if policy.snapmirror_label else None,
                ))
        result[group.name] = links
    return result


def _fmt_hours(hours: float) -> str:
    if hours < 48:
        return f"{round(hours, 1):g} h".replace(".", ",")
    return f"{round(hours / 24):g} Tage"


class _SnapMirror:
    """SnapMirror-Beziehungen und die Aufbewahrungsregeln ihrer Policies aus
    der letzten NetApp-Discovery: Volume 'svm:volume' -> [{label: Anzahl}]."""

    def __init__(self, db: Session):
        import json

        policies: dict[str, dict[str, int]] = {}
        for policy in db.query(NetAppSnapMirrorPolicy).all():
            rules: dict[str, int] = {}
            try:
                for rule in json.loads(policy.rules_json or "[]"):
                    rules[str(rule.get("label"))] = int(rule.get("count") or 0)
            except (ValueError, TypeError, AttributeError):
                pass
            policies.setdefault(policy.name, rules)
        self.by_volume: dict[str, list[tuple[str, dict[str, int]]]] = {}
        for rel in db.query(NetAppSnapMirrorRelationship).all():
            if rel.source_path:
                self.by_volume.setdefault(rel.source_path.lower(), []).append((rel.policy_name or "?", policies.get(rel.policy_name or "", {})))

    def check(self, volumes: list[str], links: list[_Link], required_days: int) -> list[str]:
        """Verstoesse gegen die verlangte sekundaere Aufbewahrung."""
        secondary = [l for l in links if l.secondary]
        if not secondary:
            return ["keine Policy mit SnapMirror-Update (sekundäre Aufbewahrung verlangt)"]
        if not volumes:
            return []
        violations = []
        for volume in volumes:
            relationships = self.by_volume.get(volume.lower())
            if not relationships:
                violations.append(f"keine SnapMirror-Beziehung für Volume {volume}")
                continue
            best, reason = 0.0, ""
            for policy_name, rules in relationships:
                for link in secondary:
                    if not link.label:
                        reason = reason or f"Policy {link.policy_name} hat kein SnapMirror-Label"
                    elif link.label not in rules:
                        reason = reason or f"SnapMirror-Policy {policy_name} bewahrt Label {link.label} nicht auf"
                    else:
                        best = max(best, rules[link.label] * link.interval_days)
            if best + 0.01 < required_days:
                if best:
                    violations.append(f"sekundäre Aufbewahrung ca. {best:.0f} Tage ({volume}), Klasse verlangt {required_days}")
                else:
                    violations.append(f"sekundäre Aufbewahrung nicht gegeben ({volume}): {reason or 'keine passende Regel'}")
        return violations


def _check_backup(
    cls: ProtectionClass, links: list[_Link], protected: bool, last_primary: datetime | None, last_secondary: datetime | None,
    now: datetime, snapmirror: "_SnapMirror | None" = None, volumes: list[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    max_age = cls.max_backup_age_hours
    secondary_days = cls.secondary_retention_days or 0
    if not protected:
        return ["in keiner Protection Group"]
    # --- Soll
    if not links:
        violations.append("kein aktiver Zeitplan (Gruppe, Policy oder Zeitplan pausiert bzw. ohne Zeitplan)")
    else:
        best_gap = min(l.gap_hours for l in links)
        if best_gap > max_age:
            violations.append(f"Sicherungsabstand {_fmt_hours(best_gap)}, Klasse verlangt höchstens {_fmt_hours(max_age)}")
        best_retention = max(l.retention_days for l in links)
        if best_retention + 0.01 < cls.min_retention_days:
            violations.append(f"primäre Aufbewahrung ca. {best_retention:.0f} Tage, Klasse verlangt {cls.min_retention_days}")
        if secondary_days and snapmirror is not None:
            violations.extend(snapmirror.check(volumes or [], links, secondary_days))
        if cls.require_app_consistent:
            consistent = [l.gap_hours for l in links if l.app_consistent]
            if not consistent:
                violations.append("keine applikationskonsistente Policy")
            elif min(consistent) > max_age and best_gap <= max_age:
                violations.append(
                    f"applikationskonsistent nur alle {_fmt_hours(min(consistent))}, Klasse verlangt höchstens {_fmt_hours(max_age)}"
                )
    # --- Ist
    tolerance = timedelta(hours=max(0.5, max_age * 0.1))
    newest = max((t for t in (last_primary, last_secondary) if t is not None), default=None)
    if newest is None:
        violations.append("noch kein erfolgreiches Backup")
    elif now - newest > timedelta(hours=max_age) + tolerance:
        violations.append(f"letztes Backup vor {_fmt_hours(round((now - newest).total_seconds() / 3600))}, erlaubt {_fmt_hours(max_age)}")
    if secondary_days:
        limit = timedelta(hours=max(max_age, SECONDARY_MIN_AGE_HOURS)) + tolerance
        if last_secondary is None:
            violations.append("keine sekundäre Kopie vorhanden")
        elif now - last_secondary > limit:
            violations.append(f"sekundäre Kopie vor {_fmt_hours(round((now - last_secondary).total_seconds() / 3600))}")
    return violations


def last_backups(db: Session) -> tuple[dict[str, datetime], dict[str, datetime]]:
    """Letztes erfolgreiches Backup je 'vm:<Name>', 'csv:<Name>', 'vol:<SVM>:<Volume>' --
    primaer und sekundaer (gleiche Logik wie der Schutzstatus-Report)."""
    not_captured = {(c.run_id, c.vm_name) for c in db.query(BackupRunVmConfig).filter(BackupRunVmConfig.not_captured.is_(True))}
    primary: dict[str, datetime] = {}
    secondary: dict[str, datetime] = {}

    def note(store: dict, key: str, when: datetime) -> None:
        if key not in store or when > store[key]:
            store[key] = when

    for row in db.query(BackupRunSnapshot).options(selectinload(BackupRunSnapshot.destinations)).all():
        has_secondary = any(d.present for d in row.destinations)
        if not (row.success or has_secondary):
            continue
        keys = [f"vm:{v}" for v in (row.vm_names or []) if (row.run_id, v) not in not_captured]
        keys += [f"csv:{c}" for c in (row.csv_names or [])]
        keys.append(f"vol:{row.svm_name}:{row.volume_name}")
        when = _aware(row.created_at)
        for key in keys:
            if row.success:
                note(primary, key, when)
            if has_secondary:
                note(secondary, key, when)
    return primary, secondary


def evaluate(db: Session, now: datetime | None = None) -> list[ObjectStatus]:
    from app.api.routes.vms import _csv_names_for_vm, _smb_share_keys_for_vm, list_csvs, list_smb_shares, list_vms

    now = now or datetime.now(timezone.utc)
    classes = {c.id: c for c in db.query(ProtectionClass).all()}
    assignments = db.query(ProtectionClassAssignment).all()
    by_name = {(a.object_type, a.cluster_id, a.object_name): a for a in assignments}
    by_uuid = {(a.cluster_id, a.vm_uuid.lower()): a for a in assignments if a.object_type == "vm" and a.vm_uuid}
    vm_uuids = {(v.cluster_id, v.name): (v.vm_uuid or "").lower() for v in db.query(HyperVVm).all()}
    links = _effective_links(db.query(ResourceGroup).all())
    primary, secondary = last_backups(db)

    def class_of(object_type: str, cluster_id: str | None, name: str) -> ProtectionClass | None:
        assignment = None
        if object_type == "vm":
            assignment = by_uuid.get((cluster_id, vm_uuids.get((cluster_id, name), "")))
        assignment = assignment or by_name.get((object_type, cluster_id, name))
        return classes.get(assignment.class_id) if assignment else None

    snapmirror = _SnapMirror(db)

    def base(object_type, cluster_id, cluster_name, name, display, groups, policies, cls, backup_key, volumes) -> ObjectStatus:
        status = ObjectStatus(
            object_type=object_type, cluster_id=cluster_id, cluster_name=cluster_name, name=name, display_name=display,
            resource_group_names=list(groups), policy_names=list(policies),
            last_backup_at=max((t for t in (primary.get(backup_key), secondary.get(backup_key)) if t), default=None),
        )
        if cls is not None:
            status.class_id, status.class_name, status.class_color = cls.id, cls.name, cls.color
            object_links = [l for g in groups for l in links.get(g, [])]
            status.violations = _check_backup(
                cls, object_links, bool(groups), primary.get(backup_key), secondary.get(backup_key), now, snapmirror, volumes,
            )
        return status

    result: list[ObjectStatus] = []
    storage_class: dict[tuple[str, str | None, str], ProtectionClass | None] = {}
    storage_volume: dict[tuple[str, str | None, str], str | None] = {}
    for csv in list_csvs(db, None):
        cls = class_of("csv", csv.cluster_id, csv.name)
        storage_class[("csv", csv.cluster_id, csv.name)] = cls
        volume = f"{csv.svm_name}:{csv.volume_name}" if csv.svm_name and csv.volume_name else None
        storage_volume[("csv", csv.cluster_id, csv.name)] = volume
        result.append(base("csv", csv.cluster_id, csv.hyperv_cluster_name, csv.name, csv.name, csv.resource_group_names, csv.policy_names,
                           cls, f"csv:{csv.name}", [volume] if volume else []))
    for share in list_smb_shares(db, None):
        key = f"{share.server}|{share.share}"
        cls = class_of("smb_share", share.cluster_id, key)
        storage_class[("smb_share", share.cluster_id, key)] = cls
        volume = f"{share.svm_name}:{share.volume_name}" if share.svm_name and share.volume_name else None
        storage_volume[("smb_share", share.cluster_id, key)] = volume
        result.append(base("smb_share", share.cluster_id, share.hyperv_cluster_name, key, f"\\\\{share.server}\\{share.share}",
                           share.resource_group_names, share.policy_names, cls, f"vol:{share.svm_name}:{share.volume_name}",
                           [volume] if volume else []))
    for vm in list_vms(db, None):
        cls = class_of("vm", vm.cluster_id, vm.name)
        locations = [("csv", n, n) for n in sorted(_csv_names_for_vm(vm))]
        locations += [("smb_share", k, "\\\\" + k.replace("|", "\\")) for k in sorted(_smb_share_keys_for_vm(vm))]
        volumes = sorted({v for kind, key, _ in locations if (v := storage_volume.get((kind, vm.cluster_id, key)))})
        status = base("vm", vm.cluster_id, vm.cluster, vm.name, vm.name, vm.resource_group_names, vm.policy_names, cls, f"vm:{vm.name}",
                      volumes)
        for kind, key, display in locations:
            store = storage_class.get((kind, vm.cluster_id, key))
            status.storage.append({"name": display, "class_name": store.name if store else None, "class_color": store.color if store else None})
            if cls is None:
                continue
            if store is None:
                status.violations.append(f"Speicher {display} hat keine Schutzklasse")
            elif store.rank > cls.rank:
                status.violations.append(f"liegt auf {display} (Klasse {store.name}), verlangt mindestens {cls.name}")
        result.append(status)
    for status in result:
        if status.class_id:
            status.status = "violation" if status.violations else "ok"
    return result
