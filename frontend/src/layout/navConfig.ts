import {
  IconDatabase,
  IconLayoutDashboard,
  IconServer,
  IconServerCog,
  IconSettings,
  IconStack2,
} from "@tabler/icons-react";
import type { Icon } from "@tabler/icons-react";

export interface NavChild {
  label: string;
  path: string;
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
    label: "VMs & CSVs",
    icon: IconServerCog,
    searchContext: "vms",
    children: [
      { label: "Virtuelle Maschinen", path: "/vms" },
      { label: "Cluster Shared Volumes", path: "/vms?tab=csv" },
    ],
  },
  {
    label: "NetApp-Systeme",
    icon: IconServer,
    path: "/netapp-clusters",
    searchContext: "netapp-clusters",
  },
  {
    label: "Storage",
    icon: IconDatabase,
    searchContext: "storage",
    children: [
      { label: "Storage Virtual Machines", path: "/storage?tab=svms" },
      { label: "SnapMirror-Beziehungen", path: "/storage?tab=snapmirror" },
      { label: "MetroCluster-Status", path: "/storage?tab=metrocluster" },
    ],
  },
  {
    label: "Backup-Jobs",
    icon: IconStack2,
    searchContext: "jobs",
    children: [
      { label: "Policies", path: "/jobs?tab=policies" },
      { label: "Job-Verlauf", path: "/jobs?tab=runs" },
    ],
  },
  {
    label: "Einstellungen",
    icon: IconSettings,
    searchContext: "settings",
    children: [
      { label: "Benutzer & Rollen", path: "/settings?tab=users" },
      { label: "Zeitpläne", path: "/settings?tab=schedules" },
      { label: "SnapMirror-Labels", path: "/settings?tab=snapmirror-labels" },
      { label: "Active-Directory-Integration", path: "/settings?tab=ad" },
      { label: "NetApp-Verbindung", path: "/settings?tab=netapp" },
      { label: "Hyper-V-Hosts", path: "/settings?tab=hyperv" },
      { label: "Updates (Git)", path: "/settings?tab=updates" },
    ],
  },
];

export function resolveSearchContext(pathname: string): string | undefined {
  for (const item of NAV_ITEMS) {
    if (item.path === pathname) return item.searchContext;
    if (item.children?.some((c) => pathname === c.path.split("?")[0])) {
      return item.searchContext;
    }
  }
  return undefined;
}
