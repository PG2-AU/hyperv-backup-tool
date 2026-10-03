"""Kapazitaetsprognose (Backlog #79, Nutzer-Vorgaben 2026-10-03): aus dem
taeglichen Kapazitaetsverlauf (CapacitySample) 4 Wochen in die Zukunft
schauen -- als gestrichelte Linie im Verlaufsdiagramm und als Alarm, wenn eine
LUN, ein Volume oder ein Aggregat innerhalb dieser 4 Wochen vollaeuft.

Methode bewusst einfach und nachvollziehbar: lineare Regression (kleinste
Quadrate) ueber die Messpunkte der letzten 30 Tage (je Tag der letzte), die
Steigung ist der Zuwachs pro Tag. Die Prognose setzt am letzten echten
Messwert an (nicht am Achsenabschnitt der Regressionsgeraden), damit die
gestrichelte Linie nahtlos an die Kurve anschliesst. "Voll" = belegt erreicht
die zuletzt gemessene Kapazitaet. Zu wenig Daten (< 5 Tage mit Messpunkt oder
weniger als 7 Tage Zeitspanne) -> keine Prognose, statt aus zwei Punkten
Unsinn hochzurechnen. Reine Funktion ohne DB, damit isoliert pruefbar."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

HORIZON_DAYS = 28
WINDOW_DAYS = 30
MIN_POINTS = 5
MIN_SPAN_DAYS = 7


@dataclass
class Forecast:
    # Zuwachs pro Tag in Bytes (negativ = schrumpft).
    growth_bytes_per_day: float
    capacity_bytes: int
    last_used_bytes: int
    last_sampled_at: datetime
    # Tage ab dem letzten Messpunkt bis "voll"; None = waechst nicht.
    days_to_full: float | None
    # Prognose-Punkte: (Zeitpunkt, belegt) fuer Tag 0 (= letzter Messwert) bis HORIZON_DAYS.
    points: list[tuple[datetime, int]] = field(default_factory=list)

    @property
    def full_within_horizon(self) -> bool:
        """Grundlage fuer den Alarm: nur bei echtem Zuwachs. Ein Objekt, das
        schon voll ist, aber nicht waechst (z.B. eine dick provisionierte
        oder ohne UNMAP vollgeschriebene LUN -- live in der Referenz-
        umgebung gesehen), ist ein Normalzustand, kein Vollaufen; das deckt
        der Schwellwert-Alarm ab."""
        return self.growth_bytes_per_day > 0 and self.days_to_full is not None and self.days_to_full <= HORIZON_DAYS

    @property
    def full_at(self) -> datetime | None:
        return self.last_sampled_at + timedelta(days=self.days_to_full) if self.days_to_full is not None else None


def forecast(samples: list[tuple[datetime, int | None, int | None]]) -> Forecast | None:
    """samples: (sampled_at, used_bytes, capacity_bytes), beliebige Reihenfolge."""
    valid = sorted((s for s in samples if s[1] is not None and s[2]), key=lambda s: s[0])
    if not valid:
        return None
    last_at, last_used, last_capacity = valid[-1]
    since = last_at - timedelta(days=WINDOW_DAYS)
    by_day: dict = {}
    for sampled_at, used, _capacity in valid:
        if sampled_at >= since:
            by_day[sampled_at.date()] = (sampled_at, used)  # je Tag der letzte Messpunkt
    points = sorted(by_day.values())
    if len(points) < MIN_POINTS or (points[-1][0] - points[0][0]).total_seconds() < MIN_SPAN_DAYS * 86400:
        return None

    origin = points[0][0]
    xs = [(p[0] - origin).total_seconds() / 86400 for p in points]
    ys = [float(p[1]) for p in points]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator if denominator else 0.0

    if last_used >= last_capacity:
        days_to_full: float | None = 0.0
    elif slope > 0:
        days_to_full = (last_capacity - last_used) / slope
    else:
        days_to_full = None
    projected = [
        (last_at + timedelta(days=d), max(0, round(last_used + slope * d))) for d in range(HORIZON_DAYS + 1)
    ]
    return Forecast(
        growth_bytes_per_day=slope, capacity_bytes=int(last_capacity), last_used_bytes=int(last_used),
        last_sampled_at=last_at, days_to_full=days_to_full, points=projected,
    )
