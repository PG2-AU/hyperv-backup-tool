import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type {
  BackupJobRun,
  BackupPolicy,
  BackupScope,
  BackupSnapshot,
  ClusterPeerCreate,
  Csv,
  DiscoveryStep,
  HyperVCluster,
  HyperVClusterCreate,
  IgroupCreate,
  MetroClusterStatus,
  NetAppAggregate,
  NetAppCluster,
  NetAppClusterPeer,
  NetAppIgroup,
  NetAppLun,
  NetAppNetworkInterface,
  NetAppPlatform,
  NetAppSchedule,
  NetAppSnapMirrorPolicy,
  NetAppSvm,
  NetAppSvmPeer,
  NetAppVolume,
  ResourceGroup,
  RestoreBroadcastDomain,
  RestoreCreateLifPayload,
  RestoreInfraConfig,
  RestoreInfraSetupPayload,
  RestoreInitiatorInfo,
  RestoreLifCandidate,
  RestoreRequirementsStatus,
  RetentionType,
  Schedule,
  ScheduleType,
  SnapMirrorLabel,
  SnapMirrorRelationship,
  SvmPeerCreate,
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
    queryFn: async () => (await apiClient.get<NetAppSvm[]>("/storage/svms")).data,
  });
}

export function useVolumes() {
  return useQuery({
    queryKey: ["volumes"],
    queryFn: async () => (await apiClient.get<NetAppVolume[]>("/storage/volumes")).data,
  });
}

export function useLuns() {
  return useQuery({
    queryKey: ["luns"],
    queryFn: async () => (await apiClient.get<NetAppLun[]>("/storage/luns")).data,
  });
}

export function useSnapmirrorPolicies() {
  return useQuery({
    queryKey: ["snapmirror-policies"],
    queryFn: async () => (await apiClient.get<NetAppSnapMirrorPolicy[]>("/storage/snapmirror-policies")).data,
  });
}

export function useNetAppSchedules() {
  return useQuery({
    queryKey: ["netapp-schedules"],
    queryFn: async () => (await apiClient.get<NetAppSchedule[]>("/storage/schedules")).data,
  });
}

export function useIgroups() {
  return useQuery({
    queryKey: ["igroups"],
    queryFn: async () => (await apiClient.get<NetAppIgroup[]>("/storage/igroups")).data,
  });
}

export function useClusterPeers() {
  return useQuery({
    queryKey: ["cluster-peers"],
    queryFn: async () => (await apiClient.get<NetAppClusterPeer[]>("/storage/cluster-peers")).data,
  });
}

export function useSvmPeers() {
  return useQuery({
    queryKey: ["svm-peers"],
    queryFn: async () => (await apiClient.get<NetAppSvmPeer[]>("/storage/svm-peers")).data,
  });
}

export function useSnapMirrorRelationships() {
  return useQuery({
    queryKey: ["snapmirror"],
    queryFn: async () => (await apiClient.get<SnapMirrorRelationship[]>("/storage/snapmirror-relationships")).data,
  });
}

export function useNetworkInterfaces() {
  return useQuery({
    queryKey: ["network-interfaces"],
    queryFn: async () => (await apiClient.get<NetAppNetworkInterface[]>("/storage/network-interfaces")).data,
  });
}

export function usePlatforms() {
  return useQuery({
    queryKey: ["platforms"],
    queryFn: async () => (await apiClient.get<NetAppPlatform[]>("/storage/platforms")).data,
  });
}

export function useAggregates() {
  return useQuery({
    queryKey: ["aggregates"],
    queryFn: async () => (await apiClient.get<NetAppAggregate[]>("/storage/aggregates")).data,
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

export function useBackupsForObject(scope: BackupScope, name: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["backups", scope, name],
    queryFn: async () => (await apiClient.get<BackupSnapshot[]>("/jobs/backups", { params: { scope, name } })).data,
    enabled: enabled && !!name,
  });
}

export function useRunningJobRuns(enabled: boolean) {
  return useQuery({
    queryKey: ["job-runs", "running"],
    queryFn: async () => (await apiClient.get<BackupJobRun[]>("/jobs/runs", { params: { status: "running" } })).data,
    enabled,
    refetchInterval: 4000,
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

export function useHyperVClusters() {
  return useQuery({
    queryKey: ["hyperv-clusters"],
    queryFn: async () => (await apiClient.get<HyperVCluster[]>("/hyperv/clusters")).data,
  });
}

export function useCreateHyperVCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: HyperVClusterCreate) => (await apiClient.post<HyperVCluster>("/hyperv/clusters", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hyperv-clusters"] }),
  });
}

export function useVerifyHyperVCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<HyperVCluster>(`/hyperv/clusters/${id}/verify`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hyperv-clusters"] }),
  });
}

export function useDeleteHyperVCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/hyperv/clusters/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["hyperv-clusters"] }),
  });
}

export function useDiscoverHyperVCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<DiscoveryStep[]>(`/hyperv/clusters/${id}/discover`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["hyperv-clusters"] });
      queryClient.invalidateQueries({ queryKey: ["vms"] });
      queryClient.invalidateQueries({ queryKey: ["csvs"] });
    },
  });
}

export const DISCOVERY_QUERY_KEYS = [
  "netapp-clusters",
  "svms",
  "volumes",
  "luns",
  "igroups",
  "cluster-peers",
  "svm-peers",
  "snapmirror",
  "network-interfaces",
  "platforms",
  "aggregates",
];

export function useDiscoverNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<DiscoveryStep[]>(`/netapp/clusters/${id}/discover`)).data,
    onSuccess: () => DISCOVERY_QUERY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  });
}

export function useCreateIgroup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, payload }: { clusterId: string; payload: IgroupCreate }) =>
      (await apiClient.post(`/netapp/clusters/${clusterId}/igroups`, payload)).data,
    onSuccess: () => DISCOVERY_QUERY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  });
}

export function useCreateClusterPeer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, payload }: { clusterId: string; payload: ClusterPeerCreate }) =>
      (await apiClient.post(`/netapp/clusters/${clusterId}/cluster-peers`, payload)).data,
    onSuccess: () => DISCOVERY_QUERY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  });
}

export function useCreateSvmPeer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, payload }: { clusterId: string; payload: SvmPeerCreate }) =>
      (await apiClient.post(`/netapp/clusters/${clusterId}/svm-peers`, payload)).data,
    onSuccess: () => DISCOVERY_QUERY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  });
}

// --- Restore-Setup-Wizard ---------------------------------------------

export function useRestoreRequirements() {
  return useQuery({
    queryKey: ["restore-requirements"],
    queryFn: async () => (await apiClient.get<RestoreRequirementsStatus>("/restore-infra/requirements")).data,
  });
}

export function useInstallRestoreRequirements() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<DiscoveryStep[]>("/restore-infra/requirements/install")).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore-requirements"] }),
  });
}

export function useRestoreInitiator(enabled: boolean) {
  return useQuery({
    queryKey: ["restore-initiator"],
    queryFn: async () => (await apiClient.get<RestoreInitiatorInfo>("/restore-infra/initiator")).data,
    enabled,
  });
}

export function useSvmLifCandidates(clusterId: string | undefined, svmName: string | undefined) {
  return useQuery({
    queryKey: ["restore-lif-candidates", clusterId, svmName],
    queryFn: async () =>
      (await apiClient.get<RestoreLifCandidate[]>(`/restore-infra/clusters/${clusterId}/svms/${svmName}/lifs`)).data,
    enabled: !!clusterId && !!svmName,
  });
}

export function useBroadcastDomains(clusterId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["restore-broadcast-domains", clusterId],
    queryFn: async () =>
      (await apiClient.get<RestoreBroadcastDomain[]>(`/restore-infra/clusters/${clusterId}/broadcast-domains`)).data,
    enabled: !!clusterId && enabled,
  });
}

export function useCreateRestoreLif() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, payload }: { clusterId: string; payload: RestoreCreateLifPayload }) =>
      (await apiClient.post<RestoreLifCandidate>(`/restore-infra/clusters/${clusterId}/lif`, payload)).data,
    onSuccess: (_data, vars) => queryClient.invalidateQueries({ queryKey: ["restore-lif-candidates", vars.clusterId] }),
  });
}

export function useSetupRestoreInfra() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ clusterId, payload }: { clusterId: string; payload: RestoreInfraSetupPayload }) =>
      (await apiClient.post<RestoreInfraConfig>(`/restore-infra/clusters/${clusterId}/setup`, payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore-infra-configs"] }),
  });
}

export function useRestoreInfraConfigs() {
  return useQuery({
    queryKey: ["restore-infra-configs"],
    queryFn: async () => (await apiClient.get<RestoreInfraConfig[]>("/restore-infra/configs")).data,
  });
}

export function useDeleteRestoreInfraConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/restore-infra/configs/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore-infra-configs"] }),
  });
}
