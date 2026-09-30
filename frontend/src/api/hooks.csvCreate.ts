import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { CsvCreateHyperVOptions, CsvCreateNetAppOptions, CsvCreatePayload, CsvCreateRun } from "@/api/types";

// Neue CSV per Assistent (backend app.api.routes.csv_create, Backlog #68).

const liveQuery = { staleTime: 60 * 1000, gcTime: 5 * 60 * 1000, retry: false, refetchOnWindowFocus: false } as const;

// Knoten + Initiatoren (WinRM auf jedem Knoten) und vorhandene CSV-Namen.
export function useCsvCreateHyperVOptions(clusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["csv-create-hyperv", clusterId],
    queryFn: async () => (await apiClient.get<CsvCreateHyperVOptions>(`/csv-create/hyperv/${clusterId}`)).data,
    enabled: enabled && !!clusterId,
    ...liveQuery,
  });
}

// SVMs, Aggregate (mit freiem Platz) und igroups inkl. Initiatoren, live.
export function useCsvCreateNetAppOptions(netappClusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["csv-create-netapp", netappClusterId],
    queryFn: async () => (await apiClient.get<CsvCreateNetAppOptions>(`/csv-create/netapp/${netappClusterId}`)).data,
    enabled: enabled && !!netappClusterId,
    ...liveQuery,
  });
}

export function useStartCsvCreate() {
  return useMutation({
    mutationFn: async (payload: CsvCreatePayload) => (await apiClient.post<CsvCreateRun>("/csv-create", payload)).data,
  });
}

export function useCsvCreateRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["csv-create-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<CsvCreateRun>(`/csv-create/runs/${id}`)).data;
      if (run.status !== "running") {
        for (const key of ["csvs", "vms", "volumes", "luns", "lun-maps", "resource-groups"])
          queryClient.invalidateQueries({ queryKey: [key] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}

export function useCsvCreateRollback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<CsvCreateRun>(`/csv-create/runs/${id}/rollback`)).data,
    onSuccess: (run) => queryClient.setQueryData(["csv-create-run", run.id], run),
  });
}

export function useCsvCreateKeep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<CsvCreateRun>(`/csv-create/runs/${id}/keep`)).data,
    onSuccess: (run) => queryClient.setQueryData(["csv-create-run", run.id], run),
  });
}
