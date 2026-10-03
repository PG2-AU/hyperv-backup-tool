"""Gemeinsame Bausteine der Reports (Backlog #84): Inhalt als Datenstruktur
(unabhaengig vom PDF, damit dieselben Daten auch als CSV und Mail-
Zusammenfassung dienen) und die Aufloesung relativer Zeitraeume.

Zeitraeume sind relativ waehlbar ("Vormonat", "letzte 7 Tage" ...), damit eine
monatlich geplante Vorlage jeden Monat den jeweils passenden Zeitraum
liefert. Gerechnet wird in der Zeitzone der Zeitplaene (schedule_timezone)."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings


@dataclass
class Kpi:
    label: str
    value: str
    # ok | warn | bad | neutral -- Ampelfarbe im PDF
    level: str = "neutral"


@dataclass
class Section:
    title: str
    columns: list[str]
    rows: list[list[str]]
    # je Zeile optional ok | warn | bad (Einfaerbung der Statusspalte)
    row_levels: list[str] | None = None
    # relative Spaltenbreiten (Summe beliebig), sonst gleichmaessig
    widths: list[float] | None = None
    note: str | None = None
    empty_text: str = "Keine Eintraege."
    # Index der Spalte, die nach row_levels eingefaerbt wird (Standard: letzte)
    status_col: int = -1


@dataclass
class ReportContent:
    title: str
    subtitle: str
    kpis: list[Kpi] = field(default_factory=list)
    # Vergleich zum Vorzeitraum (nur Reports mit Zeitraum)
    comparison: str | None = None
    findings: int = 0
    findings_text: str = ""
    sections: list[Section] = field(default_factory=list)
    # Haupttabelle fuer den CSV-Export
    csv_columns: list[str] = field(default_factory=list)
    csv_rows: list[list[str]] = field(default_factory=list)


PERIOD_PRESETS = {
    "last_7_days": "Letzte 7 Tage",
    "last_30_days": "Letzte 30 Tage",
    "previous_week": "Vorwoche",
    "current_month": "Aktueller Monat",
    "previous_month": "Vormonat",
    "custom": "Eigener Zeitraum",
}


@dataclass
class Period:
    start: datetime  # UTC, inklusiv
    end: datetime  # UTC, exklusiv
    label: str
    previous_start: datetime
    previous_end: datetime
    previous_label: str


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().schedule_timezone)


def _at_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _fmt(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    return date(day.year + month // 12, month % 12 + 1, 1)


def resolve_period(params: dict, now: datetime | None = None) -> Period:
    """params["period"]: einer der PERIOD_PRESETS; bei "custom" zusaetzlich
    period_from/period_to (ISO-Datum, beide inklusive)."""
    tz = local_tz()
    today = (now or datetime.now(timezone.utc)).astimezone(tz).date()
    preset = params.get("period") or "last_7_days"
    if preset == "custom":
        first = date.fromisoformat(params["period_from"])
        last = date.fromisoformat(params["period_to"])
        if last < first:
            first, last = last, first
    elif preset == "last_30_days":
        first, last = today - timedelta(days=29), today
    elif preset == "previous_week":
        monday = today - timedelta(days=today.weekday())
        first, last = monday - timedelta(days=7), monday - timedelta(days=1)
    elif preset == "current_month":
        first, last = _month_start(today), today
    elif preset == "previous_month":
        first = _add_months(_month_start(today), -1)
        last = _month_start(today) - timedelta(days=1)
    else:
        first, last = today - timedelta(days=6), today

    length = (last - first).days + 1
    if preset == "previous_month":
        # ganzer Monat davor
        prev_first, prev_last = _add_months(first, -1), first - timedelta(days=1)
    elif preset == "current_month":
        # gleich viele Tage im Vormonat (1. bis heute -> 1. bis gleicher Tag)
        prev_first = _add_months(first, -1)
        prev_last = min(prev_first + timedelta(days=length - 1), first - timedelta(days=1))
    else:
        prev_first, prev_last = first - timedelta(days=length), first - timedelta(days=1)
    return Period(
        start=_at_midnight(first, tz), end=_at_midnight(last + timedelta(days=1), tz),
        label=f"{_fmt(first)} – {_fmt(last)}",
        previous_start=_at_midnight(prev_first, tz), previous_end=_at_midnight(prev_last + timedelta(days=1), tz),
        previous_label=f"{_fmt(prev_first)} – {_fmt(prev_last)}",
    )


def fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "–"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_tz()).strftime("%d.%m.%Y %H:%M")


def fmt_age(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "–"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    hours = (now - value).total_seconds() / 3600
    if hours < 48:
        return f"{hours:.0f} h"
    return f"{hours / 24:.0f} Tage"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


def pct(part: int, total: int) -> str:
    return f"{part / total * 100:.0f} %" if total else "–"


def fmt_num(value: float, digits: int = 1) -> str:
    """Dezimalzahl mit deutschem Komma."""
    return f"{value:.{digits}f}".replace(".", ",")
