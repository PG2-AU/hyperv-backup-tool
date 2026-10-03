from datetime import datetime

from pydantic import BaseModel


class CapacitySamplePoint(BaseModel):
    sampled_at: datetime
    capacity_bytes: int | None = None
    used_bytes: int | None = None
    snapshot_used_bytes: int | None = None


class CapacityForecastRead(BaseModel):
    """Prognose 4 Wochen voraus (Backlog #79, app.core.capacity_forecast)."""

    growth_bytes_per_day: float
    capacity_bytes: int
    # Tage ab dem letzten Messpunkt bis "voll"; None = waechst nicht.
    days_to_full: float | None = None
    full_at: datetime | None = None
    horizon_days: int
    # Tag 0 (= letzter Messwert) bis horizon_days.
    points: list[CapacitySamplePoint]


class CapacitySeries(BaseModel):
    object_key: str
    object_name: str
    points: list[CapacitySamplePoint]
    # None = zu wenige Messpunkte fuer eine Prognose.
    forecast: CapacityForecastRead | None = None
