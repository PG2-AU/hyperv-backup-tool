interface SelectOption {
  value: string;
  label: string;
}

/** Mantine's Select/Combobox throws a hard render error (crashing the whole
 * page, no error boundary catches it) if `data` contains two options with
 * the same `value`. This is a real scenario for NetApp SnapMirror policies
 * and schedules: ONTAP commonly has SVM-scoped policies/schedules with the
 * identical name repeated across different SVMs on the same cluster (e.g.
 * every SVM getting its own "daily" schedule). Dedupe defensively before
 * handing any list to a Select, keeping the first occurrence. */
export function dedupeOptions<T extends SelectOption>(options: T[]): T[] {
  const seen = new Set<string>();
  return options.filter((o) => {
    if (seen.has(o.value)) return false;
    seen.add(o.value);
    return true;
  });
}
