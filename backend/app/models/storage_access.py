"""Globaler Sicherheits-Schalter fuer alle Storage-Aktionen (Settings >
Storage): ist er deaktiviert, lehnt das Backend jede aendernde Storage-
Operation ab (Volume/LUN/IGroup/SnapMirror/Cluster-Peer/SVM-Peer/
SnapMirror-Policy/-Schedule anlegen/aendern/loeschen, Cluster verifizieren/
discovern/Zertifikat umstellen/entfernen) -- ausgenommen einzig das
Hinzufuegen eines NEUEN NetApp-Clusters, damit ein Storage-Admin die
initiale Anbindung trotzdem vornehmen kann. Rein additiv zur bestehenden
RBAC-Permission (STORAGE_MANAGE) -- eine Aktion braucht beides: die
Berechtigung UND diesen globalen Schalter. Singleton-Zeile analog zu
AlertConfig/SchedulerConfig. Nutzer-Motivation: das Tool wird ggf. nicht
nur von Storage-Admins bedient, versehentliche Storage-Aenderungen durch
andere Rollen sollen global unterbunden werden koennen."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import DateTime


class StorageAccessConfig(Base):
    __tablename__ = "storage_access_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actions_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
