import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { VmDeleteInfo, VmDeleteRun } from "@/api/types";

// VM loeschen (backend app.api.routes.vm_delete).

export function useVmDeleteInfo(clusterId: string | null | undefined, vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-delete-info", clusterId, vmName],
    queryFn: async () => (await apiClient.get<VmDeleteInfo>(`/vm-delete/${clusterId}/${encodeURIComponent(vmName!)}`)).data,
    enabled: enabled && !!clusterId && !!vmName,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useStartVmDelete() {
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; vm_name: string; confirm_name: string; delete_files: boolean; turn_off: boolean }) =>
      (await apiClient.post<VmDeleteRun>("/vm-delete", payload)).data,
  });
}

export function useVmDeleteRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["vm-delete-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<VmDeleteRun>(`/vm-delete/runs/${id}`)).data;
      if (run.status !== "running") {
        for (const key of ["vms", "csvs", "smb-shares", "resource-groups", "alerts"]) queryClient.invalidateQueries({ queryKey: [key] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}
