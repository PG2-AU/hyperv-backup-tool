"""RBAC-Grundgeruest: Rollen, Permissions und Scopes (VM/CSV/LUN-basiert)."""

from enum import StrEnum


class Permission(StrEnum):
    # Backup-Jobs
    BACKUP_VIEW = "backup:view"
    BACKUP_CREATE = "backup:create"
    BACKUP_RUN = "backup:run"
    BACKUP_DELETE = "backup:delete"
    RESTORE_RUN = "restore:run"

    # Storage / SnapMirror
    STORAGE_VIEW = "storage:view"
    STORAGE_MANAGE = "storage:manage"

    # Hyper-V
    HYPERV_VIEW = "hyperv:view"
    HYPERV_MANAGE = "hyperv:manage"

    # System / Admin
    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"
    SETTINGS_MANAGE = "settings:manage"
    LOGS_VIEW = "logs:view"


# Vordefinierte Standardrollen. Zusaetzliche, individuell scopebare
# Rollen (z.B. "nur VM-Gruppe X") werden ueber RoleAssignment.scope
# in der Datenbank abgebildet, nicht hier statisch.
DEFAULT_ROLES: dict[str, set[Permission]] = {
    "Administrator": set(Permission),
    "BackupOperator": {
        Permission.BACKUP_VIEW,
        Permission.BACKUP_CREATE,
        Permission.BACKUP_RUN,
        Permission.RESTORE_RUN,
        Permission.STORAGE_VIEW,
        Permission.HYPERV_VIEW,
        Permission.LOGS_VIEW,
    },
    "Viewer": {
        Permission.BACKUP_VIEW,
        Permission.STORAGE_VIEW,
        Permission.HYPERV_VIEW,
        Permission.LOGS_VIEW,
    },
}


class ScopeType(StrEnum):
    """Granularitaet, auf die eine Rollenzuweisung eingeschraenkt werden kann."""

    GLOBAL = "global"
    HYPERV_HOST = "hyperv_host"
    CSV = "csv"
    VM = "vm"
    LUN = "lun"
    SVM = "svm"


def has_permission(user_permissions: set[Permission], required: Permission) -> bool:
    return required in user_permissions
