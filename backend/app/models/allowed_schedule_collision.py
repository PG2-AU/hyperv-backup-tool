"""Persistierte 'Erlaubnis' fuer eine einzelne, vom Nutzer bestaetigte
Zeitplan-Kollision (siehe app.core.scheduler._find_schedule_collisions):
anders als die meisten Alarme (Kapazitaet, Cluster-Gesundheit) soll eine
Kollisionswarnung NICHT bei jedem 15min-Check erneut aufploppen, wenn der
Nutzer sie einmal bewusst akzeptiert hat (Nutzer-Vorgabe: "wenn ich diesen
dann bestaetige, dann soll diese Kollision quasi erlaubt sein"). Eine Zeile
hier unterdrueckt genau EINE Kollision dauerhaft -- identifiziert durch
collision_key (dieselbe stabile Zusammensetzung aus Uhrzeit + beteiligten
Resource-Group/Policy-Paaren wie beim zugehoerigen Alert.object_key).
Aendert sich die Zusammensetzung (z.B. kommt ein dritter Job derselben
Uhrzeit hinzu), ergibt sich ein neuer collision_key -- die alte Erlaubnis
deckt den neuen, strukturell anderen Fall bewusst nicht ab, es entsteht ein
neuer, erneut zu bestaetigender Alarm."""

import uuid
from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class AllowedScheduleCollision(Base):
    __tablename__ = "allowed_schedule_collisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    collision_key: Mapped[str] = mapped_column(String(1024), unique=True)
    # Freitext-Momentaufnahme zum Zeitpunkt des Erlaubens (Gruppen-/Policy-
    # Namen koennen sich spaeter aendern/geloescht werden) -- fuer die
    # Verwaltungsliste in Settings > Alarme, damit dort auch ohne erneute
    # Aufloesung des Schluessels erkennbar ist, worum es ging.
    summary: Mapped[str] = mapped_column(String(500))
    allowed_at: Mapped[datetime] = mapped_column(DateTime)
