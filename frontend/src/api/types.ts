export interface VhdInfo {
  name: string;
  size_bytes: number;
  used_bytes?: number | null;
  csv_path: string;
  full_path: string;
}

export interface NetworkAdapter {
  name: string;
  mac_address?: string | null;
  switch_name?: string | null;
  vlan_id?: number | null;
}

export interface Vm {
  id: string;
  name: string;
  state: string;
  host: string;
  cluster?: string | null;
  cluster_id?: string | null;
  csv_paths: string[];
  vhdx_size_bytes?: number | null;
  vhdx_used_bytes?: number | null;
  vhds: VhdInfo[];
  resource_group_names: string[];
  policy_names: string[];
  policy_ids: string[];
  protected: boolean;
  cpu_count?: number | null;
  generation?: number | null;
  memory_startup_bytes?: number | null;
  memory_minimum_bytes?: number | null;
  memory_maximum_bytes?: number | null;
  dynamic_memory_enabled?: boolean | null;
  network_adapters: NetworkAdapter[];
  pci_devices: string[];
}

export interface Csv {
  name: string;
  owner_node: string;
  state: string;
  hyperv_cluster_name?: string | null;
  cluster_id?: string | null;
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
  unreachable_nodes: { name: string; address?: string | null; error?: string | null }[];
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
  used_bytes?: number | null;
  percent_used?: number | null;
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
  display_type?: string | null;
  create_snapshot_on_source?: boolean | null;
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

export interface SnapMirrorCheckGroup {
  scope: BackupScope;
  members: string[];
}

export interface SnapMirrorCheckResult {
  svm_name: string;
  volume_name: string;
  members: string[];
  has_relationship: boolean;
  policy_name?: string | null;
  destination_path?: string | null;
}
export type ConsistencyType = "ApplicationConsistent" | "CrashConsistent";
export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cleaning_up"
  | "cleaned_up_after_failure"
  | "cancelled";

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
  consistency: ConsistencyType;
  snapmirror_update: boolean;
  snapmirror_label_id?: string | null;
  snapmirror_label?: SnapMirrorLabel | null;
  retention_type: RetentionType;
  retention_value: number;
  snapshot_locking_enabled: boolean;
  snapshot_locking_days?: number | null;
  metrocluster_aware: boolean;
  email_alert_on_failure: boolean;
  enabled: boolean;
  created_at: string;
}

export type AlertType =
  | "capacity_volume"
  | "capacity_lun"
  | "hyperv_cluster_unhealthy"
  | "netapp_cluster_unhealthy"
  | "snapmirror_unhealthy"
  | "snapmirror_lag_exceeded"
  | "hyperv_node_unreachable"
  | "backup_missed"
  | "schedule_collision"
  | "backup_failed";

export interface Alert {
  id: string;
  alert_type: AlertType;
  object_name: string;
  netapp_cluster_id?: string | null;
  netapp_cluster_name?: string | null;
  hyperv_cluster_id?: string | null;
  svm_name?: string | null;
  message: string;
  threshold_percent?: number | null;
  triggered_percent?: number | null;
  status: "active" | "resolved";
  triggered_at: string;
  resolved_at?: string | null;
  object_uuid?: string | null;
  run_id?: string | null;
  resource_group_id?: string | null;
  policy_id?: string | null;
}

export type AlertScope = "all" | "hyperv_referenced";

export interface AlertConfig {
  volume_threshold_percent: number;
  lun_threshold_percent: number;
  snapmirror_lag_threshold_hours: number;
  backup_missed_grace_minutes: number;
  schedule_collision_window_minutes: number;
  scope: AlertScope;
}

export interface AlertConfigWritePayload {
  volume_threshold_percent: number;
  lun_threshold_percent: number;
  snapmirror_lag_threshold_hours: number;
  backup_missed_grace_minutes: number;
  schedule_collision_window_minutes: number;
  scope: AlertScope;
}

export interface AllowedScheduleCollision {
  id: string;
  collision_key: string;
  summary: string;
  allowed_at: string;
}

export interface SchedulerConfig {
  healthcheck_interval_minutes: number;
  discovery_interval_minutes: number;
  snapshot_reconcile_hour: number;
  retention_cleanup_hour: number;
  updated_at?: string | null;
}

export interface SchedulerConfigWritePayload {
  healthcheck_interval_minutes: number;
  discovery_interval_minutes: number;
  snapshot_reconcile_hour: number;
  retention_cleanup_hour: number;
}

export interface EmailConfig {
  id: string;
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_encryption: "none" | "starttls" | "ssl";
  smtp_username?: string | null;
  has_password: boolean;
  from_address: string;
  from_name: string;
  recipients: string;
  notify_on_restore_failure: boolean;
  daily_summary_enabled: boolean;
  daily_summary_hour: number;
  last_test_at?: string | null;
  last_test_error?: string | null;
  updated_at?: string | null;
}

export interface EmailConfigWritePayload {
  enabled: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_encryption: "none" | "starttls" | "ssl";
  smtp_username?: string | null;
  smtp_password?: string | null;
  from_address: string;
  from_name: string;
  recipients: string;
  notify_on_restore_failure: boolean;
  daily_summary_enabled: boolean;
  daily_summary_hour: number;
}

export interface PolicySummary {
  id: string;
  name: string;
}

export interface ResourceGroupPolicyLink {
  policy_id: string;
  policy_name: string;
  schedule_id?: string | null;
  schedule?: Schedule | null;
}

export interface ResourceGroup {
  id: string;
  name: string;
  scope: BackupScope;
  members: string[];
  policies: PolicySummary[];
  // Zeitplan haengt an der Verknuepfung Resource-Group<->Policy, nicht an
  // der Resource Group oder der Policy allein -- ermoeglicht sowohl
  // zeitversetzte Gruppen mit derselben Policy als auch dieselbe Gruppe mit
  // mehreren, unterschiedlich geplanten Policies (siehe Backend
  // app.models.resource_group.ResourceGroupPolicyLink).
  policy_links: ResourceGroupPolicyLink[];
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

export interface BackupSnapshotVhd {
  name: string;
  path: string;
  size_bytes?: number | null;
  used_bytes?: number | null;
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
  vhds: BackupSnapshotVhd[];
  destinations: BackupSnapshotDestination[];
  restore_source: "primary" | "secondary";
}

export interface BackupSnapshotDestination {
  svm_name: string;
  volume_name: string;
  cluster_name?: string | null;
  present: boolean;
  restorable: boolean;
  last_checked_at: string;
}

export interface UpcomingJob {
  resource_group_id: string;
  resource_group_name: string;
  policy_id: string;
  policy_name: string;
  schedule_name: string;
  consistency: ConsistencyType;
  next_run_at: string;
}

export interface BackupJobRun {
  id: string;
  job_id?: string | null;
  job_name: string;
  // Nur bei einem geplanten Lauf gesetzt -- ein manuelles "Jetzt ausfuehren"
  // auf der ganzen Policy (potenziell mehrere Resource Groups) laesst das
  // leer, dann faellt die Anzeige auf job_name (Policy-Name) zurueck.
  resource_group_id?: string | null;
  resource_group_name?: string | null;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  scope?: BackupScope | null;
  targets: string[];
  error_message?: string | null;
  cancel_requested_at?: string | null;
  snapshots: BackupRunSnapshot[];
  steps: RestoreRunStep[];
}

export interface RestoreInitiatorInfo {
  configured: boolean;
  iqn?: string | null;
  error?: string | null;
  file_restore_available: boolean;
}

export interface FileRestoreRunStep {
  step: string;
  label: string;
  status: "pending" | "running" | "success" | "error" | "skipped";
  message?: string | null;
}

export interface FileRestoreRun {
  id: string;
  vm_name: string;
  source_vhd_path: string;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  browse_root_path?: string | null;
  default_destination_path?: string | null;
  cleanup_needed: boolean;
  expires_at?: string | null;
  used_secondary: boolean;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: FileRestoreRunStep[];
}

export interface TriggerFileRestorePayload {
  vm_name: string;
  snapshot_id: string;
  source_vhd_path: string;
}

export interface FileEntry {
  name: string;
  is_directory: boolean;
  size_bytes?: number | null;
  modified_at?: string | null;
}

export interface CopyFileRestoreSelectionPayload {
  selected_paths: string[];
  destination_path: string;
}

export interface RestoreProxyHostConfig {
  configured: boolean;
  address?: string | null;
  username?: string | null;
  use_https: boolean;
}

export interface RestoreProxyHostWrite {
  address: string;
  username: string;
  password?: string | null;
  use_https: boolean;
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
  cluster_id?: string | null;
  backup_count: number;
  exists_in_inventory: boolean;
}

export interface RestoreRunStep {
  step: string;
  label: string;
  status: "pending" | "running" | "success" | "error" | "skipped";
  message?: string | null;
}

export interface VmBackupRunVhd {
  name: string;
  size_bytes?: number | null;
  used_bytes?: number | null;
  csv_name?: string | null;
}

export interface VmBackupRunNetworkAdapter {
  name: string;
  switch_name?: string | null;
  vlan_id?: number | null;
}

export interface VmBackupRun {
  run_id: string;
  created_at: string;
  policy_name: string;
  consistency: ConsistencyType;
  cpu_count?: number | null;
  generation?: number | null;
  memory_startup_bytes?: number | null;
  dynamic_memory_enabled?: boolean | null;
  host_name?: string | null;
  network_adapters: VmBackupRunNetworkAdapter[];
  pci_devices: string[];
  vhds: VmBackupRunVhd[];
  restore_source: "primary" | "secondary";
}

export interface VmRecreateRun {
  id: string;
  vm_name: string;
  target_vm_name?: string | null;
  disconnect_network: boolean;
  destination_csv_name?: string | null;
  source_run_id: string;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  new_vm_uuid?: string | null;
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: RestoreRunStep[];
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

export interface StorageAccess {
  actions_enabled: boolean;
}
