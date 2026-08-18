from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.user import UserSource


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    email: str
    source: UserSource
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    password: str
    role_id: str | None = None


class UserPasswordUpdate(BaseModel):
    password: str
