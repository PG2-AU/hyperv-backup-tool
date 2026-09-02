import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import DateTime
from app.models.backup_policy import BackupScope


class ResourceGroupPolicyLink(Base):
    """Verknuepfung einer Resource Group mit einer Policy -- ersetzt eine
    fruehere reine Zuordnungstabelle (siehe init_db.py-Migration). Traegt
    jetzt zusaetzlich einen eigenen Zeitplan: Nutzer-Ueberlegung, dass eine
    Resource Group an mehrere Policies mit unterschiedlicher Kadenz gehaengt
    sein kann (z.B. dasselbe CSV stuendlich UND woechentlich sichern, ueber
    zwei verschiedene Policies mit je eigener Retention) -- ein einzelner
    Zeitplan pro Resource Group (fruehere Version) haette diesen Fall nicht
    abgebildet, ohne fuer jede Kadenz eine eigene Resource Group anzulegen.
    Der Zeitplan haengt daher an GENAU DIESER Verknuepfung, nicht an der
    Resource Group oder der Policy allein."""

    __tablename__ = "resource_group_policies"

    resource_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("resource_groups.id"), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(36), ForeignKey("backup_policies.id"), primary_key=True)
    schedule_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("schedules.id"), nullable=True)

    resource_group = relationship("ResourceGroup", back_populates="policy_links")
    policy = relationship("BackupPolicy")
    schedule = relationship("Schedule")

    @property
    def policy_name(self) -> str | None:
        return self.policy.name if self.policy else None


_MEMBER_SEP = "::"


def make_member_key(cluster_id: str, name: str) -> str:
    """Baut den in ResourceGroup.members gespeicherten Eintrag fuer eine VM
    oder ein CSV -- cluster-qualifiziert, damit zwei Hyper-V-Cluster mit
    identisch benannten VMs/CSVs (z.B. beide "CSV01") nicht kollidieren.
    Siehe parse_member_key/resolve_member_key fuer das Gegenstueck."""
    return f"{cluster_id}{_MEMBER_SEP}{name}"


def parse_member_key(member: str) -> tuple[str | None, str]:
    """Zerlegt einen Member-Eintrag in (cluster_id, name). Liefert
    (None, member) fuer noch nicht migrierte/mehrdeutige Alt-Eintraege (reiner
    Name ohne Cluster-Qualifikation, siehe init_db.py-Migration) -- Aufrufer
    muessen diesen Fall als "Cluster unbekannt" behandeln, nicht als Fehler."""
    if _MEMBER_SEP in member:
        cluster_id, name = member.split(_MEMBER_SEP, 1)
        return cluster_id, name
    return None, member


def resolve_member_key(member: str, valid_keys: set[tuple[str, str]]) -> tuple[str, str] | None:
    """Loest einen Member-Eintrag gegen eine Menge aktuell bekannter
    (cluster_id, name)-Schluessel auf (z.B. aus der aktuellen Hyper-V-
    Discovery). Ein cluster-qualifizierter Eintrag muss exakt matchen; ein
    noch nicht migrierter reiner Name matcht nur, wenn er unter GENAU EINEM
    Cluster bekannt ist -- bei mehreren Clustern mit demselben Namen wird
    bewusst NICHT geraten (None), um den urspruenglichen Bug (stille
    Kollision zwischen Clustern) nicht im Fallback zu wiederholen."""
    cluster_id, name = parse_member_key(member)
    if cluster_id is not None:
        return (cluster_id, name) if (cluster_id, name) in valid_keys else None
    candidates = [k for k in valid_keys if k[1] == name]
    return candidates[0] if len(candidates) == 1 else None


class ResourceGroup(Base):
    """Buendelt VMs oder CSVs (nie gemischt, siehe `scope`) zu einer benannten
    Gruppe (z.B. 'Bronze'), die mit einer oder mehreren Backup-Policies
    verknuepft wird. Die Kombination aus Resource Group + Policy ergibt die
    tatsaechliche Backup-Definition (was wird wann/wie gesichert).

    Der Zeitplan haengt an der jeweiligen Verknuepfung (siehe
    ResourceGroupPolicyLink), NICHT an der Resource Group selbst und NICHT
    an der Policy -- Nutzer-Ueberlegung: bei vielen CSVs, die dieselbe Policy
    (z.B. 'Silver') teilen, wuerden sonst ALLE gleichzeitig gesichert (ein
    gemeinsamer Zeitplan loest alle verknuepften Resource Groups in einem
    Lauf aus) -- Snapshot-/VSS-Lastspitze auf NetApp/Hyper-V. Ausserdem kann
    dieselbe Resource Group an mehrere Policies mit unterschiedlicher Kadenz
    haengen (z.B. ein CSV stuendlich UND woechentlich, je eigene Policy) --
    ein Zeitplan pro Verknuepfung statt pro Resource Group bildet das direkt
    ab, ohne fuer jede Kadenz eine eigene Resource Group anzulegen."""

    __tablename__ = "resource_groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), unique=True)
    scope: Mapped[BackupScope] = mapped_column(Enum(BackupScope))
    members: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Fuer den einfachen Lesezugriff (welche Policies sind verknuepft) --
    # viewonly, da das Schreiben ueber policy_links laeuft (traegt den
    # Zeitplan pro Verknuepfung mit).
    policies = relationship("BackupPolicy", secondary=ResourceGroupPolicyLink.__table__, viewonly=True)
    policy_links = relationship("ResourceGroupPolicyLink", back_populates="resource_group", cascade="all, delete-orphan")
