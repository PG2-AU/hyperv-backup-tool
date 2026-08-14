export interface Vm {
  id: string;
  name: string;
  state: string;
  host: string;
  cluster?: string | null;
  csv_paths: string[];
}

export interface Csv {
  name: string;
  owner_node: string;
  state: string;
  volume_path: string;
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

export interface BackupJobDefinition {
  id: string;
  name: string;
  scope: BackupScope;
  targets: string[];
  consistency: ConsistencyType;
  schedule_cron?: string | null;
  snapmirror_label?: string | null;
  metrocluster_aware: boolean;
  enabled: boolean;
}

export interface BackupJobRun {
  id: string;
  job_id: string;
  job_name: string;
  status: JobStatus;
  started_at: string;
  finished_at?: string | null;
  scope: BackupScope;
  targets: string[];
  created_snapshots: string[];
  created_checkpoints: string[];
  error_message?: string | null;
}
