const UNITS = ["Bytes", "KB", "MB", "GB", "TB", "PB"] as const;

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || bytes < 0) return "-";
  if (bytes === 0) return "0 Bytes";

  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), UNITS.length - 1);
  const value = bytes / 1024 ** exponent;
  return `${value.toFixed(exponent === 0 ? 0 : 1)} ${UNITS[exponent]}`;
}
