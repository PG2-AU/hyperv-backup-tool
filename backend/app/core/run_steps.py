"""Offene Schritte eines beendeten Ablaufs schliessen (Nutzer-Meldung
2026-10-03): ein fehlgeschlagener oder abgebrochener Backup-Lauf liess seine
parallel laufenden Checkpoint-Schritte auf 'running' stehen -- im Protokoll
drehten sie sich weiter. Laufende Schritte werden zu 'error' (mit Hinweis),
noch nicht begonnene zu 'skipped'."""

from sqlalchemy.orm import Session


def close_open_steps(db: Session, step_model: type, run_id: str, note: str) -> int:
    count = 0
    for step in db.query(step_model).filter(step_model.run_id == run_id, step_model.status.in_(("running", "pending"))):
        if step.status == "running":
            step.status = "error"
            step.message = f"{step.message} -- {note}" if step.message else note
        else:
            step.status = "skipped"
        count += 1
    return count
