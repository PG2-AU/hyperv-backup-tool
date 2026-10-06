import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// VM-Einstellungen aendern (backend app.api.routes.vm_settings, Backlog #81).

export interface VmSettingsAdapter {
  id: string;
  name: string;
  switch_name?: string | null;
  mac_address?: string | null;
  vlan_id?: number | null;
  vlan_mode: string;
}

export interface VmSettingsDisk {
  path: string;
  name: string;
  controller: string;
  size_bytes?: number | null;
  file_size_bytes?: number | null;
  vhd_type?: string | null;
  expand_blocked_reason?: string | null;
}

export interface VmSettingsInfo {
  cluster_id: string;
  vm_name: string;
  vm_id: string;
  state: string;
  generation: number;
  node: string;
  cpu_count: number;
  host_logical_cpus: number;
  memory_startup_bytes: number;
  dynamic_memory_enabled: boolean;
  memory_minimum_bytes: number;
  memory_maximum_bytes: number;
  checkpoint_count: number;
  adapters: VmSettingsAdapter[];
  switches: { name: string; type: string }[];
  disks: VmSettingsDisk[];
  new_disk_folder?: string | null;
  hardware_editable: boolean;
  adapters_addable: boolean;
  blocked_reasons: string[];
  warnings: string[];
}

export interface VmSettingsRequest {
  cluster_id: string;
  vm_name: string;
  cpu_count?: number | null;
  memory?: { startup_bytes: number; dynamic: boolean; minimum_bytes?: number | null; maximum_bytes?: number | null } | null;
  adapters: { id: string; switch_name: string | null; vlan_id: number | null }[];
  add_adapters: { switch_name: string; vlan_id: number | null }[];
  remove_adapter_ids: string[];
  expand_disks: { path: string; size_bytes: number }[];
  add_disks: { size_bytes: number; dynamic: boolean }[];
}

export interface VmSettingsRun {
  id: string;
  vm_name: string;
  node_name?: string | null;
  changes: string[];
  status: string;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: string; message?: string | null }[];
}

export function useVmSettingsInfo(clusterId: string | null | undefined, vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-settings-info", clusterId, vmName],
    queryFn: async () => (await apiClient.get<VmSettingsInfo>(`/vm-settings/${clusterId}/${encodeURIComponent(vmName!)}`)).data,
    enabled: enabled && !!clusterId && !!vmName,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useStartVmSettings() {
  return useMutation({
    mutationFn: async (payload: VmSettingsRequest) => (await apiClient.post<VmSettingsRun>("/vm-settings", payload)).data,
  });
}

export function useVmSettingsRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["vm-settings-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<VmSettingsRun>(`/vm-settings/runs/${id}`)).data;
      if (run.status !== "running") {
        for (const key of ["vms", "csvs", "smb-shares"]) queryClient.invalidateQueries({ queryKey: [key] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}
