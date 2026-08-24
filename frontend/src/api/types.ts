export interface Vm {
  id: string;
  name: string;
  state: string;
  host: string;
  cluster?: string | null;
  csv_paths: string[];
  vhdx_size_bytes?: number | null;
  backup_policy_id?: string | null;
  backup_policy_name?: string | null;
}

export interface Csv {
  name: string;
  owner_node: string;
  state: string;
  volume_path: string;
  capacity_bytes?: number | null;
  used_bytes?: number | null;
  backup_policy_id?: string | null;
  backup_policy_name?: string | null;
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
  cluster_uuid?: string | null;
  health: NetAppClusterHealth;
  node_count: number;
  healthy_node_count: number;
  is_metrocluster: boolean;
  last_checked_at?: string | null;
  last_check_error?: string | null;
  created_at: string;
}

export interface SvmInfo {
  name: string;
  state: string;
  is_metrocluster: boolean;
}

export interface SnapMirrorRelationship {
  uuid: string;
  source_path: string;
  destination_path: string;
  state: string;
  healthy: boolean;
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
  scope?: BackupScope | null;
  targets: string[];
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

export interface BackupJobRun {
  id: string;
  job_id: string;
  job_name: string;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  scope?: BackupScope | null;
  targets: string[];
  created_snapshots: string[];
  created_checkpoints: string[];
  error_message?: string | null;
}
