"""Gemeinsame Schluesselableitung fuer den Kapazitaetsverlauf (Settings >
--, siehe app.core.scheduler.run_capacity_history_sampling und
app.api.routes.capacity_history) -- von BEIDEN Seiten genutzt, damit der
Sammel-Job und der Abfrage-Endpunkt garantiert denselben Schluessel fuer
dasselbe Objekt berechnen.

Die primaere id-Spalte von HyperVVhd/HyperVCsv/NetAppVolume/NetAppLun/
NetAppAggregate ist bei JEDEM Discovery-Lauf eine neue Zufalls-UUID (die
Zeilen werden ersetzt, nicht aktualisiert) -- als Zeitreihen-Schluessel
untauglich. Stattdessen wird hier aus stabilen Objekteigenschaften
abgeleitet:
- VHD: es gibt keine eigene stabile GUID -- der volle Dateipfad (aendert
  sich nur bei einem echten Storage-vMotion/Umbenennen der VHDX) dient
  als Ersatz, gepaart mit der Cluster-ID.
- CSV: der CSV-Name ist die stabile Resource-Kennung in Hyper-V (analog
  zu _resolve_csv_name in hyperv_clusters.py), NICHT der beim Discovery
  aufgeloeste Mount-Ordner.
- LUN/Volume/Aggregat: ONTAPs eigene UUID ist stabil ueber die gesamte
  Lebensdauer des Objekts -- Fallback auf den Namen, falls uuid
  ausnahmsweise fehlt (laut Modell nullable)."""

from typing import Literal

CapacityObjectType = Literal["vhd", "csv", "lun", "volume", "aggregate"]


def capacity_key(object_type: CapacityObjectType, cluster_id: str, **kwargs: str | None) -> str:
    if object_type == "vhd":
        path = kwargs.get("path") or ""
        return f"{cluster_id}::{path}"
    if object_type == "csv":
        name = kwargs.get("name") or ""
        return f"{cluster_id}::{name}"
    if object_type in ("lun", "volume", "aggregate"):
        identifier = kwargs.get("uuid") or kwargs.get("name") or ""
        return f"{cluster_id}::{identifier}"
    raise ValueError(f"Unbekannter object_type: {object_type}")
