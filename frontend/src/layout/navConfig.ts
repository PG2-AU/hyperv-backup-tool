import {
  IconActivity,
  IconDatabase,
  IconDatabaseImport,
  IconLayoutDashboard,
  IconServerCog,
  IconSettings,
  IconStack2,
} from "@tabler/icons-react";
import type { Icon } from "@tabler/icons-react";

export interface NavChild {
  label: string;
  path: string;
  searchContext?: string;
  // Ohne Wert immer sichtbar -- gesetzt, wenn die zugehoerige Seite ohne
  // diese Berechtigung buchstaeblich nichts anzuzeigen/zu tun hat (siehe
  // AppShellLayout.tsx fuer die Filterung). Nur auf die Menuepunkte
  // gesetzt, deren Backend-Endpunkte tatsaechlich mehr als *_VIEW
  // verlangen -- nicht pauschal auf alles.
  requiredPermission?: string;
}

export interface NavItem {
  label: string;
  icon: Icon;
  path?: string;
  searchContext?: string;
  children?: NavChild[];
  requiredPermission?: string;
}

export const NAV_ITEMS: NavItem[] = [
  {
    label: "Dashboard",
    icon: IconLayoutDashboard,
    path: "/",
  },
  {
    label: "Monitoring",
    icon: IconActivity,
    searchContext: "alerts",
    children: [
      { label: "Alarms", path: "/alerts", searchContext: "alerts" },
      { label: "System Log", path: "/logs", searchContext: "logs" },
    ],
  },
  {
    label: "Inventory",
    icon: IconServerCog,
    searchContext: "vms",
    children: [
      { label: "Virtuelle Maschinen", path: "/vms?tab=vms" },
      { label: "Cluster Shared Volumes", path: "/vms?tab=csv" },
      { label: "SMB3-Freigaben", path: "/vms?tab=smb" },
    ],
  },
  {
    label: "Backup",
    icon: IconStack2,
    searchContext: "jobs",
    children: [
      { label: "Policies", path: "/jobs?tab=policies" },
      { label: "Protection Groups", path: "/jobs?tab=protection-groups", searchContext: "resource-groups" },
      { label: "Zeitpläne", path: "/jobs?tab=schedules" },
      { label: "Kalender", path: "/jobs?tab=calendar" },
      { label: "Job-Verlauf", path: "/jobs?tab=runs" },
    ],
  },
  {
    label: "Restore",
    icon: IconDatabaseImport,
    searchContext: "restore",
    // Selbst das reine Auflisten vergangener Restores verlangt
    // RESTORE_RUN (restore.py::list_runs), keine eigene Ansichts-
    // Berechtigung -- ein Viewer hat hier buchstaeblich nichts zu sehen,
    // daher der ganze Menuepunkt statt einzelner Buttons ausgeblendet.
    requiredPermission: "restore:run",
    children: [
      { label: "Wiederherstellen", path: "/restore?tab=overview" },
      { label: "Setup", path: "/restore?tab=setup" },
    ],
  },
  {
    label: "Storage",
    icon: IconDatabase,
    searchContext: "storage",
    children: [
      { label: "Systeme", path: "/storage?tab=clusters", searchContext: "netapp-clusters" },
      { label: "Nodes", path: "/storage?tab=platforms" },
      { label: "Aggregate", path: "/storage?tab=aggregates" },
      { label: "Storage Virtual Machines", path: "/storage?tab=svms" },
      { label: "Volumes", path: "/storage?tab=volumes" },
      { label: "LUNs", path: "/storage?tab=luns" },
      { label: "CIFS-Freigaben", path: "/storage?tab=cifs-shares" },
      { label: "IGroups", path: "/storage?tab=igroups" },
      { label: "Cluster Peer", path: "/storage?tab=cluster-peers" },
      { label: "SVM Peer", path: "/storage?tab=svm-peers" },
      { label: "SnapMirror-Beziehungen", path: "/storage?tab=snapmirror" },
      { label: "SnapMirror-Policies", path: "/storage?tab=snapmirror-policies" },
      { label: "Schedules", path: "/storage?tab=schedules" },
      { label: "MetroCluster-Status", path: "/storage?tab=metrocluster" },
    ],
  },
  {
    label: "Settings",
    icon: IconSettings,
    searchContext: "settings",
    // Kein requiredPermission auf dem Elternpunkt selbst -- "Ansicht"
    // (rein lokal, kein Backend-Call) und "Hyper-V-Hosts"/"SnapMirror-
    // Labels" (eigene Endpunkte nur *_VIEW) bleiben fuer alle drei
    // Rollen sichtbar, auch wenn die meisten anderen Kinder hier fuer
    // Operator/Viewer per Kind-Filter verschwinden (siehe
    // AppShellLayout.tsx).
    children: [
      { label: "Benutzer & Rollen", path: "/settings?tab=users", requiredPermission: "user:manage" },
      { label: "SnapMirror-Labels", path: "/settings?tab=snapmirror-labels", requiredPermission: "backup:view" },
      { label: "Active-Directory-Integration", path: "/settings?tab=ad", requiredPermission: "settings:manage" },
      { label: "Hyper-V-Hosts", path: "/settings?tab=hyperv", requiredPermission: "hyperv:view" },
      { label: "WinRM-Zertifikate", path: "/settings?tab=winrm-certs", requiredPermission: "settings:manage" },
      { label: "Kerberos", path: "/settings?tab=kerberos", requiredPermission: "settings:manage" },
      { label: "Storage", path: "/settings?tab=storage", requiredPermission: "settings:manage" },
      { label: "E-Mail", path: "/settings?tab=email", requiredPermission: "settings:manage" },
      { label: "Hintergrundjobs", path: "/settings?tab=scheduler", requiredPermission: "settings:manage" },
      { label: "Alarms", path: "/settings?tab=alerts", requiredPermission: "settings:manage" },
      { label: "Ansicht", path: "/settings?tab=display" },
      { label: "Updates (Git)", path: "/settings?tab=updates", requiredPermission: "settings:manage" },
    ],
  },
];

export function resolveSearchContext(pathname: string, search = ""): string | undefined {
  const fullPath = pathname + search;
  for (const item of NAV_ITEMS) {
    if (item.path === pathname) return item.searchContext;
    // Mehrere Kinder koennen dieselbe Basis-Pathname teilen (z.B. /jobs?tab=...);
    // zuerst exakt (inkl. Query) matchen, sonst auf Pathname-Basis zurueckfallen.
    const exactChild = item.children?.find((c) => c.path === fullPath);
    if (exactChild) return exactChild.searchContext ?? item.searchContext;
    const child = item.children?.find((c) => pathname === c.path.split("?")[0]);
    if (child) return child.searchContext ?? item.searchContext;
  }
  return undefined;
}
