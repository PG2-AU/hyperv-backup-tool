import type { BackupPolicy, Schedule } from "@/api/types";

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
