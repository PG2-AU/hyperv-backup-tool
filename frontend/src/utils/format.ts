import type { BackupPolicy, BackupRunSnapshot, Schedule } from "@/api/types";

const UNITS = ["Bytes", "KB", "MB", "GB", "TB", "PB"] as const;

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "-";
  if (bytes === 0) return "0 Bytes";

  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), UNITS.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(exponent === 0 ? 0 : 1)} ${UNITS[exponent]}`;
}

// Frueher separat (und einmal -- DashboardPage -- mit abweichendem
// "degraded"-Farbton "orange" statt "yellow", vermutlich ein Kopierfehler)
// in StoragePage.tsx, SettingsPage.tsx und DashboardPage.tsx definiert --
// hierher konsolidiert (2026-09-19), damit alle drei Seiten denselben
// Farbton fuer denselben NetApp-Cluster-Health-Wert zeigen.
export const HEALTH_COLOR: Record<string, string> = { healthy: "green", degraded: "yellow", unreachable: "red", unknown: "gray" };

// Frueher identisch in RestorePage.tsx und VmsPage.tsx dupliziert.
export const VM_STATE_COLOR: Record<string, string> = { Running: "green", Off: "gray", Saved: "yellow" };

// Frueher identisch als fmtDate/formatTimestamp in WinrmCertsTab.tsx,
// AdConfigTab.tsx, KerberosTab.tsx und VersionFooter.tsx dupliziert --
// hierher konsolidiert (2026-09-19). `fallback` optional, da die
// Aufrufer unterschiedliche Platzhaltertexte brauchten ("–" vs. "noch
// nicht gelaufen" vs. "unbekannt").
export function formatDateTime(value: string | null | undefined, fallback = "–"): string {
  return value ? new Date(value).toLocaleString("de-DE") : fallback;
}

const WEEKDAY_NAMES = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];

export function formatSchedule(schedule?: Schedule | null): string {
  if (!schedule) return "manuell";

  switch (schedule.schedule_type) {
    case "hourly":
      // Live gefunden (2026-09-18): times steht in der Reihenfolge, in der
      // die Uhrzeiten im Bearbeiten-Dialog hinzugefuegt/editiert wurden,
      // nicht chronologisch -- vor der Anzeige sortieren ("HH:MM" sortiert
      // als Zeichenkette bereits korrekt chronologisch).
      return `Mehrmals täglich: ${[...schedule.times].sort().join(", ")} Uhr`;
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

// ONTAP fuehrt einen LUN-"Namen" intern immer als vollen Pfad
// (z.B. "/vol/CSV01_vol/CSV01") -- fuer die Anzeige reicht ueberall im Tool
// der letzte Pfadabschnitt (Storage > LUNs zeigte das bereits so, Nutzer-
// Vorgabe: durchgaengig). Rein fuers Rendering: jede Stelle, die den LUN-
// Namen tatsaechlich als ONTAP-Objektbezeichner braucht (LUN anlegen/
// bearbeiten/mappen, Restore-/Backup-Logik), verwendet weiterhin den
// vollen, unveraenderten Pfad -- siehe utils/netappSteps.ts, das den vollen
// Pfad aus Volume-Name + Kurzname selbst wieder zusammensetzt.
export function lunShortName(fullPathOrName?: string | null): string {
  if (!fullPathOrName) return "-";
  const parts = fullPathOrName.split("/");
  return parts[parts.length - 1] || fullPathOrName;
}

// Backend-Schluessel fuer eine SMB3-Freigabe (Backlog #22) ist "server|share"
// (siehe _smb_share_key in jobs.py, bewusst "|" statt "::" wie bei Resource-
// Group-Membern) -- fuer die Anzeige als UNC-Pfad formatiert, wie Inventory >
// SMB3-Freigaben es zeigt. Nicht betroffene Strings (kein "|") kommen
// unveraendert zurueck.
export function formatSmbShareKey(key: string): string {
  const sepIdx = key.indexOf("|");
  return sepIdx === -1 ? key : `\\\\${key.slice(0, sepIdx)}\\${key.slice(sepIdx + 1)}`;
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

// Sperrfrist einer SnapMirror-Regel (ISO-8601-Dauer mit genau einer Einheit, wie ONTAP sie liefert).
export type LockPeriodUnit = "hours" | "days" | "months" | "years";

const LOCK_PERIOD_UNITS: Record<LockPeriodUnit, { iso: (n: number) => string; label: (n: number) => string }> = {
  hours: { iso: (n) => `PT${n}H`, label: (n) => (n === 1 ? "Stunde" : "Stunden") },
  days: { iso: (n) => `P${n}D`, label: (n) => (n === 1 ? "Tag" : "Tage") },
  months: { iso: (n) => `P${n}M`, label: (n) => (n === 1 ? "Monat" : "Monate") },
  years: { iso: (n) => `P${n}Y`, label: (n) => (n === 1 ? "Jahr" : "Jahre") },
};

export function parseLockPeriod(period?: string | null): { value: number; unit: LockPeriodUnit } | null {
  if (!period) return null;
  const date = period.match(/^P(\d+)([YMD])$/);
  if (date) return { value: Number(date[1]), unit: date[2] === "Y" ? "years" : date[2] === "M" ? "months" : "days" };
  const hours = period.match(/^PT(\d+)H$/);
  return hours ? { value: Number(hours[1]), unit: "hours" } : null;
}

export function buildLockPeriod(value: number, unit: LockPeriodUnit): string {
  return LOCK_PERIOD_UNITS[unit].iso(value);
}

export function formatLockPeriod(period?: string | null): string {
  if (!period) return "";
  if (period === "infinite") return "unbegrenzt";
  const parsed = parseLockPeriod(period);
  return parsed ? `${parsed.value} ${LOCK_PERIOD_UNITS[parsed.unit].label(parsed.value)}` : period;
}
