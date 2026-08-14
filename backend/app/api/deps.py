from collections.abc import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.rbac import Permission
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.role import Role, RoleAssignment
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nicht authentifiziert",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error

    payload = decode_access_token(token)
    if payload is None or "sub" not in payload:
        raise credentials_error

    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise credentials_error

    return user


def get_user_permissions(user: User, db: Session) -> set[Permission]:
    assignments = db.query(RoleAssignment).filter(RoleAssignment.user_id == user.id).all()
    permissions: set[Permission] = set()
    for assignment in assignments:
        role = db.get(Role, assignment.role_id)
        if role:
            permissions.update(Permission(p) for p in role.permissions)
    return permissions


def require_permission(required: Permission):
    def _checker(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        permissions = get_user_permissions(user, db)
        if required not in permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Fehlende Berechtigung: {required.value}",
            )
        return user

    return _checker
