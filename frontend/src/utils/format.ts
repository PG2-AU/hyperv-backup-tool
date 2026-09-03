import type { BackupPolicy, BackupRunSnapshot, Schedule } from "@/api/types";

const UNITS = ["Bytes", "KB", "MB", "GB", "TB", "PB"] as const;

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "-";
  if (bytes === 0) return "0 Bytes";

  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), UNITS.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(exponent === 0 ? 0 : 1)} ${UNITS[exponent]}`;
}

const WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

export function formatSchedule(schedule?: Schedule | null): string {
  if (!schedule) return "manuell";

  switch (schedule.schedule_type) {
    case "hourly":
      return `Mehrmals täglich: ${schedule.times.join(", ")} Uhr`;
    case "daily":
      return `Täglich um ${schedule.times[0]} Uhr`;
    case "weekly":
      return `Wöchentlich, ${WEEKDAY_NAMES[schedule.weekday ?? 0]} um ${schedule.times[0]} Uhr`;
    case "monthly":
      return `Monatlich am ${schedule.day_of_month}. um ${schedule.times[0]} Uhr`;
    default:
      return schedule.name;
  }
}

export function formatRetention(policy: Pick<BackupPolicy, "retention_type" | "retention_value">): string {
  return policy.retention_type === "days" ? `${policy.retention_value} Tage` : `${policy.retention_value} Snapshots`;
}

const LAG_TIME_PATTERN = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/;

/** Gruppiert die Ziele eines Backup-Laufs nach CSV, statt sie als flache
 * Liste zu zeigen -- "[CSV02] Test [CSV03] VM01, VM02" statt "CSV02, CSV03,
 * Test, VM01, VM02", damit ersichtlich ist, welche VMs auf welchem CSV
 * gesichert wurden (Nutzer-Vorgabe). Nutzt die pro Snapshot bereits
 * vorhandene csv_names/vm_names-Zuordnung (BackupRunSnapshot) statt der nur
 * flachen targets-Liste auf BackupRun selbst. VMs ohne zugeordnetes CSV
 * (z.B. Aufloesungsfehler) werden ohne Klammer vorangestellt; ganz ohne
 * Snapshot-Daten (z.B. sehr alte Laeufe) faellt die Funktion auf die
 * mitgegebene flache targets-Liste zurueck. */
export function formatRunTargets(snapshots: BackupRunSnapshot[], fallbackTargets: string[]): string {
  if (snapshots.length === 0) return fallbackTargets.join(", ");

  const withCsv = snapshots.filter((s) => s.csv_names.length > 0);
  const withoutCsv = snapshots.filter((s) => s.csv_names.length === 0);

  const parts: string[] = [];
  const looseVms = new Set<string>();
  for (const s of withoutCsv) for (const vm of s.vm_names) looseVms.add(vm);
  if (looseVms.size > 0) parts.push([...looseVms].sort().join(", "));

  for (const s of [...withCsv].sort((a, b) => a.csv_names.join(",").localeCompare(b.csv_names.join(",")))) {
    const vms = [...s.vm_names].sort().join(", ");
    parts.push(`[${s.csv_names.join(", ")}]${vms ? ` ${vms}` : ""}`);
  }

  return parts.length ? parts.join(" ") : fallbackTargets.join(", ");
}

export function formatLagTime(lagTime?: string | null): string {
  if (!lagTime) return "-";
  const match = LAG_TIME_PATTERN.exec(lagTime);
  if (!match) return lagTime;
  const [, days, hours, minutes] = match;
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes && !days) parts.push(`${minutes}m`);
  return parts.length ? parts.join(" ") : "< 1m";
}
