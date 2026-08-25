import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type {
  BackupJobRun,
  BackupPolicy,
  BackupScope,
  Csv,
  DiscoveryStep,
  MetroClusterStatus,
  NetAppCluster,
  ResourceGroup,
  RetentionType,
  Schedule,
  ScheduleType,
  SnapMirrorLabel,
  SnapMirrorRelationship,
  SvmInfo,
  Vm,
} from "@/api/types";

export function useVms() {
  return useQuery({
    queryKey: ["vms"],
    queryFn: async () => (await apiClient.get<Vm[]>("/vms")).data,
  });
}

export function useCsvs() {
  return useQuery({
    queryKey: ["csvs"],
    queryFn: async () => (await apiClient.get<Csv[]>("/vms/csvs")).data,
  });
}

export function useSvms() {
  return useQuery({
    queryKey: ["svms"],
    queryFn: async () => (await apiClient.get<SvmInfo[]>("/storage/svms")).data,
  });
}

export function useSnapMirrorRelationships() {
  return useQuery({
    queryKey: ["snapmirror"],
    queryFn: async () => (await apiClient.get<SnapMirrorRelationship[]>("/storage/snapmirror-relationships")).data,
  });
}

export function useMetroClusterStatus() {
  return useQuery({
    queryKey: ["metrocluster"],
    queryFn: async () => (await apiClient.get<MetroClusterStatus>("/storage/metrocluster-status")).data,
  });
}

export function usePolicies() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: async () => (await apiClient.get<BackupPolicy[]>("/jobs")).data,
  });
}

export function useJobRuns() {
  return useQuery({
    queryKey: ["job-runs"],
    queryFn: async () => (await apiClient.get<BackupJobRun[]>("/jobs/runs")).data,
  });
}

export function useTriggerJobRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => (await apiClient.post<BackupJobRun>(`/jobs/${jobId}/run`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["job-runs"] }),
  });
}

export interface BackupPolicyWritePayload {
  name: string;
  schedule_id?: string | null;
  app_consistent: boolean;
  snapmirror_update: boolean;
  snapmirror_label_id?: string | null;
  retention_type: RetentionType;
  retention_value: number;
  snapshot_locking_enabled: boolean;
  snapshot_locking_days?: number | null;
}

export function useCreatePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: BackupPolicyWritePayload) => (await apiClient.post<BackupPolicy>("/jobs", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useUpdatePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: BackupPolicyWritePayload }) =>
      (await apiClient.put<BackupPolicy>(`/jobs/${id}`, payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useDeletePolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/jobs/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useSnapMirrorLabels() {
  return useQuery({
    queryKey: ["snapmirror-labels"],
    queryFn: async () => (await apiClient.get<SnapMirrorLabel[]>("/snapmirror-labels")).data,
  });
}

export function useCreateSnapMirrorLabel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string) => (await apiClient.post<SnapMirrorLabel>("/snapmirror-labels", { name })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["snapmirror-labels"] }),
  });
}

export function useUpdateSnapMirrorLabel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) =>
      (await apiClient.put<SnapMirrorLabel>(`/snapmirror-labels/${id}`, { name })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["snapmirror-labels"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteSnapMirrorLabel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/snapmirror-labels/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["snapmirror-labels"] }),
  });
}

export function useSchedules() {
  return useQuery({
    queryKey: ["schedules"],
    queryFn: async () => (await apiClient.get<Schedule[]>("/schedules")).data,
  });
}

export interface ScheduleWritePayload {
  name: string;
  schedule_type: ScheduleType;
  times: string[];
  weekday?: number | null;
  day_of_month?: number | null;
}

export function useCreateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ScheduleWritePayload) => (await apiClient.post<Schedule>("/schedules", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useUpdateSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: ScheduleWritePayload }) =>
      (await apiClient.put<Schedule>(`/schedules/${id}`, payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["schedules"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/schedules/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });
}

export function useResourceGroups() {
  return useQuery({
    queryKey: ["resource-groups"],
    queryFn: async () => (await apiClient.get<ResourceGroup[]>("/resource-groups")).data,
  });
}

export interface ResourceGroupWritePayload {
  name: string;
  scope: BackupScope;
  members: string[];
  policy_ids: string[];
}

export function useCreateResourceGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ResourceGroupWritePayload) => (await apiClient.post<ResourceGroup>("/resource-groups", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["resource-groups"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      queryClient.invalidateQueries({ queryKey: ["csvs"] });
    },
  });
}

export function useUpdateResourceGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, payload }: { id: string; payload: ResourceGroupWritePayload }) =>
      (await apiClient.put<ResourceGroup>(`/resource-groups/${id}`, payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["resource-groups"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      queryClient.invalidateQueries({ queryKey: ["csvs"] });
    },
  });
}

export function useDeleteResourceGroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/resource-groups/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["resource-groups"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      queryClient.invalidateQueries({ queryKey: ["csvs"] });
    },
  });
}

export function useNetAppClusters() {
  return useQuery({
    queryKey: ["netapp-clusters"],
    queryFn: async () => (await apiClient.get<NetAppCluster[]>("/netapp/clusters")).data,
  });
}

export interface NetAppClusterCreatePayload {
  name: string;
  management_lif: string;
  username: string;
  password: string;
  verify_ssl: boolean;
}

export function useCreateNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: NetAppClusterCreatePayload) =>
      (await apiClient.post<NetAppCluster>("/netapp/clusters", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useVerifyNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<NetAppCluster>(`/netapp/clusters/${id}/verify`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useEnrollNetAppClusterCertificate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<NetAppCluster>(`/netapp/clusters/${id}/enroll-certificate`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useDeleteNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/netapp/clusters/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useDiscoverNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<DiscoveryStep[]>(`/netapp/clusters/${id}/discover`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}
