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
    # Ueber die gleichnamigen Properties auf app.models.user.User gelesen
    # (leitet aus der globalen RoleAssignment ab, nicht aus einer eigenen
    # Spalte) -- None, wenn dem Benutzer aktuell keine Rolle zugewiesen ist.
    role_id: str | None = None
    role_name: str | None = None


class UserCreate(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    password: str
    role_id: str | None = None


class UserPasswordUpdate(BaseModel):
    password: str


class UserRoleUpdate(BaseModel):
    # None entfernt die aktuelle Rollenzuweisung (Benutzer hat dann keine
    # Rolle mehr -- kann sich noch anmelden, aber ueberall abgewiesen
    # werden, siehe get_user_permissions).
    role_id: str | None = None


class ADUserSearchRequest(BaseModel):
    query: str


class ADUserSearchResult(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""


class ADUserAddRequest(BaseModel):
    username: str
    display_name: str = ""
    email: str = ""
    role_id: str | None = None
