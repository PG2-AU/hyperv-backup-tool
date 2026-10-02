import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// VMs starten/herunterfahren (backend app.api.routes.vm_power).

export type VmPowerActionName = "start" | "shutdown" | "turn_off";

export interface VmPowerAction {
  id: string;
  cluster_id: string;
  vm_name: string;
  action: VmPowerActionName;
  status: "running" | "succeeded" | "failed";
  state_after?: string | null;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
}

export const VM_POWER_LABEL: Record<VmPowerActionName, string> = {
  start: "Starten",
  shutdown: "Herunterfahren",
  turn_off: "Ausschalten",
};

// Laufende und kuerzlich beendete Aktionen -- nur solange gepollt, wie eine laeuft.
export function useVmPowerActions(enabled: boolean) {
  return useQuery({
    queryKey: ["vm-power"],
    queryFn: async () => (await apiClient.get<VmPowerAction[]>("/vm-power")).data,
    enabled,
    refetchInterval: (query) => (query.state.data?.some((a) => a.status === "running") ? 2000 : false),
  });
}

export function useVmPower() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; vm_name: string; action: VmPowerActionName }) =>
      (await apiClient.post<VmPowerAction>("/vm-power", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vm-power"] }),
  });
}
