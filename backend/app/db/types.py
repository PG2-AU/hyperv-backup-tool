"""Zeitzone-bewusster Ersatz fuer sqlalchemy.DateTime.

Alle Zeitstempel in dieser App werden ausschliesslich als UTC geschrieben
(durchgehend ueber datetime.now(timezone.utc)/_now()-Helper in den
Modellen). SQLite hat aber keinen echten zeitzonenbewussten Spaltentyp --
SQLAlchemy liefert beim Lesen daher ein NAIVES datetime-Objekt zurueck
(tzinfo=None), obwohl der gespeicherte Wert UTC ist. Ohne Gegenmassnahme
serialisiert Pydantic/FastAPI das als ISO-String ohne Zeitzone (z.B.
'2026-08-27T21:28:27'), was der Browser beim Parsen (new Date(...)) gemaess
ECMAScript-Spezifikation faelschlich als LOKALE Zeit statt als UTC
interpretiert -- gegen echte Nutzung verifizierter Bug: ein um 23:26 Uhr
Lokalzeit (CEST, UTC+2) erstellter Snapshot wurde in der GUI als 21:26 Uhr
angezeigt (die korrekte UTC-Uhrzeit, aber ohne Zeitzone erneut als
Lokalzeit missinterpretiert).

Dieser TypeDecorator haengt beim Lesen aus der DB explizit UTC an ein
naives datetime, bevor es Pydantic/FastAPI erreicht -- dadurch wird korrekt
mit Offset ('...+00:00') serialisiert und der Browser rechnet richtig in
seine lokale Zeitzone um. Einziger Aenderungsaufwand in den Modellen: statt
'from sqlalchemy import DateTime' hier importieren -- die eigentlichen
mapped_column(DateTime, ...)-Definitionen bleiben unveraendert."""

from datetime import datetime, timezone

from sqlalchemy import DateTime as _DateTime
from sqlalchemy.types import TypeDecorator


class DateTime(TypeDecorator):
    impl = _DateTime
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
