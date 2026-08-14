from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.rbac import Permission
from app.db.session import get_db
from app.models.role import Role
from app.models.user import User
from app.schemas.user import UserRead
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["users"])


class RoleRead(BaseModel):
    id: str
    name: str
    description: str
    permissions: list[str]
    is_system_role: bool


@router.get("/users", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db), user=Depends(require_permission(Permission.USER_MANAGE))) -> list[User]:
    return db.query(User).order_by(User.username).all()


@router.get("/roles", response_model=list[RoleRead])
def list_roles(db: Session = Depends(get_db), user=Depends(require_permission(Permission.ROLE_MANAGE))) -> list[Role]:
    return db.query(Role).order_by(Role.name).all()
