from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.crypto import decrypt_secret
from app.core.rbac import Permission
from app.core.security import hash_password
from app.db.session import get_db
from app.models.ad_config import AdConfig
from app.models.role import Role, RoleAssignment
from app.models.system_log import SystemLogEvent
from app.models.user import User, UserSource
from app.schemas.user import ADUserAddRequest, ADUserSearchRequest, ADUserSearchResult, UserCreate, UserPasswordUpdate, UserRead
from app.services.ad_service import ActiveDirectoryService
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["users"])

MIN_PASSWORD_LENGTH = 8


def _log_user_action(db: Session, actor: User, message: str, level: str = "INFO") -> None:
    """Persistiert eine Benutzerverwaltungs-Aktion im System Log (Nutzerwunsch:
    mehr Log-Eintraege) -- gleiches Muster wie _log_storage_action in
    netapp_clusters.py, eigene source="users" statt "storage"."""
    actor_name = actor.display_name or actor.username
    db.add(SystemLogEvent(level=level, source="users", message=f"{message} (durch {actor_name})"))
    db.commit()


class RoleRead(BaseModel):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system_role: bool


def _require_min_password_length(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein",
        )


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db), user=Depends(require_permission(Permission.USER_MANAGE))) -> list[User]:
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.USER_MANAGE)),
) -> User:
    _require_min_password_length(payload.password)

    if db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Benutzername bereits vergeben")

    role = None
    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rolle nicht gefunden")

    new_user = User(
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        source=UserSource.LOCAL,
        hashed_password=hash_password(payload.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if role is not None:
        db.add(RoleAssignment(user_id=new_user.id, role_id=role.id, scope_type="global"))
        db.commit()

    _log_user_action(db, user, f"Lokaler Benutzer '{new_user.username}' angelegt" + (f" (Rolle: {role.name})" if role else ""))

    return new_user


@router.post("/users/ad-search", response_model=list[ADUserSearchResult])
def search_ad_users(
    payload: ADUserSearchRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.USER_MANAGE)),
) -> list[ADUserSearchResult]:
    """Durchsucht das AD-Verzeichnis nach Benutzern -- fuer das proaktive
    Hinzufuegen ueber 'Benutzer hinzufuegen' > Active Directory (Backlog
    #12, schlanke Variante ohne Gruppe-zu-Rolle-Mapping). Nutzt das in
    Settings > Active Directory hinterlegte Lese-Service-Konto, nicht
    die Zugangsdaten des aufrufenden Admins."""
    config = db.query(AdConfig).first()
    if config is None or not config.enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Active-Directory-Integration ist nicht aktiviert (Settings > Active Directory).")
    if not config.bind_user or not config.encrypted_bind_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein AD-Lese-Service-Konto konfiguriert (Settings > Active Directory).",
        )

    service = ActiveDirectoryService(config.server, config.domain, config.base_dn, config.use_ssl)
    result = service.search_users(config.bind_user, decrypt_secret(config.encrypted_bind_password), payload.query)
    if not result.success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.error or "AD-Suche fehlgeschlagen")
    return [ADUserSearchResult(username=u.username, display_name=u.display_name, email=u.email) for u in result.users]


@router.post("/users/ad-add", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def add_ad_user(
    payload: ADUserAddRequest, db: Session = Depends(get_db), user=Depends(require_permission(Permission.USER_MANAGE)),
) -> User:
    """Legt einen zuvor per ad-search gefundenen AD-Benutzer proaktiv an
    -- ohne Passwort (Anmeldung erfolgt spaeter per AD-Bind, siehe
    auth.py), mit optional sofort zugewiesener Rolle. Bewusst KEIN
    automatisches Gruppe-zu-Rolle-Mapping (Backlog #12), die Rolle wird
    hier explizit vom Admin gewaehlt."""
    if db.query(User).filter(User.username == payload.username).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Benutzername bereits vergeben")

    role = None
    if payload.role_id is not None:
        role = db.get(Role, payload.role_id)
        if role is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Rolle nicht gefunden")

    new_user = User(
        username=payload.username,
        display_name=payload.display_name,
        email=payload.email,
        source=UserSource.ACTIVE_DIRECTORY,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    if role is not None:
        db.add(RoleAssignment(user_id=new_user.id, role_id=role.id, scope_type="global"))
        db.commit()

    _log_user_action(db, user, f"AD-Benutzer '{new_user.username}' hinzugefuegt" + (f" (Rolle: {role.name})" if role else " (ohne Rolle)"))

    return new_user


@router.put("/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def update_user_password(
    user_id: str,
    payload: UserPasswordUpdate,
    db: Session = Depends(get_db),
    user=Depends(require_permission(Permission.USER_MANAGE)),
) -> None:
    _require_min_password_length(payload.password)

    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Benutzer nicht gefunden")
    if target.source != UserSource.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kennwort kann nur fuer lokale Benutzer geaendert werden",
        )

    target.hashed_password = hash_password(payload.password)
    db.commit()

    _log_user_action(db, user, f"Kennwort geaendert fuer Benutzer '{target.username}'")


@router.get("/roles", response_model=list[RoleRead])
def list_roles(db: Session = Depends(get_db), user=Depends(require_permission(Permission.ROLE_MANAGE))) -> list[Role]:
    return db.query(Role).order_by(Role.name).all()
