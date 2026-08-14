from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_user_permissions
from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.db.session import get_db
from app.models.user import User, UserSource
from app.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from app.services.ad_service import ActiveDirectoryService

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    settings = get_settings()
    user = db.query(User).filter(User.username == payload.username).first()

    if settings.ad_enabled:
        ad_service = ActiveDirectoryService(settings)
        result = ad_service.authenticate(payload.username, payload.password)
        if not result.success:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=result.error or "Login fehlgeschlagen")

        if user is None:
            user = User(
                username=payload.username,
                display_name=result.display_name,
                email=result.email,
                source=UserSource.ACTIVE_DIRECTORY,
            )
            db.add(user)
        else:
            user.display_name = result.display_name or user.display_name
            user.email = result.email or user.email
    else:
        if user is None or user.hashed_password is None or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Ungueltige Anmeldedaten")

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
