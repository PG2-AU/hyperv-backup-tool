import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { VmMoveRun, VmMoveTargets, VmStorageTargets } from "@/api/types";

// VM verschieben (Stufe 1: Host-Move per Live-Migration), siehe backend
// app.api.routes.vm_moves.

export function useVmMoveTargets(clusterId: string | null | undefined, vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-move-targets", clusterId, vmName],
    queryFn: async () =>
      (await apiClient.get<VmMoveTargets>(`/vm-moves/targets/${clusterId}/${encodeURIComponent(vmName!)}`)).data,
    enabled: enabled && !!clusterId && !!vmName,
    // Knotenstatus/Owner sind live -- beim erneuten Oeffnen immer frisch.
    staleTime: 0,
    gcTime: 0,
    retry: false,
    // Kein erneutes Abfragen beim Fensterwechsel: kostet WinRM-Aufrufe je
    // Knoten und wuerde eine bereits getroffene Auswahl zuruecksetzen.
    refetchOnWindowFocus: false,
  });
}

export function useStartVmMove() {
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; vm_name: string; target_node: string }) =>
      (await apiClient.post<VmMoveRun>("/vm-moves", payload)).data,
  });
}

export function useVmMoveRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["vm-move-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<VmMoveRun>(`/vm-moves/${id}`)).data;
      if (run.status !== "running") {
        // Host/Standort-Anzeige im Inventory sofort nachziehen.
        queryClient.invalidateQueries({ queryKey: ["vms"] });
        queryClient.invalidateQueries({ queryKey: ["site-mismatch-summary"] });
        queryClient.invalidateQueries({ queryKey: ["csvs"] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}

export function useVmStorageTargets(clusterId: string | null | undefined, vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-storage-targets", clusterId, vmName],
    queryFn: async () =>
      (await apiClient.get<VmStorageTargets>(`/vm-moves/storage-targets/${clusterId}/${encodeURIComponent(vmName!)}`)).data,
    enabled: enabled && !!clusterId && !!vmName,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    // Kein erneutes Abfragen beim Fensterwechsel: kostet WinRM-Aufrufe je
    // Knoten und wuerde eine bereits getroffene Auswahl zuruecksetzen.
    refetchOnWindowFocus: false,
  });
}

export function useStartStorageMove() {
  return useMutation({
    mutationFn: async (payload: {
      cluster_id: string;
      vm_name: string;
      destination_csv_name: string;
      acknowledge_protection_change: boolean;
    }) => (await apiClient.post<VmMoveRun>("/vm-moves/storage", payload)).data,
  });
}

export function useCancelVmMove() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (runId: string) => (await apiClient.post<VmMoveRun>(`/vm-moves/${runId}/cancel`)).data,
    onSuccess: (run) => queryClient.invalidateQueries({ queryKey: ["vm-move-run", run.id] }),
  });
}
