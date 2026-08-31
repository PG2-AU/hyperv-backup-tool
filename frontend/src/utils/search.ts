// Generische "ueber alle Spalten"-Suche: durchsucht saemtliche (auch
// verschachtelte) Werte eines Objekts als Text, statt wie die VM-Suche in
// Inventory nur ein einzelnes Feld (z.B. Name) zu pruefen. Reicht fuer die
// flachen DTOs aus der Storage-Seite (SVMs/Volumes/LUNs/IGroups) voll aus.
function flatten(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.map(flatten).join(" ");
  if (typeof value === "object") return Object.values(value as Record<string, unknown>).map(flatten).join(" ");
  return String(value);
}

export function matchesAllColumns<T extends object>(row: T, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return Object.values(row).some((v) => flatten(v).toLowerCase().includes(needle));
}
