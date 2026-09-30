import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type {
  SmbCreateHyperVOptions,
  SmbCreateNetAppOptions,
  SmbCreatePayload,
  SmbCreateRun,
  SmbDeleteInfo,
  SmbDeleteRun,
} from "@/api/types";

// SMB3-Freigabe anlegen/loeschen (backend app.api.routes.smb_create/smb_delete).

const liveQuery = { staleTime: 60 * 1000, gcTime: 5 * 60 * 1000, retry: false, refetchOnWindowFocus: false } as const;

function invalidateInventory(queryClient: QueryClient) {
  for (const key of ["smb-shares", "vms", "volumes", "cifs-shares", "resource-groups"]) queryClient.invalidateQueries({ queryKey: [key] });
}

export function useSmbCreateHyperVOptions(clusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["smb-create-hyperv", clusterId],
    queryFn: async () => (await apiClient.get<SmbCreateHyperVOptions>(`/smb-create/hyperv/${clusterId}`)).data,
    enabled: enabled && !!clusterId,
    ...liveQuery,
  });
}

export function useSmbCreateNetAppOptions(netappClusterId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["smb-create-netapp", netappClusterId],
    queryFn: async () => (await apiClient.get<SmbCreateNetAppOptions>(`/smb-create/netapp/${netappClusterId}`)).data,
    enabled: enabled && !!netappClusterId,
    ...liveQuery,
  });
}

export function useStartSmbCreate() {
  return useMutation({
    mutationFn: async (payload: SmbCreatePayload) => (await apiClient.post<SmbCreateRun>("/smb-create", payload)).data,
  });
}

export function useSmbCreateRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["smb-create-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<SmbCreateRun>(`/smb-create/runs/${id}`)).data;
      if (run.status !== "running") invalidateInventory(queryClient);
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}

export function useSmbCreateRollback() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<SmbCreateRun>(`/smb-create/runs/${id}/rollback`)).data,
    onSuccess: (run) => queryClient.setQueryData(["smb-create-run", run.id], run),
  });
}

export function useSmbCreateKeep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<SmbCreateRun>(`/smb-create/runs/${id}/keep`)).data,
    onSuccess: (run) => queryClient.setQueryData(["smb-create-run", run.id], run),
  });
}

export function useSmbDeleteInfo(clusterId: string | null | undefined, server: string | undefined, share: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["smb-delete-info", clusterId, server, share],
    queryFn: async () =>
      (await apiClient.get<SmbDeleteInfo>(`/smb-delete/${clusterId}`, { params: { server, share } })).data,
    enabled: enabled && !!clusterId && !!server && !!share,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useStartSmbDelete() {
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; server: string; share: string; confirm_name: string; delete_volume: boolean }) =>
      (await apiClient.post<SmbDeleteRun>("/smb-delete", payload)).data,
  });
}

export function useSmbDeleteRun(id: string | undefined) {
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ["smb-delete-run", id],
    queryFn: async () => {
      const run = (await apiClient.get<SmbDeleteRun>(`/smb-delete/runs/${id}`)).data;
      if (run.status !== "running") invalidateInventory(queryClient);
      return run;
    },
    enabled: !!id,
    refetchInterval: (query) => (query.state.data?.status === "running" || !query.state.data ? 2000 : false),
  });
}
