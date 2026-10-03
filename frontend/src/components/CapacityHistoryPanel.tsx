import { AreaChart } from "@mantine/charts";
import { Alert, Group, SegmentedControl, Skeleton, Text } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { useMemo, useState } from "react";

import { useCapacityHistory } from "@/api/hooks";
import type { CapacityObjectType, CapacitySeries } from "@/api/types";
import { formatBytes } from "@/utils/format";

const MONTH_OPTIONS = [
  { label: "1 Monat", value: "1" },
  { label: "3 Monate", value: "3" },
  { label: "6 Monate", value: "6" },
  { label: "12 Monate", value: "12" },
];

const SERIES_COLORS = ["blue.6", "teal.6", "orange.6", "grape.6", "red.6", "yellow.6", "cyan.6", "lime.6"];

const BYTES_PER_GIB = 1024 ** 3;

// Bei Volumes zusaetzlich die Snapshot-Belegung als eigene Flaeche je Serie
// (Backlog #67) -- nur fuer Serien, die den Wert haben (Messpunkte vor
// Einfuehrung der Snapshot-Erfassung haben ihn nicht). Nicht gestapelt:
// Snapshots sind ein Teil ("davon") der belegten Menge, die lila Flaeche
// liegt deshalb innerhalb der blauen und wird darueber gezeichnet.
function snapshotKey(name: string): string {
  return `${name} – davon Snapshots`;
}

function hasSnapshots(s: CapacitySeries): boolean {
  return s.points.some((p) => p.snapshot_used_bytes != null);
}

// Prognose 4 Wochen voraus (Backlog #79): eigene, gestrichelte Serie je
// Objekt. Nutzer-Vorgabe 2026-10-03: nicht 28 einzelne Punkte, sondern nur
// EIN Prognosepunkt (Wert in 28 Tagen), optisch immer im festen Abstand von
// etwa 7 Tagen rechts vom letzten echten Messpunkt -- dazwischen leere
// Platzhalter-Zeilen (die x-Achse ist kategorial: jede Zeile = ein Schritt).
// Die Linie startet am letzten Messpunkt, damit sie nahtlos anschliesst.
const FORECAST_GAP_SLOTS = 7;

function forecastKey(name: string): string {
  return `${name} – Prognose`;
}

function forecastLabel(series: CapacitySeries[]): string | null {
  const end = series.map((s) => s.forecast?.points[s.forecast.points.length - 1]?.sampled_at).find(Boolean);
  return end ? `Prognose ${new Date(end).toLocaleDateString("de-DE")}` : null;
}

function buildChartData(series: CapacitySeries[]): Record<string, number | string | null>[] {
  const dateSet = new Set<string>();
  series.forEach((s) => s.points.forEach((p) => dateSet.add(p.sampled_at.slice(0, 10))));
  const dates = [...dateSet].sort();
  const lastDate = dates[dates.length - 1];
  const rows: Record<string, number | string | null>[] = dates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    series.forEach((s) => {
      const point = s.points.find((p) => p.sampled_at.slice(0, 10) === date);
      row[s.object_name] = point?.used_bytes != null ? point.used_bytes / BYTES_PER_GIB : null;
      if (hasSnapshots(s)) {
        row[snapshotKey(s.object_name)] = point?.snapshot_used_bytes != null ? point.snapshot_used_bytes / BYTES_PER_GIB : null;
      }
      if (s.forecast) {
        // Startpunkt der Prognoselinie = letzter echter Messwert.
        row[forecastKey(s.object_name)] = date === lastDate && point?.used_bytes != null ? point.used_bytes / BYTES_PER_GIB : null;
      }
    });
    return row;
  });

  const label = forecastLabel(series);
  if (label && rows.length > 0) {
    // Leere Platzhalter mit unsichtbaren, eindeutigen Achsenbeschriftungen.
    for (let i = 1; i < FORECAST_GAP_SLOTS; i++) rows.push({ date: "\u200b".repeat(i) });
    const end: Record<string, number | string | null> = { date: label };
    series.forEach((s) => {
      const last = s.forecast?.points[s.forecast.points.length - 1];
      if (last?.used_bytes != null) end[forecastKey(s.object_name)] = last.used_bytes / BYTES_PER_GIB;
    });
    rows.push(end);
  }
  return rows;
}

function forecastText(s: CapacitySeries): { text: string; color: string } {
  const f = s.forecast;
  if (!f) return { text: "Prognose: noch zu wenige Messpunkte (mind. 5 Tage über 1 Woche)", color: "dimmed" };
  const perDay = `${f.growth_bytes_per_day >= 0 ? "+" : "−"}${formatBytes(Math.abs(f.growth_bytes_per_day))}/Tag`;
  if (f.days_to_full == null || f.growth_bytes_per_day <= 0) return { text: `Prognose: wächst nicht (${perDay})`, color: "dimmed" };
  const days = Math.max(0, Math.floor(f.days_to_full));
  const when = f.full_at ? new Date(f.full_at).toLocaleDateString("de-DE") : "";
  if (days <= f.horizon_days)
    return {
      text: days === 0 ? `Prognose: bereits voll (${perDay})` : `Prognose: voll in ca. ${days} Tagen, um den ${when} (${perDay})`,
      color: "red",
    };
  return {
    text: `Prognose: in den nächsten ${Math.round(f.horizon_days / 7)} Wochen nicht voll -- bei gleichem Trend in ca. ${days} Tagen (${perDay})`,
    color: "dimmed",
  };
}

export function CapacityHistoryPanel({
  objectType,
  clusterId,
  vmUuid,
  name,
  uuid,
}: {
  objectType: CapacityObjectType;
  clusterId: string | null | undefined;
  vmUuid?: string | null;
  name?: string | null;
  uuid?: string | null;
}) {
  const [months, setMonths] = useState("3");
  const { data: series, isLoading } = useCapacityHistory(
    { objectType, clusterId, months: Number(months), vmUuid, name, uuid },
    true,
  );

  const chartData = useMemo(() => buildChartData(series ?? []), [series]);
  const chartSeries = useMemo(
    () =>
      (series ?? []).flatMap((s, i) => {
        const base = {
          name: s.object_name,
          label: hasSnapshots(s) ? `${s.object_name} (belegt)` : s.object_name,
          color: SERIES_COLORS[i % SERIES_COLORS.length],
        };
        const extra = [];
        if (hasSnapshots(s)) extra.push({ name: snapshotKey(s.object_name), label: "davon Snapshots", color: "grape.6" });
        if (s.forecast)
          extra.push({ name: forecastKey(s.object_name), label: `${s.object_name} – Prognose`, color: base.color, strokeDasharray: "6 4" });
        return [base, ...extra];
      }),
    [series],
  );
  const totalPoints = (series ?? []).reduce((sum, s) => sum + s.points.length, 0);
  // Kapazitaetslinie nur bei genau einem Objekt (bei mehreren VHDs waeren es
  // mehrere unterschiedliche Grenzen).
  const single = series?.length === 1 ? series[0] : null;
  const capacityBytes = single?.forecast?.capacity_bytes ?? single?.points[single.points.length - 1]?.capacity_bytes ?? null;

  return (
    <Group align="flex-start" justify="space-between" wrap="nowrap" gap="md">
      <div style={{ flexGrow: 1, minWidth: 0 }}>
        {isLoading ? (
          <Skeleton height={180} />
        ) : totalPoints < 2 ? (
          <Alert color="gray" variant="light" icon={<IconInfoCircle size={16} />}>
            Noch zu wenige Messpunkte für einen Verlauf -- der Kapazitäts-Sammler läuft einmal täglich, ein Backfill vergangener
            Werte ist nicht möglich.
          </Alert>
        ) : (
          <AreaChart
            h={220}
            data={chartData}
            dataKey="date"
            type="default"
            withLegend={chartSeries.length > 1}
            series={chartSeries}
            areaProps={(item) => (item.name.endsWith("– Prognose") ? { fillOpacity: 0 } : {})}
            referenceLines={
              capacityBytes
                ? [{ y: capacityBytes / BYTES_PER_GIB, label: `Kapazität ${formatBytes(capacityBytes)}`, color: "red.6" }]
                : undefined
            }
            valueFormatter={(v) => formatBytes(v * BYTES_PER_GIB)}
            curveType="linear"
            withGradient={false}
            fillOpacity={0.35}
            connectNulls
          />
        )}
        {!isLoading &&
          totalPoints >= 2 &&
          (series ?? []).map((s) => {
            const { text, color } = forecastText(s);
            return (
              <Text key={s.object_key} size="xs" c={color} mt={4}>
                {(series?.length ?? 0) > 1 ? `${s.object_name}: ` : ""}
                {text}
              </Text>
            );
          })}
      </div>
      <div>
        <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb={4}>
          Zeitraum
        </Text>
        <SegmentedControl size="xs" value={months} onChange={setMonths} data={MONTH_OPTIONS} orientation="vertical" />
      </div>
    </Group>
  );
}
