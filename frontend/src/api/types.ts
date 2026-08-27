export interface VhdInfo {
  name: string;
  size_bytes: number;
  csv_path: string;
  full_path: string;
}

export interface Vm {
  id: string;
  name: string;
  state: string;
  host: string;
  cluster?: string | null;
  csv_paths: string[];
  vhdx_size_bytes?: number | null;
  vhds: VhdInfo[];
  resource_group_names: string[];
  policy_names: string[];
  policy_ids: string[];
  protected: boolean;
}

export interface Csv {
  name: string;
  owner_node: string;
  state: string;
  volume_path: string;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  lun_name?: string | null;
  lun_capacity_bytes?: number | null;
  lun_used_bytes?: number | null;
  volume_name?: string | null;
  volume_capacity_bytes?: number | null;
  volume_used_bytes?: number | null;
  svm_name?: string | null;
  netapp_cluster_name?: string | null;
  resource_group_names: string[];
  policy_names: string[];
  policy_ids: string[];
  protected: boolean;
}

export type NetAppAuthMethod = "password" | "certificate";
export type NetAppClusterHealth = "unknown" | "healthy" | "degraded" | "unreachable";

export interface NetAppCluster {
  id: string;
  name: string;
  management_lif: string;
  username: string;
  auth_method: NetAppAuthMethod;
  verify_ssl: boolean;
  ontap_version?: string | null;
  ontap_cluster_name?: string | null;
  cluster_uuid?: string | null;
  health: NetAppClusterHealth;
  node_count: number;
  healthy_node_count: number;
  is_metrocluster: boolean;
  last_checked_at?: string | null;
  last_check_error?: string | null;
  created_at: string;
}

export type HyperVClusterHealth = "unknown" | "healthy" | "degraded" | "unreachable";

export interface HyperVCluster {
  id: string;
  name: string;
  management_address: string;
  username: string;
  use_https: boolean;
  hyperv_cluster_name?: string | null;
  health: HyperVClusterHealth;
  node_count: number;
  healthy_node_count: number;
  last_checked_at?: string | null;
  last_check_error?: string | null;
  created_at: string;
}

export interface HyperVClusterCreate {
  name: string;
  management_address: string;
  username: string;
  password: string;
  use_https: boolean;
}

export interface HyperVClusterCreationPlan {
  name: string;
  managementAddress: string;
  username: string;
  password: string;
  useHttps: boolean;
}

export interface DiscoveryStep {
  step: string;
  success: boolean;
  message: string;
  count?: number | null;
}

interface NetAppDiscoveredBase {
  id: string;
  cluster_id: string;
  cluster_name: string;
  uuid?: string | null;
  last_seen_at: string;
}

export interface NetAppSvm extends NetAppDiscoveredBase {
  name: string;
  state?: string | null;
  subtype?: string | null;
  allowed_protocols?: string | null;
  data_services?: string | null;
}

export interface NetAppVolume extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  state?: string | null;
  size_bytes?: number | null;
  used_bytes?: number | null;
  percent_used?: number | null;
  security_style?: string | null;
  language?: string | null;
  snapshot_autodelete_enabled?: boolean | null;
  autosize_mode?: string | null;
  snapshot_policy_name?: string | null;
  encryption_enabled?: boolean | null;
  snapmirror_protected?: boolean | null;
}

export interface NetAppLun extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  volume_name?: string | null;
  state?: string | null;
  size_bytes?: number | null;
  os_type?: string | null;
  mapped_igroups?: string | null;
}

export interface NetAppIgroup extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  os_type?: string | null;
  protocol?: string | null;
  initiator_count: number;
}

export interface NetAppClusterPeer extends NetAppDiscoveredBase {
  name?: string | null;
  remote_name?: string | null;
  state?: string | null;
  peer_ip_addresses?: string | null;
  local_ip_addresses?: string | null;
}

export interface NetAppSvmPeer extends NetAppDiscoveredBase {
  svm_name?: string | null;
  peer_svm_name?: string | null;
  peer_cluster_name?: string | null;
  state?: string | null;
  applications?: string | null;
}

export interface SnapMirrorRelationship extends NetAppDiscoveredBase {
  source_path?: string | null;
  destination_path?: string | null;
  state?: string | null;
  healthy: boolean;
  lag_time?: string | null;
  last_transfer_size_bytes?: number | null;
  last_transfer_error?: string | null;
  schedule_name?: string | null;
  policy_name?: string | null;
  destination_cluster_name?: string | null;
}

export interface NetAppNetworkInterface extends NetAppDiscoveredBase {
  name?: string | null;
  address?: string | null;
  svm_name?: string | null;
  state?: string | null;
}

export interface NetAppPlatform extends NetAppDiscoveredBase {
  node_name: string;
  model?: string | null;
  serial_number?: string | null;
  ontap_version?: string | null;
  uptime_seconds?: number | null;
  state?: string | null;
}

export interface NetAppAggregate extends NetAppDiscoveredBase {
  name: string;
  node_name?: string | null;
  state?: string | null;
  size_bytes?: number | null;
  used_bytes?: number | null;
  used_percent?: number | null;
  efficiency_ratio?: number | null;
  efficiency_ratio_wo_snapshots?: number | null;
}

export const IGROUP_OS_TYPES = ["aix", "hpux", "hyper_v", "linux", "netware", "openvms", "solaris", "vmware", "windows", "xen"] as const;
export const LUN_OS_TYPES = [
  "aix", "hpux", "hyper_v", "linux", "netware", "openvms", "solaris", "solaris_efi",
  "vmware", "windows", "windows_2008", "windows_gpt", "xen",
] as const;

export interface IgroupCreate {
  svm_name: string;
  name: string;
  os_type: string;
  protocol: "fcp" | "iscsi" | "mixed";
  initiators: string[];
}

export interface LunCreate {
  svm_name: string;
  lun_name: string;
  os_type: string;
  size_bytes: number;
  volume_name: string;
}

export interface VolumeCreate {
  svm_name: string;
  name: string;
  aggregate_name: string;
  size_bytes: number;
}

export interface LunMapCreate {
  svm_name: string;
  lun_name: string;
  igroup_name: string;
}

export interface LunCreationPlan {
  clusterId: string;
  svmName: string;
  volumeMode: "existing" | "new";
  volumeName: string;
  newVolumeAggregate?: string;
  newVolumeSizeBytes?: number;
  lunName: string;
  osType: string;
  lunSizeBytes: number;
  igroupMode: "none" | "existing" | "new";
  igroupName?: string;
  newIgroup?: {
    name: string;
    osType: string;
    protocol: "fcp" | "iscsi" | "mixed";
    initiators: string[];
  };
}

export interface ClusterPeerCreate {
  peer_cluster_id: string;
}

export interface SvmPeerCreate {
  local_svm_name: string;
  peer_cluster_id: string;
  peer_svm_name: string;
  applications: string[];
}

export interface VolumeCreatePayload {
  svm_name: string;
  name: string;
  aggregate_name: string;
  size_bytes: number;
  security_style?: "unix" | "ntfs" | "mixed" | null;
  guarantee_type?: "volume" | "none" | null;
  volume_type?: "rw" | "dp" | null;
}

export interface VolumeCreationPlan {
  clusterId: string;
  svmName: string;
  name: string;
  aggregateName: string;
  sizeBytes: number;
  securityStyle: "unix" | "ntfs" | "mixed";
  guaranteeType: "volume" | "none";
}

export interface LunEditPlan {
  clusterId: string;
  lunUuid: string;
  svmName: string;
  volumeName: string;
  currentShortName: string;
  newSizeBytes?: number;
  setEnabled?: boolean;
  unmapIgroupName?: string;
  mapIgroupName?: string;
}

export interface VolumeEditPlan {
  clusterId: string;
  volumeUuid: string;
  volumeName: string;
  newSizeBytes?: number;
  setState?: "online" | "offline";
}

export interface SnapMirrorPolicyRule {
  label: string;
  count: string;
}

export interface SnapMirrorPolicyRuleWrite {
  label: string;
  count: number;
}

export interface NetAppSnapMirrorPolicy extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  scope?: string | null;
  type?: string | null;
  comment?: string | null;
  rules: SnapMirrorPolicyRule[];
}

export interface NetAppSchedule extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  scope?: string | null;
  schedule_type?: string | null;
  minutes: number[];
  hours: number[];
  days: number[];
  weekdays: number[];
}

export type VaultType = "vault" | "mirror_vault";

export interface NewPolicyPlan {
  svmName: string;
  name: string;
  vaultType: VaultType;
  rules: SnapMirrorPolicyRuleWrite[];
}

export interface NewSchedulePlan {
  svmName?: string;
  name: string;
  minutes: number[];
  hours: number[];
  days: number[];
  weekdays: number[];
}

export interface PolicyCreationPlan {
  clusterId: string;
  svmName: string;
  name: string;
  vaultType: VaultType;
  rules: SnapMirrorPolicyRuleWrite[];
}

export interface PolicyEditPlan {
  clusterId: string;
  policyUuid: string;
  policyName: string;
  rules: SnapMirrorPolicyRuleWrite[];
}

export interface ScheduleCreationPlan {
  clusterId: string;
  svmName?: string;
  name: string;
  minutes: number[];
  hours: number[];
  days: number[];
  weekdays: number[];
}

export interface SnapmirrorCreationPlan {
  sourceClusterId: string;
  sourceSvmName: string;
  sourceVolumeName: string;
  sourceVolumeSizeBytes: number;
  destinationClusterId: string;
  destinationSvmName: string;
  destinationVolumeName: string;
  destinationAggregate: string;
  policyMode: "existing" | "new";
  policyName?: string;
  newPolicy?: NewPolicyPlan;
  scheduleMode: "none" | "existing" | "new";
  scheduleName?: string;
  newSchedule?: NewSchedulePlan;
  autoInitialize: boolean;
}

export interface SnapmirrorEditPlan {
  clusterId: string;
  relationshipUuid: string;
  sourcePath: string;
  destinationSvmName: string;
  policyMode: "existing" | "new";
  policyName?: string;
  newPolicy?: NewPolicyPlan;
  scheduleMode: "unchanged" | "none" | "existing" | "new";
  scheduleName?: string;
  newSchedule?: NewSchedulePlan;
}

export interface MetroClusterStatus {
  configured: boolean;
  mode: string;
  switchover_in_progress: boolean;
}

export type BackupScope = "vm" | "csv" | "lun";
export type ConsistencyType = "ApplicationConsistent" | "CrashConsistent";
export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cleaning_up"
  | "cleaned_up_after_failure";

export type ScheduleType = "hourly" | "daily" | "weekly" | "monthly";

export interface Schedule {
  id: string;
  name: string;
  schedule_type: ScheduleType;
  times: string[];
  weekday?: number | null;
  day_of_month?: number | null;
  created_at: string;
}

export interface SnapMirrorLabel {
  id: string;
  name: string;
  created_at: string;
}

export type RetentionType = "days" | "count";

export interface BackupPolicy {
  id: string;
  name: string;
  schedule_id?: string | null;
  schedule?: Schedule | null;
  consistency: ConsistencyType;
  snapmirror_update: boolean;
  snapmirror_label_id?: string | null;
  snapmirror_label?: SnapMirrorLabel | null;
  retention_type: RetentionType;
  retention_value: number;
  snapshot_locking_enabled: boolean;
  snapshot_locking_days?: number | null;
  metrocluster_aware: boolean;
  enabled: boolean;
  created_at: string;
}

export interface PolicySummary {
  id: string;
  name: string;
}

export interface ResourceGroup {
  id: string;
  name: string;
  scope: BackupScope;
  members: string[];
  policies: PolicySummary[];
  created_at: string;
}

export interface BackupRunSnapshot {
  id: string;
  netapp_cluster_name?: string | null;
  svm_name?: string | null;
  volume_name?: string | null;
  csv_names: string[];
  lun_names: string[];
  vm_names: string[];
  snapshot_name?: string | null;
  snapshot_uuid?: string | null;
  success: boolean;
  error_message?: string | null;
}

export interface BackupSnapshot {
  id: string;
  run_id: string;
  policy_name: string;
  consistency: ConsistencyType;
  created_at: string;
  netapp_cluster_name?: string | null;
  svm_name?: string | null;
  volume_name?: string | null;
  csv_names: string[];
  vm_names: string[];
  snapshot_name?: string | null;
  snapshot_uuid?: string | null;
}

export interface BackupJobRun {
  id: string;
  job_id?: string | null;
  job_name: string;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  scope?: BackupScope | null;
  targets: string[];
  error_message?: string | null;
  snapshots: BackupRunSnapshot[];
}

export interface RestoreInitiatorInfo {
  configured: boolean;
  iqn?: string | null;
  error?: string | null;
}

export interface RestoreLifCandidate {
  name: string;
  address: string;
  reachable: boolean;
}

export interface RestoreBroadcastDomainPort {
  node_name: string;
  port_name: string;
}

export interface RestoreBroadcastDomain {
  name: string;
  ipspace: string;
  ports: RestoreBroadcastDomainPort[];
}

export interface RestoreCreateLifPayload {
  svm_name: string;
  name: string;
  address: string;
  netmask: string;
  broadcast_domain: string;
  home_node: string;
  home_port: string;
}

export interface RestoreInfraSetupPayload {
  svm_name: string;
  iscsi_lif_name?: string | null;
  iscsi_lif_address: string;
  iscsi_lif_port?: number;
  igroup_name?: string;
}

export type RestoreMode = "replace" | "add";

export interface VmWithBackups {
  name: string;
  host?: string | null;
  state?: string | null;
  cluster?: string | null;
  backup_count: number;
}

export interface RestoreRunStep {
  step: string;
  label: string;
  status: "pending" | "running" | "success" | "error" | "skipped";
  message?: string | null;
}

export interface RestoreRun {
  id: string;
  vm_name: string;
  mode: RestoreMode;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  source_vhd_path: string;
  restored_vhd_path?: string | null;
  cleanup_needed: boolean;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: RestoreRunStep[];
}

export interface TriggerRestorePayload {
  vm_name: string;
  snapshot_id: string;
  source_vhd_path: string;
  mode: RestoreMode;
}

export interface RestoreInfraConfig {
  id: string;
  netapp_cluster_id: string;
  svm_name: string;
  iscsi_lif_name?: string | null;
  iscsi_lif_address: string;
  iscsi_lif_port: number;
  igroup_name: string;
  initiator_iqn: string;
}
