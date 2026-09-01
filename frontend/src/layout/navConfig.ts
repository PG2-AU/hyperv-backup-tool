import {
  IconAlertTriangle,
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
}

export interface NavItem {
  label: string;
  icon: Icon;
  path?: string;
  searchContext?: string;
  children?: NavChild[];
}

export const NAV_ITEMS: NavItem[] = [
  {
    label: "Dashboard",
    icon: IconLayoutDashboard,
    path: "/",
  },
  {
    label: "Alarms",
    icon: IconAlertTriangle,
    path: "/alerts",
    searchContext: "alerts",
  },
  {
    label: "Inventory",
    icon: IconServerCog,
    searchContext: "vms",
    children: [
      { label: "Virtuelle Maschinen", path: "/vms?tab=vms" },
      { label: "Cluster Shared Volumes", path: "/vms?tab=csv" },
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
      { label: "Job-Verlauf", path: "/jobs?tab=runs" },
    ],
  },
  {
    label: "Restore",
    icon: IconDatabaseImport,
    searchContext: "restore",
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
      { label: "Cluster", path: "/storage?tab=clusters", searchContext: "netapp-clusters" },
      { label: "Nodes", path: "/storage?tab=platforms" },
      { label: "Aggregate", path: "/storage?tab=aggregates" },
      { label: "Storage Virtual Machines", path: "/storage?tab=svms" },
      { label: "Volumes", path: "/storage?tab=volumes" },
      { label: "LUNs", path: "/storage?tab=luns" },
      { label: "IGroups", path: "/storage?tab=igroups" },
      { label: "Cluster Peer", path: "/storage?tab=cluster-peers" },
      { label: "SVM Peer", path: "/storage?tab=svm-peers" },
      { label: "SnapMirror-Beziehungen", path: "/storage?tab=snapmirror" },
      { label: "MetroCluster-Status", path: "/storage?tab=metrocluster" },
    ],
  },
  {
    label: "Settings",
    icon: IconSettings,
    searchContext: "settings",
    children: [
      { label: "Benutzer & Rollen", path: "/settings?tab=users" },
      { label: "SnapMirror-Labels", path: "/settings?tab=snapmirror-labels" },
      { label: "SnapMirror-Policies", path: "/settings?tab=netapp-snapmirror-policies" },
      { label: "Schedules", path: "/settings?tab=netapp-schedules" },
      { label: "Active-Directory-Integration", path: "/settings?tab=ad" },
      { label: "Hyper-V-Hosts", path: "/settings?tab=hyperv" },
      { label: "E-Mail", path: "/settings?tab=email" },
      { label: "Hintergrundjobs", path: "/settings?tab=scheduler" },
      { label: "Alarms", path: "/settings?tab=alerts" },
      { label: "Ansicht", path: "/settings?tab=display" },
      { label: "Updates (Git)", path: "/settings?tab=updates" },
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
