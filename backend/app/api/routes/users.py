from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.core.security import hash_password
from app.db.session import get_db
from app.models.role import Role, RoleAssignment
from app.models.user import User, UserSource
from app.schemas.user import UserCreate, UserPasswordUpdate, UserRead
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["users"])

MIN_PASSWORD_LENGTH = 8


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


@router.get("/roles", response_model=list[RoleRead])
def list_roles(db: Session = Depends(get_db), user=Depends(require_permission(Permission.ROLE_MANAGE))) -> list[Role]:
    return db.query(Role).order_by(Role.name).all()
