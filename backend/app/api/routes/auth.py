from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_user_permissions
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.ad_config import AdConfig
from app.models.user import User, UserSource
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services.ad_service import ActiveDirectoryService, _bare_username

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    # Toleriert sowohl 'netapp.service' als auch 'DOMAIN\netapp.service' im
    # Login-Feld -- auf die reine sAMAccountName-Form normalisieren, BEVOR
    # gegen User.username nachgeschlagen wird. Sonst wuerde ein Login mit
    # domain-qualifiziertem Namen ein bereits ueber "Benutzer hinzufuegen" >
    # Active Directory vorab angelegtes (und mit Rolle versehenes!) Konto
    # verfehlen -- dessen User.username ist immer die reine sAMAccountName-
    # Form (aus der AD-Suche uebernommen) -- und stattdessen faelschlich
    # einen zweiten, rechtelosen JIT-Account anlegen. Live gefunden
    # 2026-09-16 in der Produktionsumgebung.
    username = _bare_username(payload.username)
    user = db.query(User).filter(User.username == username).first()

    # Ein bestehendes LOKALES Konto (inkl. des initialen admin-Kontos)
    # wird IMMER lokal geprueft, unabhaengig davon, ob AD aktiviert ist
    # -- sonst waeren lokale Konten nicht mehr nutzbar, sobald AD
    # aktiviert wird (live gefunden 2026-09-16, Nutzer-Vorgabe: lokale
    # und AD-Benutzer muessen nebeneinander funktionieren).
    if user is not None and user.source == UserSource.LOCAL:
        if user.hashed_password is None or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungueltige Anmeldedaten")
    else:
        ad_config = db.query(AdConfig).first()
        if ad_config is None or not ad_config.enabled:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungueltige Anmeldedaten")

        ad_service = ActiveDirectoryService(ad_config.server, ad_config.domain, ad_config.base_dn, ad_config.use_ssl)
        result = ad_service.authenticate(username, payload.password)
        if not result.success:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.error or "Login fehlgeschlagen")

        if user is None:
            user = User(
                username=username,
                display_name=result.display_name,
                email=result.email,
                source=UserSource.ACTIVE_DIRECTORY,
            )
            db.add(user)
        else:
            user.display_name = result.display_name or user.display_name
            user.email = result.email or user.email

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Benutzer ist deaktiviert")

    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)

    token = create_access_token(subject=user.id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CurrentUser:
    permissions = get_user_permissions(user, db)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        permissions=sorted(p.value for p in permissions),
    )
