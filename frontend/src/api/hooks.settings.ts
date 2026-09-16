import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

export interface UserRead {
  id: string;
  username: string;
  display_name: string;
  email: string;
  source: "local" | "active_directory";
  is_active: boolean;
  created_at: string;
  last_login_at?: string | null;
}

export interface RoleRead {
  id: string;
  name: string;
  description: string;
  permissions: string[];
  is_system_role: boolean;
}

export interface PublicSettings {
  environment: string;
  ontap_cluster_mgmt_lif: string;
  ontap_verify_ssl: boolean;
  ontap_is_metrocluster: boolean;
  winrm_transport: string;
  winrm_use_https: boolean;
  winrm_port: number;
  winrm_ca_trust_path: string;
  git_repo_url: string;
  git_branch: string;
  auto_update_enabled: boolean;
  auto_update_interval_minutes: number;
}

export interface VersionInfo {
  commit?: string | null;
  commit_short?: string | null;
  commit_count?: number | null;
  last_deploy_at?: string | null;
  last_health_check_at?: string | null;
  last_discovery_at?: string | null;
  last_snapshot_reconciliation_at?: string | null;
  last_retention_cleanup_at?: string | null;
  last_file_restore_expiry_at?: string | null;
}

export interface CommitInfo {
  hash: string;
  short_hash: string;
  date: string;
  subject: string;
  body?: string | null;
}

export interface UserCreatePayload {
  username: string;
  display_name?: string;
  email?: string;
  password: string;
  role_id?: string | null;
}

export interface AdConfig {
  enabled: boolean;
  server: string;
  domain: string;
  base_dn: string;
  use_ssl: boolean;
  bind_user: string;
  bind_password_set: boolean;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface AdConfigWrite {
  enabled: boolean;
  server: string;
  domain: string;
  base_dn: string;
  use_ssl: boolean;
  bind_user: string;
  bind_password?: string | null;
}

export interface AdTestResult {
  success: boolean;
  message: string;
}

export interface ADUserSearchResult {
  username: string;
  display_name: string;
  email: string;
}

export interface ADUserAddPayload {
  username: string;
  display_name?: string;
  email?: string;
  role_id?: string | null;
}

export function useUsers() {
  return useQuery({
    queryKey: ["users"],
    queryFn: async () => (await apiClient.get<UserRead[]>("/users")).data,
  });
}

export function useCreateUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: UserCreatePayload) => (await apiClient.post<UserRead>("/users", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function useUpdateUserPassword() {
  return useMutation({
    mutationFn: async ({ userId, password }: { userId: string; password: string }) =>
      (await apiClient.put(`/users/${userId}/password`, { password })).data,
  });
}

export function useRoles() {
  return useQuery({
    queryKey: ["roles"],
    queryFn: async () => (await apiClient.get<RoleRead[]>("/roles")).data,
  });
}

// Settings > Active Directory -- GUI-verwaltete AD-Integration fuer die
// GUI-Anmeldung (nicht zu verwechseln mit Settings > Kerberos, das ist
// ausschliesslich fuer die WinRM-Verbindung zu Hyper-V).
export function useAdConfig() {
  return useQuery({
    queryKey: ["ad-config"],
    queryFn: async () => (await apiClient.get<AdConfig>("/ad-config")).data,
  });
}

export function useUpdateAdConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AdConfigWrite) => (await apiClient.put<AdConfig>("/ad-config", payload)).data,
    onSuccess: (data) => queryClient.setQueryData(["ad-config"], data),
  });
}

export function useTestAdConnection() {
  return useMutation({
    mutationFn: async (payload: { server: string; domain: string; base_dn: string; use_ssl: boolean; bind_user: string; bind_password?: string | null }) =>
      (await apiClient.post<AdTestResult>("/ad-config/test", payload)).data,
  });
}

export function useSearchAdUsers() {
  return useMutation({
    mutationFn: async (query: string) => (await apiClient.post<ADUserSearchResult[]>("/users/ad-search", { query })).data,
  });
}

export function useAddAdUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ADUserAddPayload) => (await apiClient.post<UserRead>("/users/ad-add", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
}

export function usePublicSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: async () => (await apiClient.get<PublicSettings>("/settings")).data,
  });
}

export function useVersion() {
  return useQuery({
    queryKey: ["version"],
    queryFn: async () => (await apiClient.get<VersionInfo>("/settings/version")).data,
    staleTime: 5 * 60 * 1000,
  });
}

export function useVersionHistory(limit = 100) {
  return useQuery({
    queryKey: ["version-history", limit],
    queryFn: async () => (await apiClient.get<CommitInfo[]>("/settings/version-history", { params: { limit } })).data,
    staleTime: 5 * 60 * 1000,
  });
}
