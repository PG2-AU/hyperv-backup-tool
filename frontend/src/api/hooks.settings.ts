import { useQuery } from "@tanstack/react-query";

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

export function useUsers() {
  return useQuery({
    queryKey: ["users"],
    queryFn: async () => (await apiClient.get<UserRead[]>("/users")).data,
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
