import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { CsvResizeInfo, CsvResizeRun } from "@/api/types";

// CSV vergroessern (backend app.api.routes.csv_resize, Backlog #69).

// Live-Ist-Stand (WinRM + ONTAP) -- innerhalb eines geoeffneten Dialogs
// wiederverwendet, beim Schliessen verworfen (resetCsvResizeQueries).
export function useCsvResizeInfo(clusterId: string | null | undefined, csvName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["csv-resize-info", clusterId, csvName],
    queryFn: async () => (await apiClient.get<CsvResizeInfo>(`/csv-resize/${clusterId}/${encodeURIComponent(csvName!)}`)).data,
    enabled: enabled && !!clusterId && !!csvName,
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function resetCsvResizeQueries(queryClient: QueryClient) {
  queryClient.removeQueries({ queryKey: ["csv-resize-info"] });
}

export function useStartCsvResize() {
  return useMutation({
    mutationFn: async (payload: {
      cluster_id: string;
      csv_name: string;
      new_volume_size_bytes: number | null;
      new_lun_size_bytes: number | null;
    }) => (await apiClient.post<CsvResizeRun>("/csv-resize", payload)).data,
  });
}

export function useCsvResizeRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["csv-resize-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<CsvResizeRun>(`/csv-resize/runs/${id}`)).data;
      if (run.status !== "running") {
        for (const key of ["csvs", "vms", "volumes", "luns"]) queryClient.invalidateQueries({ queryKey: [key] });
      }
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}
