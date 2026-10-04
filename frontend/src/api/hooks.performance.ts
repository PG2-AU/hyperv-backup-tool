import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// Performance (backend app.api.routes.performance, Backlog #80 Stufe 1).

export type PerfObjectType = "lun" | "volume";
export type PerfInterval = "1h" | "1d" | "1w" | "1m" | "1y";

export interface Rwt {
  read?: number | null;
  write?: number | null;
  total?: number | null;
}

export interface PerfValues {
  iops: Rwt;
  latency_ms: Rwt;
  throughput: Rwt; // Bytes/s
  timestamp?: string | null;
  status?: string | null;
}

export interface PerfRow {
  kind: "csv" | "smb_share" | "volume";
  name: string;
  hyperv_cluster_name?: string | null;
  netapp_cluster_id?: string | null;
  netapp_cluster_name?: string | null;
  svm_name?: string | null;
  object_type?: PerfObjectType | null;
  object_uuid?: string | null;
  object_name?: string | null;
  values?: PerfValues | null;
  note?: string | null;
}

export interface PerfOverview {
  rows: PerfRow[];
  errors: string[];
}

export interface PerfPoint {
  timestamp: string;
  iops_read?: number | null;
  iops_write?: number | null;
  iops_total?: number | null;
  latency_read_ms?: number | null;
  latency_write_ms?: number | null;
  latency_total_ms?: number | null;
  throughput_read?: number | null;
  throughput_write?: number | null;
  throughput_total?: number | null;
}

/** Aktuelle Werte -- alle 30 s neu (Backend puffert 20 s). */
export function usePerformanceOverview(enabled = true) {
  return useQuery({
    queryKey: ["performance-overview"],
    queryFn: async () => (await apiClient.get<PerfOverview>("/performance/overview")).data,
    refetchInterval: 30_000,
    enabled,
  });
}

export function usePerformanceHistory(
  target: { netappClusterId?: string | null; objectType?: PerfObjectType | null; uuid?: string | null },
  interval: PerfInterval,
) {
  const { netappClusterId, objectType, uuid } = target;
  return useQuery({
    queryKey: ["performance-history", netappClusterId, objectType, uuid, interval],
    queryFn: async () =>
      (
        await apiClient.get<PerfPoint[]>("/performance/history", {
          params: { netapp_cluster_id: netappClusterId, object_type: objectType, uuid, interval },
        })
      ).data,
    enabled: !!(netappClusterId && objectType && uuid),
    // kurze Zeitraeume laufen mit, lange aendern sich kaum
    refetchInterval: interval === "1h" ? 30_000 : interval === "1d" ? 300_000 : false,
  });
}

// --- VM-Seite (Stufe 2): aus der App-eigenen Sammlung (Storage QoS) ---

export type VmPerfRange = "1d" | "1w" | "1m" | "3m";

export interface VmPerfCsv {
  csv_name: string;
  iops: number;
  latency_ms: number;
  bandwidth: number;
}

export interface VmPerfRow {
  inventory_id?: string | null;
  cluster_id: string;
  cluster_name?: string | null;
  vm_uuid?: string | null;
  vm_name: string;
  host?: string | null;
  state?: string | null;
  sampled_at?: string | null;
  iops?: number | null;
  latency_ms?: number | null;
  bandwidth?: number | null;
  csvs: VmPerfCsv[];
  note?: string | null;
}

export interface VmPerfCollector {
  cluster_id: string;
  cluster_name: string;
  last_run_at?: string | null;
  error?: string | null;
  vm_rows: number;
}

export interface VmPerfOverview {
  interval_minutes: number;
  collectors: VmPerfCollector[];
  rows: VmPerfRow[];
}

export interface VmPerfPoint {
  timestamp: string;
  iops: number;
  latency_ms: number;
  bandwidth: number;
}

/** Letzte Messung je VM (aus der DB, keine WinRM-Abfrage) -- jede Minute neu. */
export function useVmPerformance(enabled = true) {
  return useQuery({
    queryKey: ["performance-vms"],
    queryFn: async () => (await apiClient.get<VmPerfOverview>("/performance/vms")).data,
    refetchInterval: 60_000,
    enabled,
  });
}

export function useVmPerformanceHistory(target: { clusterId?: string | null; vmUuid?: string | null }, range: VmPerfRange) {
  const { clusterId, vmUuid } = target;
  return useQuery({
    queryKey: ["performance-vm-history", clusterId, vmUuid, range],
    queryFn: async () =>
      (await apiClient.get<VmPerfPoint[]>("/performance/vms/history", { params: { cluster_id: clusterId, vm_uuid: vmUuid, range } })).data,
    enabled: !!(clusterId && vmUuid),
    refetchInterval: range === "1d" ? 60_000 : false,
  });
}
