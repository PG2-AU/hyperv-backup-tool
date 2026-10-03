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

export interface VmCheckpoint {
  name: string;
  id: string;
  creation_time: string;
  // "hvnb_"-Praefix = von dieser App selbst erstellt, typischerweise ein
  // Ueberbleibsel eines abgebrochenen Backup-Laufs.
  app_created: boolean;
}

export interface Vm {
  id: string;
  name: string;
  state: string;
  host: string;
  cluster?: string | null;
  cluster_id?: string | null;
  csv_paths: string[];
  // UNC-Pfade ('\\server\share') der NetApp-CIFS-Freigaben, auf denen VHDs
  // dieser VM liegen (Backlog #22) -- parallel zu csv_paths.
  smb_share_paths: string[];
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
  checkpoints: VmCheckpoint[];
  // Standort-Kennzeichnung (siehe backend app.core.sites).
  host_site?: SiteBadge | null;
  storage_sites: SiteBadge[];
  site_mismatch: boolean;
  // CSVs/Freigaben, deren Standort vom Host-Standort abweicht.
  site_mismatch_storage: string[];
  // Host oder mindestens eine Disk noch keinem Standort zugeordnet.
  site_unassigned: boolean;
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
  site?: SiteBadge | null;
  // 'netapp' = vom NetApp-System geerbt, 'override' = manuell an der CSV.
  site_source?: "netapp" | "override" | null;
}

export interface SmbShare {
  server: string;
  share: string;
  hyperv_cluster_name?: string | null;
  cluster_id?: string | null;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  volume_name?: string | null;
  svm_name?: string | null;
  netapp_cluster_name?: string | null;
  resource_group_names: string[];
  policy_names: string[];
  policy_ids: string[];
  protected: boolean;
}

export type NetAppAuthMethod = "password" | "certificate";
export type NetAppClusterHealth = "unknown" | "healthy" | "degraded" | "unreachable";
export type NetAppSystemType = "cluster" | "svm";

export interface NetAppCluster {
  id: string;
  name: string;
  system_type: NetAppSystemType;
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

export interface HyperVClusterUpdate {
  name: string;
  management_address: string;
  username: string;
  // Leer = bestehendes Passwort beibehalten (siehe Backend HyperVClusterUpdate).
  password?: string;
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
  // Snapshot-Belegung (Backlog #67)
  snapshot_used_bytes?: number | null;
  snapshot_reserve_bytes?: number | null;
  snapshot_reserve_percent?: number | null;
  snapshot_count?: number | null;
  // davon laut Backup-Katalog von dieser App erstellte, noch vorhandene Snapshots
  backup_snapshot_count: number;
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

export interface NetAppCifsShare extends NetAppDiscoveredBase {
  name: string;
  svm_name?: string | null;
  volume_name?: string | null;
  path?: string | null;
  size_bytes?: number | null;
  used_bytes?: number | null;
  percent_used?: number | null;
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
  efficiency_ratio_wo_snapshots_flexclones?: number | null;
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

export type BackupScope = "vm" | "csv" | "lun" | "smb_share";

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
  | "succeeded_with_errors"
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
  paused: boolean;
  paused_since?: string | null;
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
  paused_since?: string | null;
  created_at: string;
}

export type AlertType =
  | "capacity_volume"
  | "capacity_lun"
  | "capacity_forecast_volume"
  | "capacity_forecast_lun"
  | "capacity_forecast_aggregate"
  | "hyperv_cluster_unhealthy"
  | "netapp_cluster_unhealthy"
  | "snapmirror_unhealthy"
  | "snapmirror_lag_exceeded"
  | "hyperv_node_unreachable"
  | "backup_missed"
  | "schedule_collision"
  | "hyperv_orphan_checkpoint"
  | "hyperv_vm_multi_csv"
  | "hyperv_vm_avhdx_without_checkpoint"
  | "hyperv_vm_site_mismatch"
  | "db_backup_failed"
  | "db_backup_overdue"
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
  // vm_name: bei hyperv_orphan_checkpoint, hyperv_vm_multi_csv,
  // hyperv_vm_site_mismatch UND hyperv_vm_avhdx_without_checkpoint gesetzt. Bei hyperv_orphan_checkpoint
  // zusaetzlich Grundlage fuer den "Checkpoint löschen"-Button, bei
  // hyperv_vm_avhdx_without_checkpoint fuer den "VM Discovery"-Button.
  vm_name?: string | null;
  checkpoint_id?: string | null;
}

export type AlertScope = "all" | "hyperv_referenced";

export interface AlertConfig {
  volume_threshold_percent: number;
  lun_threshold_percent: number;
  snapmirror_lag_threshold_hours: number;
  backup_missed_grace_minutes: number;
  schedule_collision_window_minutes: number;
  orphan_checkpoint_grace_minutes: number;
  avhdx_without_checkpoint_grace_minutes: number;
  site_mismatch_grace_minutes: number;
  alert_check_interval_minutes: number;
  // 0 = deaktiviert (kein automatisches Quittieren verpasster Laeufe)
  backup_missed_auto_dismiss_days: number;
  scope: AlertScope;
}

export interface AlertConfigWritePayload {
  volume_threshold_percent: number;
  lun_threshold_percent: number;
  snapmirror_lag_threshold_hours: number;
  backup_missed_grace_minutes: number;
  schedule_collision_window_minutes: number;
  orphan_checkpoint_grace_minutes: number;
  avhdx_without_checkpoint_grace_minutes: number;
  site_mismatch_grace_minutes: number;
  alert_check_interval_minutes: number;
  backup_missed_auto_dismiss_days: number;
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
  backup_cancel_force_timeout_minutes: number;
  backup_run_max_duration_minutes: number;
  backup_checkpoint_parallelism: number;
  updated_at?: string | null;
}

export interface SchedulerConfigWritePayload {
  healthcheck_interval_minutes: number;
  discovery_interval_minutes: number;
  snapshot_reconcile_hour: number;
  retention_cleanup_hour: number;
  backup_cancel_force_timeout_minutes: number;
  backup_run_max_duration_minutes: number;
  backup_checkpoint_parallelism: number;
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
  paused: boolean;
  paused_since?: string | null;
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
  // Anzeigename -- bei aktivem Checkpoint (is_avhdx) der wahrscheinliche
  // Name der Basis-VHDX statt der Checkpoint-AVHDX (rein kosmetisch).
  display_name: string;
  path: string;
  size_bytes?: number | null;
  used_bytes?: number | null;
  is_avhdx: boolean;
  // Nur gesetzt wenn is_avhdx: Id des Checkpoints, dessen eigener
  // aufgezeichneter Stand fuer DIESES VHD bereits eine plain VHDX ist --
  // der Datei-Modus kann NUR diesen Stand direkt mounten (kein Merge
  // moeglich dort).
  plain_checkpoint_id?: string | null;
}

// Ein zum Backup-Zeitpunkt auf der VM vorhandener Checkpoint -- fuer die
// Auswahl "auf welchen Stand restorieren".
export interface BackupSnapshotCheckpoint {
  id: string;
  name: string;
  creation_time: string;
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
  checkpoints: BackupSnapshotCheckpoint[];
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
  // Nur relevant, wenn die VHD is_avhdx ist: siehe BackupSnapshotVhd.plain_checkpoint_id.
  avhdx_checkpoint_id?: string | null;
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
  hostname?: string | null;
  username?: string | null;
  use_https: boolean;
}

export interface RestoreProxyHostWrite {
  address: string;
  hostname?: string | null;
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
  initiator_portal_address?: string | null;
}

export interface RestoreProxyIpAddress {
  address: string;
  interface_alias: string;
  prefix_length?: number | null;
}

export interface RestoreInfraCheckResult {
  reachable: boolean;
  detail: string;
  source_address?: string | null;
  target: string;
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
  display_name: string;
  size_bytes?: number | null;
  used_bytes?: number | null;
  csv_name?: string | null;
  is_avhdx: boolean;
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
  checkpoints: BackupSnapshotCheckpoint[];
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
  // Nur relevant, wenn die restorte VHD eine .avhdx ist: leer/undefined =
  // Stand zum Backup-Zeitpunkt (voller Merge, Standard), sonst die Id
  // eines der zum Backup-Zeitpunkt vorhandenen Checkpoints (siehe
  // BackupSnapshot.checkpoints) -- Restore auf genau dessen Stand.
  avhdx_checkpoint_id?: string | null;
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
  initiator_portal_address?: string | null;
}

export interface StorageAccess {
  actions_enabled: boolean;
  hide_metrocluster_mirrors: boolean;
}

// --- Settings > WinRM-Zertifikate ---

export interface WinrmHostCertificate {
  id: string;
  label: string;
  host_address?: string | null;
  fingerprint_sha256: string;
  subject_cn?: string | null;
  sans: string[];
  not_before?: string | null;
  not_after?: string | null;
  uploaded_at: string;
  uploaded_by?: string | null;
}

export interface WinrmTrustState {
  last_bundle_built_at?: string | null;
  bundle_path?: string | null;
  bundle_cert_count: number;
  updated_by?: string | null;
}

export interface WinrmCertsOverview {
  certificates: WinrmHostCertificate[];
  trust_state: WinrmTrustState;
  active_trust_path: string;
  bundle_outdated: boolean;
}

export type WinrmClusterType = "failover_cluster" | "single_host";

export interface WinrmSetupScriptRequest {
  cluster_type: WinrmClusterType;
  cno_hostname?: string | null;
  cno_ip?: string | null;
  own_ip?: string | null;
}

// Settings > Kerberos (Backlog-Punkt 50) -- ein globales Realm/KDC-Paar,
// siehe app.core.kerberos_config fuer den Hintergrund (bewusst kein
// Multi-Domain).
export interface KerberosConfig {
  realm?: string | null;
  kdc_hostname?: string | null;
  kdc_address?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface KerberosDetectResult {
  realm: string;
  kdc_hostname: string;
  kdc_address?: string | null;
}

export interface KerberosTestResult {
  success: boolean;
  message: string;
}

// Kapazitaetsverlauf (Liniendiagramm) fuer VHDs/CSVs/LUNs/Volumes/
// Aggregate, siehe GET /api/capacity-history. object_key ist der stabile,
// serverseitig abgeleitete Schluessel (siehe capacity_key im Backend) --
// NICHT die id des jeweiligen Discovery-Objekts.
export type CapacityObjectType = "vhd" | "csv" | "lun" | "volume" | "aggregate" | "smb_share";

export interface CapacitySamplePoint {
  sampled_at: string;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  // nur bei Volumes: davon durch Snapshots belegt
  snapshot_used_bytes?: number | null;
}

// Prognose 4 Wochen voraus (Backlog #79, backend app.core.capacity_forecast).
export interface CapacityForecast {
  growth_bytes_per_day: number;
  capacity_bytes: number;
  days_to_full?: number | null;
  full_at?: string | null;
  horizon_days: number;
  points: CapacitySamplePoint[];
}

export interface CapacitySeries {
  object_key: string;
  object_name: string;
  points: CapacitySamplePoint[];
  // null = zu wenige Messpunkte fuer eine Prognose.
  forecast?: CapacityForecast | null;
}

// --- Standorte (Settings > Standorte, siehe backend app.models.site) ---

export interface SiteBadge {
  id: string;
  name: string;
  color: string;
}

export interface Site extends SiteBadge {
  description?: string | null;
  created_at: string;
}

export interface SiteWrite {
  name: string;
  description?: string | null;
  color: string;
}

export interface SiteNodeAssignment {
  node_name: string;
  site_id?: string | null;
  vm_count: number;
}

export interface SiteHyperVClusterAssignment {
  cluster_id: string;
  cluster_name: string;
  nodes: SiteNodeAssignment[];
}

export interface SiteNetAppClusterAssignment {
  netapp_cluster_id: string;
  name: string;
  is_metrocluster: boolean;
  metrocluster_mode?: string | null;
  site_id?: string | null;
}

export interface SiteCsvAssignment {
  cluster_id: string;
  cluster_name: string;
  csv_name: string;
  disk_serial_number?: string | null;
  netapp_cluster_name?: string | null;
  inherited_site_id?: string | null;
  override_site_id?: string | null;
}

export interface SiteAssignments {
  hyperv_clusters: SiteHyperVClusterAssignment[];
  netapp_clusters: SiteNetAppClusterAssignment[];
  csvs: SiteCsvAssignment[];
  // NetApp-Systeme ausserhalb des MetroCluster-Normalbetriebs -- solange
  // nicht leer, ist die Standort-Abweichungs-Pruefung ausgesetzt.
  switchover_clusters: string[];
}

export interface SiteMismatchSummary {
  sites_configured: boolean;
  mismatch_count: number;
  unassigned_count: number;
  switchover_clusters: string[];
}

// --- VM verschieben (Inventory > VMs, siehe backend app.api.routes.vm_moves) ---

export interface VmMoveTargetNode {
  name: string;
  state: string;
  is_current: boolean;
  site?: SiteBadge | null;
  vm_count: number;
  memory_total_bytes?: number | null;
  memory_free_bytes?: number | null;
  memory_free_after_bytes?: number | null;
  // false = zu wenig RAM inkl. Reserve, null = unbekannt
  fits_memory?: boolean | null;
  memory_error?: string | null;
  recommended: boolean;
}

export interface VmMoveTargets {
  vm_name: string;
  current_node?: string | null;
  host_site?: SiteBadge | null;
  storage_sites: SiteBadge[];
  vm_memory_bytes?: number | null;
  vm_state?: string | null;
  memory_reserve_bytes_hint: string;
  nodes: VmMoveTargetNode[];
  recommended_node?: string | null;
  recommended_reason?: string | null;
  blocked_reason?: string | null;
}

export interface VmMoveRunStep {
  step: string;
  label: string;
  status: "pending" | "running" | "success" | "error" | "skipped";
  message?: string | null;
}

export interface VmMoveRun {
  id: string;
  hyperv_cluster_id: string;
  vm_name: string;
  move_type: string;
  source_node?: string | null;
  target_node: string;
  destination_csv_name?: string | null;
  destination_smb_server?: string | null;
  destination_smb_share?: string | null;
  // CSV-Name oder \\server\share
  destination_label?: string | null;
  progress_percent?: number | null;
  cancel_requested_at?: string | null;
  status: "running" | "succeeded" | "failed";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: VmMoveRunStep[];
}

// Storage-Ziel: CSV oder (kind "smb") SMB3-Freigabe, name = \\server\share
export interface VmStorageTargetCsv {
  kind: "csv" | "smb";
  name: string;
  smb_server?: string | null;
  smb_share?: string | null;
  path?: string | null;
  capacity_bytes?: number | null;
  free_bytes?: number | null;
  // Zusatzbelegung, wenn diese CSV das Ziel ist
  needed_bytes: number;
  free_after_bytes?: number | null;
  // Heute von der VM hier belegt, wird beim Wegverschieben frei
  freed_bytes: number;
  site?: SiteBadge | null;
  is_current: boolean;
  fits: boolean;
  protection_groups_after: string[];
  protection_change: "same" | "changed" | "lost" | "gained";
}

export interface VmStorageTargets {
  vm_name: string;
  current_csvs: string[];
  host_site?: SiteBadge | null;
  required_bytes: number;
  protection_groups_now: string[];
  csvs: VmStorageTargetCsv[];
  usage_live: boolean;
  usage_note?: string | null;
  reserve_hint: string;
  recommended_csv?: string | null;
  recommended_reason?: string | null;
  blocked_reason?: string | null;
}

// --- Konfiguration exportieren/importieren (Settings > System, Backlog #45) ---

export interface ConfigImportPreview {
  created_at?: string | null;
  created_by?: string | null;
  app_commit?: string | null;
  include_catalog: boolean;
  tables: { table: string; label: string; count: number }[];
  manual_steps: string[];
  warnings: string[];
  // nicht leer = Import nicht moeglich
  blockers: string[];
}

export interface ConfigImportResult {
  imported: Record<string, number>;
  paused_groups: string[];
  skipped_users: string[];
  manual_steps: string[];
  bundle_rebuilt: boolean;
}

// --- DB-Sicherung (Settings > DB-Sicherung, Backlog #66) ---

export interface DbBackupConfig {
  enabled: boolean;
  // Kennung im Dateinamen, Aufbewahrung raeumt nur Dateien mit dieser Kennung auf
  instance_name: string;
  share_path: string;
  username: string;
  password_set: boolean;
  hour_utc: number;
  retention_days: number;
  local_keep: number;
  last_attempt_at?: string | null;
  last_success_at?: string | null;
  last_file_name?: string | null;
  last_size_bytes?: number | null;
  last_error?: string | null;
  last_upload_failed: boolean;
}

export interface DbBackupConfigWrite {
  enabled: boolean;
  instance_name: string;
  share_path: string;
  username: string;
  // undefined/null = unveraendert, "" = loeschen
  password?: string | null;
  hour_utc: number;
  retention_days: number;
  local_keep: number;
}

export interface DbBackupFile {
  name: string;
  size_bytes: number;
  created_at: string;
  host: string;
  pre_restore: boolean;
}

export interface DbBackupList {
  share: DbBackupFile[];
  local: DbBackupFile[];
  share_error?: string | null;
}

export interface DbRestorePreview {
  counts: Record<string, number>;
  latest_backup_run_at?: string | null;
  secret_key_ok?: boolean | null;
  blockers: string[];
  running_jobs: string[];
  confirm_word: string;
}

// --- CSV vergroessern (Inventory > CSVs, Backlog #69) ---

export interface CsvResizeInfo {
  cluster_id: string;
  netapp_cluster_id: string;
  netapp_cluster_name: string;
  csv: {
    name: string;
    path?: string | null;
    owner_node?: string | null;
    state?: string | null;
    capacity_bytes?: number | null;
    used_bytes?: number | null;
    serial_number: string;
  };
  partition: { disk_size_bytes: number; partition_size_bytes: number; partition_max_bytes: number };
  lun: { uuid: string; name: string; svm_name?: string | null; size_bytes: number; used_bytes?: number | null; space_reserved: boolean };
  volume: {
    uuid: string;
    name: string;
    size_bytes: number;
    used_bytes?: number | null;
    available_bytes?: number | null;
    max_size_bytes?: number | null;
    snapshot_reserve_bytes?: number | null;
    snapshot_reserve_percent?: number | null;
    snapshot_used_bytes?: number | null;
    guarantee?: string | null;
    autosize_mode?: string | null;
    luns_total_bytes: number;
    other_luns_bytes: number;
    lun_count: number;
  };
  aggregate?: { name: string; size_bytes?: number | null; used_bytes?: number | null; available_bytes?: number | null } | null;
  aggregate_count: number;
  blocked_reason?: string | null;
}

export interface CsvResizeRun {
  id: string;
  csv_name: string;
  new_volume_size_bytes?: number | null;
  new_lun_size_bytes?: number | null;
  csv_size_before_bytes?: number | null;
  csv_size_after_bytes?: number | null;
  status: "running" | "succeeded" | "failed";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null }[];
}

// Neue CSV per Assistent (backend app.api.routes.csv_create, Backlog #68).
export interface CsvCreateNode {
  name: string;
  state: string;
  initiators: { type: "iscsi" | "fc"; address: string }[];
  error?: string | null;
}

export interface CsvCreateHyperVOptions {
  cluster_id: string;
  nodes: CsvCreateNode[];
  csv_names: string[];
  busy_reason?: string | null;
}

export interface CsvCreateNetAppOptions {
  netapp_cluster_id: string;
  system_type: string;
  svms: { name: string; allowed_protocols?: string | null }[];
  aggregates: { name: string; state?: string | null; size_bytes?: number | null; available_bytes?: number | null }[];
  igroups: { name: string; svm_name?: string | null; os_type?: string | null; protocol?: string | null; initiators: string[] }[];
}

export interface CsvCreatePayload {
  cluster_id: string;
  netapp_cluster_id: string;
  svm_name: string;
  aggregate_name: string | null;
  csv_name: string;
  volume_name: string;
  lun_name: string;
  lun_size_bytes: number;
  volume_size_bytes: number;
  autosize_grow: boolean;
  igroup_names: string[];
  file_system: "NTFS" | "ReFS";
  allocation_unit: 4096 | 65536;
  rename_folder: boolean;
  resource_group_id: string | null;
}

export interface CsvCreateRun {
  id: string;
  csv_name: string;
  svm_name: string;
  volume_name: string;
  lun_name: string;
  volume_size_bytes: number;
  lun_size_bytes: number;
  created_volume_uuid?: string | null;
  created_lun_uuid?: string | null;
  lun_id?: number | null;
  mapped_igroups: string[];
  format_node?: string | null;
  disk_formatted: boolean;
  cluster_resource_name?: string | null;
  csv_added: boolean;
  csv_path?: string | null;
  rollback_declined: boolean;
  has_created_objects: boolean;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null }[];
}

// CSV loeschen (backend app.api.routes.csv_delete).
export interface CsvDeleteInfo {
  cluster_id: string;
  csv_name: string;
  csv_path?: string | null;
  owner_node?: string | null;
  state?: string | null;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  serial_number: string;
  netapp_cluster_id: string;
  netapp_cluster_name: string;
  lun_uuid: string;
  lun_name: string;
  svm_name?: string | null;
  lun_size_bytes: number;
  igroups: string[];
  volume_uuid: string;
  volume_name: string;
  volume_size_bytes: number;
  other_luns: string[];
  volume_deletable: boolean;
  snapshots: { total: number; backup_count: number; oldest_backup?: string | null; newest_backup?: string | null };
  snapmirror_destinations: string[];
  vms: string[];
  vm_files: { path: string; size_bytes: number }[];
  other_files: { path: string; size_bytes: number }[];
  files_truncated: boolean;
  protection_groups: string[];
  blocked_reasons: string[];
  warnings: string[];
}

export interface CsvDeleteRun {
  id: string;
  csv_name: string;
  lun_name: string;
  volume_name: string;
  delete_lun: boolean;
  delete_volume: boolean;
  capacity_bytes?: number | null;
  removed_from_groups: string[];
  status: "running" | "succeeded" | "failed";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null }[];
}

// SMB3-Freigabe anlegen/loeschen (backend app.api.routes.smb_create/smb_delete).
export interface SmbCreateHyperVOptions {
  cluster_id: string;
  domain: string;
  accounts: { account: string; source: string }[];
  existing_shares: string[];
  busy_reason?: string | null;
  warnings: string[];
}

export interface SmbCreateNetAppOptions {
  netapp_cluster_id: string;
  svms: { name: string; cifs_server: string }[];
  aggregates: { name: string; state?: string | null; size_bytes?: number | null; available_bytes?: number | null }[];
}

export interface SmbCreatePayload {
  cluster_id: string;
  netapp_cluster_id: string;
  svm_name: string;
  aggregate_name: string | null;
  volume_name: string;
  share_name: string;
  volume_size_bytes: number;
  autosize_grow: boolean;
  accounts: string[];
  resource_group_id: string | null;
}

type RunStep = { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null };

export interface SmbCreateRun {
  id: string;
  svm_name: string;
  volume_name: string;
  share_name: string;
  volume_size_bytes: number;
  cifs_server?: string | null;
  unc_path?: string | null;
  created_volume_uuid?: string | null;
  share_created: boolean;
  rollback_declined: boolean;
  has_created_objects: boolean;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: RunStep[];
}

export interface SmbDeleteInfo {
  cluster_id: string;
  server: string;
  share: string;
  share_path?: string | null;
  netapp_cluster_id: string;
  netapp_cluster_name: string;
  svm_name: string;
  volume_uuid: string;
  volume_name: string;
  volume_size_bytes: number;
  used_bytes?: number | null;
  other_shares: string[];
  lun_count: number;
  volume_deletable: boolean;
  snapshots: { total: number; backup_count: number; oldest_backup?: string | null; newest_backup?: string | null };
  snapmirror_destinations: string[];
  vms: string[];
  vm_files: { path: string; size_bytes: number }[];
  other_files: { path: string; size_bytes: number }[];
  files_truncated: boolean;
  protection_groups: string[];
  blocked_reasons: string[];
  warnings: string[];
}

export interface SmbDeleteRun {
  id: string;
  server: string;
  share: string;
  volume_name: string;
  delete_volume: boolean;
  capacity_bytes?: number | null;
  removed_from_groups: string[];
  status: "running" | "succeeded" | "failed";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: RunStep[];
}

// Neue VM per Assistent (backend app.api.routes.vm_create, Backlog #74).
export interface VmCreateNode {
  name: string;
  state: string;
  site?: SiteBadge | null;
  vm_count: number;
  memory_total_bytes?: number | null;
  memory_free_bytes?: number | null;
  switches: { name: string; type: string }[];
  error?: string | null;
}

export interface VmCreateLocation {
  kind: "csv" | "smb";
  key: string;
  label: string;
  root: string;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  site?: SiteBadge | null;
}

export interface VmCreateOptions {
  cluster_id: string;
  nodes: VmCreateNode[];
  locations: VmCreateLocation[];
  vm_names: string[];
  busy_reason?: string | null;
}

export interface VmCreateIsoList {
  isos: { path: string; size_bytes: number }[];
  warnings: string[];
}

export interface VmCreatePayload {
  cluster_id: string;
  vm_name: string;
  node_name: string;
  location_kind: "csv" | "smb";
  location_key: string;
  generation: 1 | 2;
  cpu_count: number;
  memory_startup_bytes: number;
  dynamic_memory: boolean;
  memory_minimum_bytes: number | null;
  memory_maximum_bytes: number | null;
  disk_size_bytes: number;
  disk_dynamic: boolean;
  data_disks: { size_bytes: number; dynamic: boolean }[];
  switch_name: string | null;
  vlan_id: number | null;
  secure_boot: boolean;
  secure_boot_template: string;
  tpm: boolean;
  iso_path: string | null;
  high_availability: boolean;
  start_after: boolean;
  resource_group_id: string | null;
}

export interface VmCreateRun {
  id: string;
  vm_name: string;
  node_name: string;
  storage_root: string;
  vm_folder: string;
  new_vm_uuid?: string | null;
  vm_created: boolean;
  cluster_role_added: boolean;
  rollback_declined: boolean;
  has_created_objects: boolean;
  status: "running" | "succeeded" | "failed" | "cleaned_up";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null }[];
}

// VM loeschen (backend app.api.routes.vm_delete).
export interface VmDeleteInfo {
  cluster_id: string;
  vm_name: string;
  vm_id: string;
  state: string;
  node: string;
  configuration_location?: string | null;
  checkpoint_count: number;
  files: { path: string; size_bytes?: number | null; shared_with?: string | null }[];
  folders: string[];
  total_bytes: number;
  backup_count: number;
  protection_groups: string[];
  blocked_reasons: string[];
  warnings: string[];
}

export interface VmDeleteRun {
  id: string;
  vm_name: string;
  node_name?: string | null;
  delete_files: boolean;
  turn_off: boolean;
  removed_from_groups: string[];
  status: "running" | "succeeded" | "failed";
  error_message?: string | null;
  started_at: string;
  finished_at?: string | null;
  steps: { step: string; label: string; status: "pending" | "running" | "success" | "error" | "skipped"; message?: string | null }[];
}
