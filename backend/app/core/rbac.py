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
    # Getrennt von STORAGE_MANAGE (Objekte *innerhalb* eines bereits
    # registrierten NetApp-Systems verwalten) -- diese Permission gilt
    # nur fuer das Hinzufuegen/Entfernen eines ganzen Systems selbst
    # (Nutzer-Vorgabe 2026-09-17: Operator soll Storage-Objekte
    # verwalten duerfen, aber keine Systeme an-/abbauen).
    STORAGE_CLUSTER_MANAGE = "storage:cluster_manage"

    # Hyper-V
    HYPERV_VIEW = "hyperv:view"
    HYPERV_MANAGE = "hyperv:manage"
    # Analog zu STORAGE_CLUSTER_MANAGE -- nur fuer das Hinzufuegen/
    # Entfernen eines ganzen Hyper-V-Clusters, nicht fuer das Verwalten
    # von VMs/Checkpoints/Discovery innerhalb eines bereits registrierten
    # Clusters (bleibt HYPERV_MANAGE).
    HYPERV_CLUSTER_MANAGE = "hyperv:cluster_manage"

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
    # Vormals "BackupOperator" (siehe app.db.init_db._rename_legacy_system_roles
    # fuer die Umbenennung bestehender Installationen) -- erweitert um
    # STORAGE_MANAGE/HYPERV_MANAGE (Nutzer-Vorgabe 2026-09-17: Operator
    # soll den Tagesbetrieb auf Storage-/Hyper-V-Objekten fuehren
    # duerfen), bewusst weiterhin OHNE STORAGE_CLUSTER_MANAGE/
    # HYPERV_CLUSTER_MANAGE (keine Systeme/Cluster an-/abbauen) und ohne
    # BACKUP_DELETE (hatte BackupOperator auch nie).
    "Operator": {
        Permission.BACKUP_VIEW,
        Permission.BACKUP_CREATE,
        Permission.BACKUP_RUN,
        Permission.RESTORE_RUN,
        Permission.STORAGE_VIEW,
        Permission.STORAGE_MANAGE,
        Permission.HYPERV_VIEW,
        Permission.HYPERV_MANAGE,
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
