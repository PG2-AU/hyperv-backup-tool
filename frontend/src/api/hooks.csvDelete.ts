import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { CsvDeleteInfo, CsvDeleteRun } from "@/api/types";

// CSV loeschen (backend app.api.routes.csv_delete).

export function useCsvDeleteInfo(clusterId: string | null | undefined, csvName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["csv-delete-info", clusterId, csvName],
    queryFn: async () => (await apiClient.get<CsvDeleteInfo>(`/csv-delete/${clusterId}/${encodeURIComponent(csvName!)}`)).data,
    enabled: enabled && !!clusterId && !!csvName,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useStartCsvDelete() {
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; csv_name: string; confirm_name: string; delete_lun: boolean; delete_volume: boolean }) =>
      (await apiClient.post<CsvDeleteRun>("/csv-delete", payload)).data,
  });
}

export function useCsvDeleteRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["csv-delete-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<CsvDeleteRun>(`/csv-delete/runs/${id}`)).data;
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
