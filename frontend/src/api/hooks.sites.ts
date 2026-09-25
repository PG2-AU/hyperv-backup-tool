import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { Site, SiteAssignments, SiteMismatchSummary, SiteWrite } from "@/api/types";

// Standort-Kennzeichnung (Settings > Standorte, siehe backend app.models.site).
// Jede Aenderung wirkt sich auf die Standort-Badges im Inventory und die
// Dashboard-Kachel aus -- daher nach jeder Mutation alles davon neu laden.
function invalidateSiteViews(queryClient: QueryClient) {
  for (const key of ["sites", "site-assignments", "site-mismatch-summary", "vms", "csvs"]) {
    queryClient.invalidateQueries({ queryKey: [key] });
  }
}

export function useSites() {
  return useQuery({
    queryKey: ["sites"],
    queryFn: async () => (await apiClient.get<Site[]>("/sites")).data,
  });
}

export function useSiteAssignments() {
  return useQuery({
    queryKey: ["site-assignments"],
    queryFn: async () => (await apiClient.get<SiteAssignments>("/sites/assignments")).data,
  });
}

export function useSiteMismatchSummary() {
  return useQuery({
    queryKey: ["site-mismatch-summary"],
    queryFn: async () => (await apiClient.get<SiteMismatchSummary>("/sites/mismatch-summary")).data,
  });
}

export function useSaveSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id?: string; payload: SiteWrite }) =>
      id ? (await apiClient.put<Site>(`/sites/${id}`, payload)).data : (await apiClient.post<Site>("/sites", payload)).data,
    onSuccess: () => invalidateSiteViews(queryClient),
  });
}

export function useDeleteSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/sites/${id}`);
    },
    onSuccess: () => invalidateSiteViews(queryClient),
  });
}

export function useSetNodeSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; node_name: string; site_id: string | null }) => {
      await apiClient.put("/sites/assignments/node", payload);
    },
    onSuccess: () => invalidateSiteViews(queryClient),
  });
}

export function useSetNetAppSite() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { netapp_cluster_id: string; site_id: string | null }) => {
      await apiClient.put("/sites/assignments/netapp", payload);
    },
    onSuccess: () => invalidateSiteViews(queryClient),
  });
}

export function useSetCsvSiteOverride() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { cluster_id: string; disk_serial_number: string; csv_name: string; site_id: string | null }) => {
      await apiClient.put("/sites/assignments/csv", payload);
    },
    onSuccess: () => invalidateSiteViews(queryClient),
  });
}
