import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { VmCreateIsoList, VmCreateOptions, VmCreatePayload, VmCreateRun } from "@/api/types";

// Neue VM per Assistent (backend app.api.routes.vm_create, Backlog #74).

const liveQuery = { staleTime: 60 * 1000, gcTime: 5 * 60 * 1000, retry: false, refetchOnWindowFocus: false } as const;

// Knoten (RAM, Switches, Standort) und Ablageorte.
export function useVmCreateOptions(clusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-create-options", clusterId],
    queryFn: async () => (await apiClient.get<VmCreateOptions>(`/vm-create/options/${clusterId}`)).data,
    enabled: enabled && !!clusterId,
    ...liveQuery,
  });
}

// *.iso auf CSVs und SMB3-Freigaben -- erst abgefragt, wenn ein Medium gewuenscht ist.
export function useVmCreateIsos(clusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-create-isos", clusterId],
    queryFn: async () => (await apiClient.get<VmCreateIsoList>(`/vm-create/isos/${clusterId}`)).data,
    enabled: enabled && !!clusterId,
    ...liveQuery,
  });
}

export function useStartVmCreate() {
  return useMutation({
    mutationFn: async (payload: VmCreatePayload) => (await apiClient.post<VmCreateRun>("/vm-create", payload)).data,
  });
}

export function useVmCreateRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["vm-create-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<VmCreateRun>(`/vm-create/runs/${id}`)).data;
      if (run.status !== "running") {
        for (const key of ["vms", "csvs", "smb-shares", "resource-groups"]) queryClient.invalidateQueries({ queryKey: [key] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}

export function useVmCreateRollback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<VmCreateRun>(`/vm-create/runs/${id}/rollback`)).data,
    onSuccess: (run) => queryClient.setQueryData(["vm-create-run", run.id], run),
  });
}

export function useVmCreateKeep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<VmCreateRun>(`/vm-create/runs/${id}/keep`)).data,
    onSuccess: (run) => queryClient.setQueryData(["vm-create-run", run.id], run),
  });
}
