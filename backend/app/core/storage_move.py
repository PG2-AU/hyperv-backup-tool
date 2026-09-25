"""Pfad-Abbildung fuer den Storage-Move einer VM (VM verschieben, Stufe 2,
siehe app.api.routes.vm_moves).

Nutzer-Vorgabe 2026-09-25: die Ordnerstruktur der Quelle bleibt erhalten.
Jeder Pfad unterhalb von C:\\ClusterStorage\\<Quell-CSV>\\ wird 1:1 unter
C:\\ClusterStorage\\<Ziel-CSV>\\ abgebildet -- liegt die VM auf der Quelle
in einem eigenen Ordner (z.B. ...\\VM01\\Virtual Hard Disks\\VM01.vhdx),
entsteht auf dem Ziel exakt derselbe Ordner; liegt eine VHDX direkt im
CSV-Stamm, landet sie auch auf dem Ziel direkt im Stamm. Reine Funktion
ohne WinRM, damit sie isoliert pruefbar ist."""

import re
from dataclasses import dataclass, field
from ntpath import join as win_join

from app.services.hyperv_service import VmStorageLayout

_CSV_PATH_RE = re.compile(r"^(?P<root>[A-Za-z]:\\ClusterStorage\\[^\\]+)(?P<rest>\\.*)?$", re.IGNORECASE)


def split_csv_path(path: str | None) -> tuple[str, str] | None:
    """('C:\\ClusterStorage\\Volume1', '\\VM01\\disk.vhdx') oder None, wenn
    der Pfad nicht auf einer CSV liegt."""
    if not path:
        return None
    match = _CSV_PATH_RE.match(path.strip().rstrip("\\"))
    if not match:
        return None
    return match.group("root"), match.group("rest") or ""


def _same(a: str, b: str) -> bool:
    return a.rstrip("\\").lower() == b.rstrip("\\").lower()


@dataclass
class StorageMovePlan:
    virtual_machine_path: str | None = None
    snapshot_file_path: str | None = None
    smart_paging_file_path: str | None = None
    vhd_moves: list[tuple[str, str]] = field(default_factory=list)
    # Dateien, die am Ziel noch nicht existieren duerfen.
    collision_candidates: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.virtual_machine_path or self.snapshot_file_path or self.smart_paging_file_path or self.vhd_moves)

    def summary(self) -> str:
        lines = [f"{src} → {dst}" for src, dst in self.vhd_moves]
        for label, value in (
            ("Konfiguration", self.virtual_machine_path),
            ("Checkpoints", self.snapshot_file_path),
            ("Smart Paging", self.smart_paging_file_path),
        ):
            if value:
                lines.append(f"{label} → {value}")
        return "; ".join(lines)


def plan_storage_move(layout: VmStorageLayout, destination_root: str) -> StorageMovePlan:
    """Bildet alle Ablageorte der VM auf die Ziel-CSV ab. Bereits auf der
    Ziel-CSV liegende Orte bleiben unangetastet. Blockierende Gruende
    (Checkpoints, AVHDX, Pfade ausserhalb einer CSV, doppelte Zielpfade)
    landen in plan.errors -- der Aufrufer bricht dann VOR dem Move ab."""
    plan = StorageMovePlan()
    dest_root = destination_root.rstrip("\\")

    if layout.checkpoint_count:
        plan.errors.append(
            f"Die VM hat {layout.checkpoint_count} Checkpoint(s) -- bitte vorher entfernen bzw. zusammenführen, "
            "damit die Differenzdateien nicht getrennt von ihrer Basis-VHDX verschoben werden."
        )

    def _map(path: str | None, label: str) -> str | None:
        if not path:
            return None
        parts = split_csv_path(path)
        if parts is None:
            plan.errors.append(f"{label} liegt nicht auf einer CSV ({path}) -- wird von der Verschiebung nicht unterstützt.")
            return None
        root, rest = parts
        if _same(root, dest_root):
            return None  # liegt bereits auf der Ziel-CSV
        return dest_root + rest

    seen_destinations: dict[str, str] = {}
    for vhd in layout.vhd_paths:
        if vhd.lower().endswith(".avhdx"):
            plan.errors.append(f"Festplatte {vhd} ist eine AVHDX-Differenzdatei -- bitte vorher Checkpoints zusammenführen.")
            continue
        destination = _map(vhd, f"Festplatte {vhd}")
        if destination is None:
            continue
        key = destination.lower()
        if key in seen_destinations:
            plan.errors.append(
                f"Zwei Festplatten würden am Ziel denselben Pfad bekommen ({destination}: {seen_destinations[key]} und {vhd})."
            )
            continue
        seen_destinations[key] = vhd
        plan.vhd_moves.append((vhd, destination))
        plan.collision_candidates.append(destination)

    plan.virtual_machine_path = _map(layout.configuration_location, "Der Konfigurationsordner")
    if plan.virtual_machine_path and layout.vm_id:
        # Die eigentliche Konfiguration liegt unterhalb des Ordners in
        # 'Virtual Machines\<GUID>.vmcx' -- der Ordner selbst darf bereits
        # existieren (z.B. ein gemeinsamer Hyper-V-Ordner fuer viele VMs).
        plan.collision_candidates.append(win_join(plan.virtual_machine_path, "Virtual Machines", f"{layout.vm_id}.vmcx"))
    plan.snapshot_file_path = _map(layout.snapshot_file_location, "Der Checkpoint-Ordner")
    plan.smart_paging_file_path = _map(layout.smart_paging_file_path, "Der Smart-Paging-Ordner")

    if not plan.errors and plan.is_empty:
        plan.errors.append("Alle Dateien der VM liegen bereits auf der Ziel-CSV.")
    return plan


def verify_storage_move(layout: VmStorageLayout, plan: StorageMovePlan) -> list[str]:
    """Vergleicht die tatsaechlichen Ablageorte NACH dem Move mit dem Plan.
    Leere Liste = alles am Ziel."""
    problems: list[str] = []
    current = {p.lower() for p in layout.vhd_paths}
    for src, dst in plan.vhd_moves:
        if dst.lower() not in current:
            problems.append(f"{src} liegt nicht unter {dst}")
    for label, planned, actual in (
        ("Konfiguration", plan.virtual_machine_path, layout.configuration_location),
        ("Checkpoints", plan.snapshot_file_path, layout.snapshot_file_location),
        ("Smart Paging", plan.smart_paging_file_path, layout.smart_paging_file_path),
    ):
        if planned and not (actual and _same(planned, actual)):
            problems.append(f"{label} liegt unter {actual}, erwartet {planned}")
    return problems
