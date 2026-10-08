import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// Settings > Updates: Release-Paket hochladen und vom Host-Dienst einspielen
// lassen (backend app.api.routes.updates).

export interface StagedUpdatePackage {
  package: string;
  version: string;
  sha256: string;
  size_bytes: number;
  uploaded_by?: string | null;
  uploaded_at?: string | null;
}

export interface UpdateResult {
  status: "running" | "succeeded" | "failed";
  package?: string | null;
  version?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  log?: string | null;
}

// Auto-Update aus Git (scripts/hvnb-git-autoupdate, nur Entwicklungsumgebungen)
export interface AutoUpdateState {
  enabled: boolean;
  branch?: string | null;
  repo?: string | null;
  interval_minutes?: string | null;
  state?: "current" | "building" | "installing" | "waiting" | "failed" | "error" | "disabled" | null;
  message?: string | null;
  target_commit?: string | null;
  installed_commit?: string | null;
  last_check_at?: string | null;
  last_update_at?: string | null;
}

// Online-Update aus einer Registry (auf dem Server per hvnb-update --set-registry hinterlegt)
export interface RegistryState {
  repo: string;
  latest_version?: string | null;
  checked_at?: string | null;
  error?: string | null;
  auto_enabled: boolean;
}

export interface UpdateStatus {
  // release = Release-Image (Upload moeglich), git = bisherige Auslieferung
  delivery: "release" | "git";
  version?: string | null;
  agent_active: boolean;
  agent_last_seen_at?: string | null;
  staged?: StagedUpdatePackage | null;
  pending?: "requested" | "running" | null;
  pending_action?: "package" | "registry-install" | "registry-check" | "git-configure" | "git-remove" | null;
  last_result?: UpdateResult | null;
  // fehlt, wenn auf dem Server nicht eingerichtet
  auto_update?: AutoUpdateState | null;
  // fehlt, wenn keine Registry hinterlegt ist
  registry?: RegistryState | null;
  // der Server kann ein Auto-Update aus Git einrichten (Skript + git vorhanden)
  git_available: boolean;
  git_config_result?: { action?: string | null; status?: "ok" | "failed" | null; message?: string | null; at?: string | null } | null;
}

const KEY = ["update-status"];

export function useUpdateStatus(fast: boolean) {
  return useQuery({
    queryKey: KEY,
    queryFn: async () => (await apiClient.get<UpdateStatus>("/updates/status")).data,
    // Waehrend des Einspielens startet die App neu: weiter abfragen, Fehler nicht als endgueltig werten.
    refetchInterval: fast ? 3000 : 15000,
    retry: false,
  });
}

export function useUploadUpdatePackage(onProgress: (percent: number) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, sha256 }: { file: File; sha256: string }) =>
      (
        await apiClient.put<UpdateStatus>("/updates/package", file, {
          params: { filename: file.name, sha256 },
          headers: { "Content-Type": "application/octet-stream" },
          onUploadProgress: (event) => onProgress(event.total ? Math.round((event.loaded / event.total) * 100) : 0),
        })
      ).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useDiscardUpdatePackage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.delete<UpdateStatus>("/updates/package")).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useInstallUpdatePackage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<UpdateStatus>("/updates/install")).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useSetAutoUpdate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => (await apiClient.put<UpdateStatus>("/updates/auto-update", { enabled })).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useCheckRegistry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<UpdateStatus>("/updates/registry/check")).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useInstallFromRegistry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (version: string) => (await apiClient.post<UpdateStatus>("/updates/registry/install", { version })).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useSetRegistryAuto() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => (await apiClient.put<UpdateStatus>("/updates/registry/auto", { enabled })).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export interface GitConfigWrite {
  repo: string;
  branch: string;
  interval_minutes: number;
}

export function useSetGitConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: GitConfigWrite) => (await apiClient.put<UpdateStatus>("/updates/git-config", payload)).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useRemoveGitConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.delete<UpdateStatus>("/updates/git-config")).data,
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}
