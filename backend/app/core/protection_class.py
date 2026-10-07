"""Pruefung der Schutzklassen (Backlog #86), siehe app.models.protection_class.

Je VM, CSV und SMB3-Freigabe mit zugewiesener Klasse drei Pruefungen:

1. Soll (Konfiguration): Erfuellen die Policies und Zeitplaene, ueber die das
   Objekt gesichert wird, die Klasse? Sicherungsabstand = groesste Luecke
   zwischen zwei geplanten Laeufen (bei mehreren Uhrzeiten am Tag inkl. der
   ueber Mitternacht); pausierte Gruppen/Zeitplaene und deaktivierte Policies
   zaehlen nicht. Aufbewahrung JE STUFE (stuendlich/taeglich/woechentlich/
   monatlich, Nutzer-Vorgabe 2026-10-07 -- eine Monatsstufe darf eine zu
   kurze Tagesstufe nicht verdecken), jeweils primaer und sekundaer als
   Zeitraum. Die Stufe einer Policy ergibt sich aus ihrem Zeitplan; eine
   feinere Stufe zaehlt fuer eine groebere mit (stuendlich 14 Tage erfuellt
   "taeglich 14 Tage"). PRIMAER = Aufbewahrung der Policy (Anzahl-Retention
   = Anzahl x mittlerer Abstand). SEKUNDAER = was das SnapMirror-Ziel laut
   seiner Policy fuer das Label der sichernden Policy behaelt (Regel-Anzahl
   x Abstand), je Volume des Objekts; alle Volumes muessen reichen.
2. Ist: Ist das letzte erfolgreiche Backup jung genug (10 % bzw. mindestens
   30 min Toleranz fuer die Laufzeit), und gibt es bei Pflicht eine sekundaere
   Kopie, die nicht aelter als max(Klassen-Alter, 26 h) ist?
   Zusaetzlich als HINWEIS (kein Verstoss, kein Alarm): Reicht die aelteste
   vorhandene Sicherung je Stufe primaer/sekundaer schon so weit zurueck,
   wie die Klasse verlangt? Die Soll-Pruefung sagt nur, was die Policies behalten
   WERDEN -- nach Einrichtung oder Policy-Aenderung ist die Historie erst
   im Aufbau (Nutzer-Meldung 2026-10-07: "erfuellt" trotz nur 27 Tagen).
3. Speicher (nur VMs): Jede CSV/Freigabe, auf der die VM liegt, muss eine
   Klasse haben, die mindestens so hoch ist wie die der VM (rank kleiner =
   hoeher).

Reine DB-Auswertung, keine WinRM-/NetApp-Aufrufe -- genutzt von der Anzeige
(app.api.routes.protection_classes), dem Alarm (scheduler.run_alert_check) und
dem Schutzstatus-Report."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from app.models.backup_policy import BackupScope, ConsistencyType, RetentionType
from app.models.backup_run import BackupRunSnapshot, BackupRunVmConfig
from app.models.hyperv_discovery import HyperVVm
from app.models.netapp_discovery import NetAppSnapMirrorPolicy, NetAppSnapMirrorRelationship
from app.models.protection_class import ProtectionClass, ProtectionClassAssignment
from app.models.resource_group import ResourceGroup
from app.models.schedule import ScheduleType

SECONDARY_MIN_AGE_HOURS = 26


@dataclass(frozen=True)
class _Tier:
    index: int  # 0 = feinste Stufe
    key: str
    adjective: str
    # Einheit der Eingabe in der Klasse und ihr Wert in Tagen
    unit: str
    unit_days: int
    # Toleranz fuer den Hinweis "Aufbewahrung im Aufbau": N Sicherungen im
    # Abstand X reichen zu jedem Zeitpunkt nur zwischen (N-1)*X und N*X zurueck.
    reach_tolerance_days: float


TIERS = (
    _Tier(0, "hourly", "stündliche", "Tage", 1, 1.0),
    _Tier(1, "daily", "tägliche", "Tage", 1, 1.0),
    _Tier(2, "weekly", "wöchentliche", "Wochen", 7, 7.0),
    _Tier(3, "monthly", "monatliche", "Monate", 30, 31.0),
)


def tier_requirements(cls: ProtectionClass) -> list[tuple[_Tier, int, int]]:
    """Je Stufe mit Vorgabe: (Stufe, primaer in Tagen, sekundaer in Tagen); 0 = nicht verlangt."""
    result = []
    tiers = cls.retention_tiers if isinstance(cls.retention_tiers, dict) else {}
    for tier in TIERS:
        entry = tiers.get(tier.key) or {}
        primary = int(entry.get("primary") or 0) * tier.unit_days
        secondary = int(entry.get("secondary") or 0) * tier.unit_days
        if primary or secondary:
            result.append((tier, primary, secondary))
    return result


def _fmt_span(days: float, tier: _Tier) -> str:
    return f"{round(days / tier.unit_days, 1):g} {tier.unit}".replace(".", ",")


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
    # Hinweise ohne Verstoss (z.B. Aufbewahrung noch im Aufbau)
    notes: list[str] = field(default_factory=list)
    # nur VMs: Speicherorte mit deren Klasse
    storage: list[dict] = field(default_factory=list)
    resource_group_names: list[str] = field(default_factory=list)
    policy_names: list[str] = field(default_factory=list)
    last_backup_at: datetime | None = None
    # NetApp-Volumes des Objekts ('svm:volume') -- fuer die sekundaere Aufbewahrung
    volumes: list[str] = field(default_factory=list)
    # Bei Verstoss: Protection Groups (passender Art, aktiv), deren Sicherung
    # die Klasse fuer dieses Objekt erfuellen wuerde und in denen es noch nicht ist.
    suggested_groups: list[str] = field(default_factory=list)


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
    # Stufe laut Zeitplan: Index in TIERS
    tier: int = 1


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
                    tier=0 if mean < 24 else 1 if mean == 24 else 2 if mean <= 168 else 3,
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

    def check(self, volumes: list[str], links: list[_Link], required_days: int, tier: _Tier) -> list[str]:
        """Verstoesse gegen die verlangte sekundaere Aufbewahrung einer Stufe
        (links = Policies dieser oder einer feineren Stufe)."""
        want = _fmt_span(required_days, tier)
        secondary = [l for l in links if l.secondary]
        if not secondary:
            return [f"sekundär: keine {tier.adjective} Policy mit SnapMirror-Update (Klasse verlangt {want})"]
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
                    violations.append(
                        f"sekundär: {tier.adjective} Sicherungen ca. {_fmt_span(best, tier)} aufbewahrt ({volume}), Klasse verlangt {want}"
                    )
                else:
                    violations.append(
                        f"sekundär: {tier.adjective} Sicherungen nicht aufbewahrt ({volume}): {reason or 'keine passende Regel'}"
                    )
        return violations


def check_config(
    cls: ProtectionClass, links: list[_Link], snapmirror: "_SnapMirror | None" = None, volumes: list[str] | None = None,
) -> list[str]:
    """Soll-Pruefung: erfuellen diese Policy-Verknuepfungen (fuer diese Volumes)
    die Klasse? Leere Liste = ja. Auch Grundlage fuer "welche Klassen erfuellt
    eine Protection Group" und fuer den Vorschlag passender Gruppen."""
    violations: list[str] = []
    max_age = cls.max_backup_age_hours
    if not links:
        return ["kein aktiver Zeitplan (Gruppe, Policy oder Zeitplan pausiert bzw. ohne Zeitplan)"]
    best_gap = min(l.gap_hours for l in links)
    if best_gap > max_age:
        violations.append(f"Sicherungsabstand {_fmt_hours(best_gap)}, Klasse verlangt höchstens {_fmt_hours(max_age)}")
    seen: set[str] = set()
    for tier, primary_days, secondary_days in tier_requirements(cls):
        candidates = [l for l in links if l.tier <= tier.index]
        found: list[str] = []
        if primary_days:
            best_retention = max((l.retention_days for l in candidates), default=0.0)
            if not candidates:
                found.append(f"primär: keine {tier.adjective} Policy (Klasse verlangt {_fmt_span(primary_days, tier)})")
            elif best_retention + 0.01 < primary_days:
                found.append(
                    f"primär: {tier.adjective} Sicherungen ca. {_fmt_span(best_retention, tier)} aufbewahrt, "
                    f"Klasse verlangt {_fmt_span(primary_days, tier)}"
                )
        if secondary_days and snapmirror is not None:
            found.extend(snapmirror.check(volumes or [], candidates, secondary_days, tier))
        # 'keine SnapMirror-Beziehung fuer Volume X' kaeme sonst je Stufe einmal
        violations.extend(v for v in found if v not in seen)
        seen.update(found)
    if cls.require_app_consistent:
        consistent = [l.gap_hours for l in links if l.app_consistent]
        if not consistent:
            violations.append("keine applikationskonsistente Policy")
        elif min(consistent) > max_age and best_gap <= max_age:
            violations.append(
                f"applikationskonsistent nur alle {_fmt_hours(min(consistent))}, Klasse verlangt höchstens {_fmt_hours(max_age)}"
            )
    return violations


def _check_backup(
    cls: ProtectionClass, links: list[_Link], protected: bool, last_primary: datetime | None, last_secondary: datetime | None,
    now: datetime, snapmirror: "_SnapMirror | None" = None, volumes: list[str] | None = None,
) -> list[str]:
    violations: list[str] = []
    max_age = cls.max_backup_age_hours
    secondary_required = any(secondary for _, _, secondary in tier_requirements(cls))
    if not protected:
        return ["in keiner Protection Group"]
    violations.extend(check_config(cls, links, snapmirror, volumes))
    # --- Ist
    tolerance = timedelta(hours=max(0.5, max_age * 0.1))
    newest = max((t for t in (last_primary, last_secondary) if t is not None), default=None)
    if newest is None:
        violations.append("noch kein erfolgreiches Backup")
    elif now - newest > timedelta(hours=max_age) + tolerance:
        violations.append(f"letztes Backup vor {_fmt_hours(round((now - newest).total_seconds() / 3600))}, erlaubt {_fmt_hours(max_age)}")
    if secondary_required:
        limit = timedelta(hours=max(max_age, SECONDARY_MIN_AGE_HOURS)) + tolerance
        if last_secondary is None:
            violations.append("keine sekundäre Kopie vorhanden")
        elif now - last_secondary > limit:
            violations.append(f"sekundäre Kopie vor {_fmt_hours(round((now - last_secondary).total_seconds() / 3600))}")
    return violations


def _reach_notes(
    cls: ProtectionClass, links: list[_Link], oldest_primary: dict[str, datetime], oldest_secondary: dict[str, datetime],
    now: datetime,
) -> list[str]:
    """Hinweise, wenn die aelteste vorhandene Sicherung einer Stufe noch nicht
    so weit zurueckreicht, wie die Klasse verlangt (oldest_*: Policy-Name ->
    aelteste vorhandene Sicherung des Objekts)."""
    requirements = tier_requirements(cls)
    if not requirements:
        return ["Aufbewahrung nicht definiert (Stufen in der Schutzklasse eintragen)"]
    notes = []
    for tier, primary_days, secondary_days in requirements:
        policies = {l.policy_name for l in links if l.tier <= tier.index}
        for label, store, required in (("primär", oldest_primary, primary_days), ("sekundär", oldest_secondary, secondary_days)):
            oldest = min((store[p] for p in policies if p in store), default=None)
            if not required or oldest is None:
                continue
            age = (now - oldest).total_seconds() / 86400
            if age + tier.reach_tolerance_days < required:
                notes.append(
                    f"{label} im Aufbau ({tier.adjective} Sicherungen): reichen erst bis {oldest.astimezone().strftime('%d.%m.%Y')} "
                    f"zurück ({_fmt_span(age, tier)}), Klasse verlangt {_fmt_span(required, tier)}"
                )
    return notes


def backup_times(db: Session) -> tuple[dict[str, datetime], dict[str, datetime], dict[str, dict], dict[str, dict]]:
    """Vorhandene Backups je 'vm:<Name>', 'csv:<Name>', 'vol:<SVM>:<Volume>':
    (letztes primaer, letztes sekundaer, aeltestes primaer je Policy-Name,
    aeltestes sekundaer je Policy-Name); gleiche Logik wie der Schutzstatus-Report."""
    not_captured = {(c.run_id, c.vm_name) for c in db.query(BackupRunVmConfig).filter(BackupRunVmConfig.not_captured.is_(True))}
    primary: dict[str, datetime] = {}
    secondary: dict[str, datetime] = {}
    oldest_primary: dict[str, dict[str, datetime]] = {}
    oldest_secondary: dict[str, dict[str, datetime]] = {}

    def note(store: dict, oldest: dict, key: str, policy: str, when: datetime) -> None:
        if key not in store or when > store[key]:
            store[key] = when
        per_policy = oldest.setdefault(key, {})
        if policy not in per_policy or when < per_policy[policy]:
            per_policy[policy] = when

    rows = db.query(BackupRunSnapshot).options(selectinload(BackupRunSnapshot.destinations), selectinload(BackupRunSnapshot.run)).all()
    for row in rows:
        has_secondary = any(d.present for d in row.destinations)
        if not (row.success or has_secondary):
            continue
        keys = [f"vm:{v}" for v in (row.vm_names or []) if (row.run_id, v) not in not_captured]
        keys += [f"csv:{c}" for c in (row.csv_names or [])]
        keys.append(f"vol:{row.svm_name}:{row.volume_name}")
        when = _aware(row.created_at)
        policy = row.run.policy_name if row.run is not None else ""
        for key in keys:
            if row.success:
                note(primary, oldest_primary, key, policy, when)
            if has_secondary:
                note(secondary, oldest_secondary, key, policy, when)
    return primary, secondary, oldest_primary, oldest_secondary


def last_backups(db: Session) -> tuple[dict[str, datetime], dict[str, datetime]]:
    """Letztes erfolgreiches Backup je Schluessel, primaer und sekundaer."""
    return backup_times(db)[:2]


def evaluate(db: Session, now: datetime | None = None) -> list[ObjectStatus]:
    from app.api.routes.vms import _csv_names_for_vm, _smb_share_keys_for_vm, list_csvs, list_smb_shares, list_vms

    now = now or datetime.now(timezone.utc)
    classes = {c.id: c for c in db.query(ProtectionClass).all()}
    assignments = db.query(ProtectionClassAssignment).all()
    by_name = {(a.object_type, a.cluster_id, a.object_name): a for a in assignments}
    by_uuid = {(a.cluster_id, a.vm_uuid.lower()): a for a in assignments if a.object_type == "vm" and a.vm_uuid}
    vm_uuids = {(v.cluster_id, v.name): (v.vm_uuid or "").lower() for v in db.query(HyperVVm).all()}
    all_groups = db.query(ResourceGroup).all()
    links = _effective_links(all_groups)
    primary, secondary, oldest_primary, oldest_secondary = backup_times(db)

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
            resource_group_names=list(groups), policy_names=list(policies), volumes=list(volumes),
            last_backup_at=max((t for t in (primary.get(backup_key), secondary.get(backup_key)) if t), default=None),
        )
        if cls is not None:
            status.class_id, status.class_name, status.class_color = cls.id, cls.name, cls.color
            object_links = [l for g in groups for l in links.get(g, [])]
            status.violations = _check_backup(
                cls, object_links, bool(groups), primary.get(backup_key), secondary.get(backup_key), now, snapmirror, volumes,
            )
            status.notes = _reach_notes(cls, object_links, oldest_primary.get(backup_key, {}), oldest_secondary.get(backup_key, {}), now)
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
    scope_of = {"vm": BackupScope.VM, "csv": BackupScope.CSV, "smb_share": BackupScope.SMB_SHARE}
    for status in result:
        if not status.class_id:
            continue
        status.status = "violation" if status.violations else "ok"
        if status.violations:
            cls = classes[status.class_id]
            status.suggested_groups = sorted(
                g.name for g in all_groups
                if g.scope == scope_of[status.object_type] and g.name not in status.resource_group_names
                and not check_config(cls, links.get(g.name, []), snapmirror, status.volumes)
            )
    return result


@dataclass
class GroupFit:
    group_id: str
    group_name: str
    scope: str
    paused: bool
    member_count: int
    # je Klasse: erfuellt? sonst die Gruende
    classes: list[dict] = field(default_factory=list)


def group_fit(db: Session) -> list[GroupFit]:
    """Welche Schutzklassen erfuellt jede Protection Group mit ihren Policies
    und Zeitplaenen -- berechnet, nicht von Hand zugeordnet (Nutzer-Freigabe
    2026-10-06). Die sekundaere Aufbewahrung wird gegen die Volumes der
    aktuellen Mitglieder geprueft; ohne Mitglieder nur die Konfiguration."""
    classes = db.query(ProtectionClass).order_by(ProtectionClass.rank, ProtectionClass.name).all()
    groups = db.query(ResourceGroup).order_by(ResourceGroup.name).all()
    links = _effective_links(groups)
    snapmirror = _SnapMirror(db)
    volumes: dict[str, set[str]] = {}
    for status in evaluate(db):
        for name in status.resource_group_names:
            volumes.setdefault(name, set()).update(status.volumes)
    result = []
    for group in groups:
        fit = GroupFit(
            group_id=group.id, group_name=group.name, scope=str(getattr(group.scope, "value", group.scope)), paused=bool(group.paused),
            member_count=len(group.members or []),
        )
        for cls in classes:
            reasons = check_config(cls, links.get(group.name, []), snapmirror, sorted(volumes.get(group.name, set())))
            fit.classes.append({"class_id": cls.id, "class_name": cls.name, "class_color": cls.color, "fits": not reasons, "reasons": reasons})
        result.append(fit)
    return result
