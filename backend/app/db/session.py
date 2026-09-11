from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

# timeout=30: SQLite's Default (5s) reicht nicht, wenn zwei Backup-Laeufe
# (z.B. zwei leicht versetzte Resource-Group-Zeitplaene derselben Policy)
# echt ueberlappen -- live reproduziert ("database is locked",
# sqlite3.OperationalError) durch zwei absichtlich parallel gestartete
# _execute_job_run-Laeufe. 30s laesst SQLite selbst auf den Lock warten,
# statt sofort aufzugeben.
connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
