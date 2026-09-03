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
  RestoreProxyHostConfig,
  RestoreProxyHostWrite,
  RestoreRun,
  TriggerRestorePayload,
  CopyFileRestoreSelectionPayload,
  EmailConfig,
  EmailConfigWritePayload,
  FileEntry,
  FileRestoreRun,
  TriggerFileRestorePayload,
  SnapMirrorCheckGroup,
  SnapMirrorCheckResult,
  VmBackupRun,
  VmRecreateRun,
  VmWithBackups,
  RetentionType,
  Schedule,
  ScheduleType,
  SnapMirrorLabel,
  SnapMirrorRelationship,
  Alert,
  AlertConfig,
  AlertConfigWritePayload,
  SchedulerConfig,
  SchedulerConfigWritePayload,
  StorageAccess,
  SvmPeerCreate,
  UpcomingJob,
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

export function useUpcomingJobs(hours = 24) {
  return useQuery({
    queryKey: ["upcoming-jobs", hours],
    queryFn: async () => (await apiClient.get<UpcomingJob[]>("/jobs/upcoming", { params: { hours } })).data,
  });
}

// Fester Kalendertag-Bereich statt "ab jetzt vorausschauend" -- fuer die
// Backup-Kalenderansicht (Backup > Kalender), die auch in vergangene oder
// weiter entfernte Monate blaettern koennen muss. startDate/endDate im
// Format "YYYY-MM-DD".
export function useJobsCalendar(startDate: string, endDate: string) {
  return useQuery({
    queryKey: ["upcoming-jobs", "range", startDate, endDate],
    queryFn: async () =>
      (await apiClient.get<UpcomingJob[]>("/jobs/upcoming", { params: { start_date: startDate, end_date: endDate } })).data,
  });
}

export function useBackupsForObject(
  scope: BackupScope,
  name: string | undefined,
  clusterId: string | undefined | null,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["backups", scope, name, clusterId],
    queryFn: async () =>
      (await apiClient.get<BackupSnapshot[]>("/jobs/backups", { params: { scope, name, cluster_id: clusterId } })).data,
    enabled: enabled && !!name,
  });
}

export function useDeleteBackupSnapshot(scope: BackupScope, name: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (snapshotId: string) => {
      await apiClient.delete(`/jobs/backups/${snapshotId}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backups", scope, name] }),
  });
}

export function useDetachVmFromBackupSnapshot(scope: BackupScope, name: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ snapshotId, vmName }: { snapshotId: string; vmName: string }) => {
      await apiClient.post(`/jobs/backups/${snapshotId}/detach-vm`, null, { params: { vm_name: vmName } });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backups", scope, name] }),
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

export function useJobRun(id: string | undefined, poll: boolean) {
  return useQuery({
    queryKey: ["job-run", id],
    queryFn: async () => (await apiClient.get<BackupJobRun>(`/jobs/runs/${id}`)).data,
    enabled: !!id,
    refetchInterval: (query) => (poll && query.state.data?.status === "running" ? 3000 : false),
  });
}

export function useTriggerJobRun() {
  const queryClient = useQueryClient();
  return useMutation({
    // resourceGroupId (optional, einzeln ODER als Liste): beschraenkt den
    // Lauf auf genau diese Resource Group(s) statt (Default, weggelassen)
    // alle mit der Policy verknuepften. Einzeln genutzt vom "Jetzt
    // nachholen"-Button beim backup_missed-Alarm (siehe AlertsPage.tsx),
    // als Liste vom Auswahldialog bei "Jetzt ausfuehren" auf einer Policy
    // mit mehreren verknuepften Protection Groups (siehe
    // ResourceGroupPickerModal/runPolicy.ts). paramsSerializer indexes:null
    // sendet ein Array als wiederholte gleichnamige Query-Parameter
    // (?resource_group_id=a&resource_group_id=b) statt Axios' Default mit
    // eckigen Klammern ([]) -- FastAPIs list[str]-Query-Param erkennt nur
    // die wiederholte Form, keine Klammern.
    mutationFn: async ({ jobId, resourceGroupId }: { jobId: string; resourceGroupId?: string | string[] }) =>
      (
        await apiClient.post<BackupJobRun>(`/jobs/${jobId}/run`, null, {
          params: { resource_group_id: resourceGroupId },
          paramsSerializer: { indexes: null },
        })
      ).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["job-runs"] }),
  });
}

export interface BackupPolicyWritePayload {
  name: string;
  app_consistent: boolean;
  snapmirror_update: boolean;
  snapmirror_label_id?: string | null;
  retention_type: RetentionType;
  retention_value: number;
  snapshot_locking_enabled: boolean;
  snapshot_locking_days?: number | null;
  email_alert_on_failure: boolean;
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

export interface ResourceGroupPolicyLinkWritePayload {
  policy_id: string;
  schedule_id?: string | null;
}

export interface ResourceGroupWritePayload {
  name: string;
  scope: BackupScope;
  members: string[];
  policy_links: ResourceGroupPolicyLinkWritePayload[];
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

export function useCheckSnapMirror() {
  return useMutation({
    mutationFn: async (groups: SnapMirrorCheckGroup[]) =>
      (await apiClient.post<SnapMirrorCheckResult[]>("/resource-groups/check-snapmirror", { groups })).data,
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

export function useRestoreInitiator(enabled: boolean) {
  return useQuery({
    queryKey: ["restore-initiator"],
    queryFn: async () => (await apiClient.get<RestoreInitiatorInfo>("/restore-infra/initiator")).data,
    enabled,
  });
}

export function useRestoreProxyHost(enabled: boolean) {
  return useQuery({
    queryKey: ["restore-proxy-host"],
    queryFn: async () => (await apiClient.get<RestoreProxyHostConfig>("/restore-infra/proxy-host")).data,
    enabled,
  });
}

export function useSaveRestoreProxyHost() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: RestoreProxyHostWrite) =>
      (await apiClient.put<RestoreProxyHostConfig>("/restore-infra/proxy-host", payload)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["restore-proxy-host"] });
      queryClient.invalidateQueries({ queryKey: ["restore-initiator"] });
    },
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

// --- Restore-Ausführung --------------------------------------------------

export function useVmsWithBackups() {
  return useQuery({
    queryKey: ["restore-vms"],
    queryFn: async () => (await apiClient.get<VmWithBackups[]>("/restore/vms")).data,
  });
}

export function useTriggerRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: TriggerRestorePayload) => (await apiClient.post<RestoreRun>("/restore/runs", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore-runs"] }),
  });
}

export function useRestoreRun(id: string | undefined, poll: boolean) {
  return useQuery({
    queryKey: ["restore-run", id],
    queryFn: async () => (await apiClient.get<RestoreRun>(`/restore/runs/${id}`)).data,
    enabled: !!id,
    refetchInterval: (query) => (poll && query.state.data?.status === "running" ? 3000 : false),
  });
}

export function useVmBackupRuns(vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-backup-runs", vmName],
    queryFn: async () => (await apiClient.get<VmBackupRun[]>(`/restore/vms/${vmName}/backup-runs`)).data,
    enabled: enabled && !!vmName,
  });
}

export interface RecreateVmPayload {
  run_id: string;
  new_vm_name?: string;
  disconnect_network?: boolean;
  destination_csv_name?: string;
}

export function useRecreateVm(vmName: string | undefined) {
  return useMutation({
    mutationFn: async (payload: RecreateVmPayload) =>
      (await apiClient.post<VmRecreateRun>(`/restore/vms/${vmName}/recreate`, payload)).data,
  });
}

export function useVmRecreateRun(id: string | undefined, poll: boolean) {
  return useQuery({
    queryKey: ["vm-recreate-run", id],
    queryFn: async () => (await apiClient.get<VmRecreateRun>(`/restore/vm-recreate-runs/${id}`)).data,
    enabled: !!id,
    refetchInterval: (query) => (poll && query.state.data?.status === "running" ? 3000 : false),
  });
}

export function useRestoreRuns() {
  return useQuery({
    queryKey: ["restore-runs"],
    queryFn: async () => (await apiClient.get<RestoreRun[]>("/restore/runs")).data,
    refetchInterval: 15_000,
  });
}

export function useCleanupRestoreRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<RestoreRun>(`/restore/runs/${id}/cleanup`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["restore-runs"] }),
  });
}

// --- Datei-basierter Restore: VHDX auf dem Restore-Proxy-Host mounten und
// durchsuchen, statt die ganze Platte auf eine CSV zu kopieren (siehe
// backend/app/api/routes/file_restore.py). Gleiche Hook-Muster wie beim
// normalen Restore oben (Trigger-Mutation + Poll-Query + Cleanup-Mutation),
// zusaetzlich eine Browse-Query fuer den Datei-Browser im Wizard.

export function useTriggerFileRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: TriggerFileRestorePayload) =>
      (await apiClient.post<FileRestoreRun>("/file-restore/runs", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["file-restore-runs"] }),
  });
}

export function useFileRestoreRun(id: string | undefined, poll: boolean) {
  return useQuery({
    queryKey: ["file-restore-run", id],
    queryFn: async () => (await apiClient.get<FileRestoreRun>(`/file-restore/runs/${id}`)).data,
    enabled: !!id,
    refetchInterval: (query) => (poll && query.state.data?.status === "running" ? 3000 : false),
  });
}

export function useFileRestoreRuns() {
  return useQuery({
    queryKey: ["file-restore-runs"],
    queryFn: async () => (await apiClient.get<FileRestoreRun[]>("/file-restore/runs")).data,
    refetchInterval: 15_000,
  });
}

export function useBrowseFileRestore(runId: string | undefined, path: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["file-restore-browse", runId, path],
    queryFn: async () =>
      (await apiClient.get<FileEntry[]>(`/file-restore/runs/${runId}/browse`, { params: path ? { path } : {} })).data,
    enabled: enabled && !!runId,
  });
}

export function useCopyFileRestoreSelection(runId: string | undefined) {
  return useMutation({
    mutationFn: async (payload: CopyFileRestoreSelectionPayload) =>
      (await apiClient.post(`/file-restore/runs/${runId}/copy`, payload)).data,
  });
}

export function useCleanupFileRestoreRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<FileRestoreRun>(`/file-restore/runs/${id}/cleanup`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["file-restore-runs"] }),
  });
}

export function useEmailConfig() {
  return useQuery({
    queryKey: ["email-config"],
    queryFn: async () => (await apiClient.get<EmailConfig>("/email-config")).data,
  });
}

export function useUpdateEmailConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: EmailConfigWritePayload) => (await apiClient.put<EmailConfig>("/email-config", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["email-config"] }),
  });
}

export function useSendTestEmail() {
  return useMutation({
    mutationFn: async (recipient: string) => (await apiClient.post("/email-config/test", { recipient })).data,
  });
}

export function useAlerts() {
  return useQuery({
    queryKey: ["alerts"],
    queryFn: async () => (await apiClient.get<Alert[]>("/alerts")).data,
    refetchInterval: 60_000,
  });
}

export function useAlertConfig() {
  return useQuery({
    queryKey: ["alert-config"],
    queryFn: async () => (await apiClient.get<AlertConfig>("/alerts/config")).data,
  });
}

export function useUpdateAlertConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AlertConfigWritePayload) => (await apiClient.put<AlertConfig>("/alerts/config", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alert-config"] }),
  });
}

// Sofort-Check nach einer Aktion auf der Alarme-Seite (z.B. Volume
// vergrößert) -- Discovery + Alert-Check laufen serverseitig synchron,
// dauert dadurch spuerbar (mehrere Sekunden je Cluster), daher fire-and-
// forget mit Ladeindikator statt den Nutzer zu blockieren.
export function useRecheckAlerts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post("/alerts/recheck")).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

// Quittiert den virtuellen "Backup fehlgeschlagen"-Alarm eines einzelnen
// Laufs (siehe Backend alerts.py) -- fuer eine selten laufende oder
// inzwischen deaktivierte/geloeschte Policy, deren Alarm sich sonst nie
// durch einen spaeteren erfolgreichen Lauf von selbst aufloesen wuerde.
export function useDismissBackupFailedAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (runId: string) => (await apiClient.post(`/alerts/backup-runs/${runId}/dismiss`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

// Fuer echte, persistierte Alert-Zeilen (Kapazitaet/Cluster/SnapMirror/
// backup_missed) -- anders als bei den virtuellen 'Backup fehlgeschlagen'-
// Alarmen oben gibt es hier ein echtes Alert.id.
export function useDismissAlert() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (alertId: string) => (await apiClient.post(`/alerts/${alertId}/dismiss`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["alerts"] }),
  });
}

export function useSchedulerConfig() {
  return useQuery({
    queryKey: ["scheduler-config"],
    queryFn: async () => (await apiClient.get<SchedulerConfig>("/scheduler-config")).data,
  });
}

export function useUpdateSchedulerConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: SchedulerConfigWritePayload) =>
      (await apiClient.put<SchedulerConfig>("/scheduler-config", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["scheduler-config"] }),
  });
}

// Globaler Sicherheits-Schalter fuer alle Storage-Aktionen (Settings >
// Storage) -- siehe app.models.storage_access.StorageAccessConfig. Lesbar
// mit STORAGE_VIEW (jeder, der die Storage-Seite sieht, braucht den Wert
// um Aktions-Buttons auszugrauen), aenderbar nur mit SETTINGS_MANAGE.
export function useStorageAccess() {
  return useQuery({
    queryKey: ["storage-access"],
    queryFn: async () => (await apiClient.get<StorageAccess>("/storage-access")).data,
  });
}

export function useUpdateStorageAccess() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: StorageAccess) => (await apiClient.put<StorageAccess>("/storage-access", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["storage-access"] }),
  });
}
