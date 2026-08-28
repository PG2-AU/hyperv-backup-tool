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
  ad_enabled: boolean;
  ad_server: string;
  ad_domain: string;
  ad_base_dn: string;
  ontap_cluster_mgmt_lif: string;
  ontap_verify_ssl: boolean;
  ontap_is_metrocluster: boolean;
  winrm_transport: string;
  winrm_use_https: boolean;
  winrm_port: number;
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

export interface UserCreatePayload {
  username: string;
  display_name?: string;
  email?: string;
  password: string;
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
