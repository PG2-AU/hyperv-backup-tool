import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// Schutzklassen (backend app.api.routes.protection_classes, Backlog #86).

export type ProtectionObjectType = "vm" | "csv" | "smb_share";

export interface ProtectionClassWrite {
  name: string;
  rank: number;
  color: string;
  description?: string | null;
  max_backup_age_hours: number;
  // Aufbewahrung primaer / sekundaer in Tagen (sekundaer 0 = nicht verlangt)
  min_retention_days: number;
  secondary_retention_days: number;
  require_app_consistent: boolean;
}

export interface ProtectionClass extends ProtectionClassWrite {
  id: string;
  assigned_count: number;
}

export interface ProtectionObjectStatus {
  object_type: ProtectionObjectType;
  cluster_id?: string | null;
  cluster_name?: string | null;
  // VM-Name, CSV-Name bzw. 'server|share'
  name: string;
  display_name: string;
  class_id?: string | null;
  class_name?: string | null;
  class_color?: string | null;
  status: "ok" | "violation" | "unassigned";
  violations: string[];
  // Hinweise ohne Verstoss (z.B. Aufbewahrung noch im Aufbau)
  notes: string[];
  storage: { name: string; class_name?: string | null; class_color?: string | null }[];
  resource_group_names: string[];
  policy_names: string[];
  last_backup_at?: string | null;
  // bei Verstoss: Protection Groups, deren Sicherung die Klasse erfuellen wuerde
  suggested_groups: string[];
}

export interface GroupClassFit {
  class_id: string;
  class_name: string;
  class_color?: string | null;
  fits: boolean;
  reasons: string[];
}

export interface GroupFit {
  group_id: string;
  group_name: string;
  scope: string;
  paused: boolean;
  member_count: number;
  classes: GroupClassFit[];
}

export function useProtectionClasses() {
  return useQuery({
    queryKey: ["protection-classes"],
    queryFn: async () => (await apiClient.get<ProtectionClass[]>("/protection-classes")).data,
  });
}

/** Pruefergebnis je VM/CSV/SMB3-Freigabe -- eine Abfrage fuer alle Tabellen
 *  (Inventory-Spalten und Backup > Schutzklassen teilen sich den Cache). */
export function useProtectionStatus(enabled = true) {
  return useQuery({
    queryKey: ["protection-class-status"],
    queryFn: async () => (await apiClient.get<ProtectionObjectStatus[]>("/protection-classes/status")).data,
    staleTime: 30_000,
    refetchInterval: 120_000,
    enabled,
  });
}

/** Je Protection Group: welche Schutzklassen ihre Sicherung erfuellt (berechnet). */
export function useGroupFit(enabled = true) {
  return useQuery({
    queryKey: ["protection-group-fit"],
    queryFn: async () => (await apiClient.get<GroupFit[]>("/protection-classes/group-fit")).data,
    staleTime: 10_000,
    enabled,
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () => {
    for (const key of ["protection-classes", "protection-class-status", "protection-group-fit", "alerts"]) queryClient.invalidateQueries({ queryKey: [key] });
  };
}

export function useSaveProtectionClass() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async ({ id, payload }: { id?: string; payload: ProtectionClassWrite }) =>
      id
        ? (await apiClient.put<ProtectionClass>(`/protection-classes/${id}`, payload)).data
        : (await apiClient.post<ProtectionClass>("/protection-classes", payload)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteProtectionClass() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (id: string) => apiClient.delete(`/protection-classes/${id}`),
    onSuccess: invalidate,
  });
}

export function useAssignProtectionClass() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (payload: {
      class_id: string | null;
      objects: { object_type: ProtectionObjectType; cluster_id: string; name: string }[];
    }) => apiClient.put("/protection-classes/assignments", payload),
    onSuccess: invalidate,
  });
}
