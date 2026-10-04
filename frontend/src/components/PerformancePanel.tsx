import { LineChart } from "@mantine/charts";
import { Alert, Group, SegmentedControl, SimpleGrid, Skeleton, Stack, Text } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { useMemo, useState } from "react";

import { usePerformanceHistory, usePerformanceOverview } from "@/api/hooks.performance";
import type { PerfInterval, PerfObjectType, PerfPoint, PerfRow } from "@/api/hooks.performance";
import { apiErrorMessage } from "@/utils/errors";

// Performance-Verlauf eines Volumes oder einer LUN (Backlog #80 Stufe 1):
// IOPS, Latenz und Durchsatz je Lesen/Schreiben aus ONTAPs eigener Historie.

const INTERVALS: { value: PerfInterval; label: string }[] = [
  { value: "1h", label: "1 Std." },
  { value: "1d", label: "1 Tag" },
  { value: "1w", label: "1 Woche" },
  { value: "1m", label: "1 Monat" },
  { value: "1y", label: "1 Jahr" },
];

const NUMBER = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });
const DECIMAL = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

export function formatIops(value?: number | null): string {
  return value == null ? "–" : NUMBER.format(value);
}

export function formatLatency(value?: number | null): string {
  return value == null ? "–" : `${DECIMAL.format(value)} ms`;
}

export function formatThroughput(bytesPerSecond?: number | null): string {
  if (bytesPerSecond == null) return "–";
  const mb = bytesPerSecond / 1024 ** 2;
  if (mb >= 1) return `${new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 }).format(mb)} MB/s`;
  return `${NUMBER.format(bytesPerSecond / 1024)} KB/s`;
}

/** Latenz-Ampel: erst ab etwas Last aussagekraeftig (bei fast 0 IOPS sind
 *  einzelne langsame Zugriffe Rauschen). */
export function latencyColor(latencyMs?: number | null, iops?: number | null): string | undefined {
  if (latencyMs == null || (iops ?? 0) < 10) return undefined;
  if (latencyMs >= 20) return "red";
  if (latencyMs >= 10) return "orange";
  return undefined;
}

function timeLabel(iso: string, interval: PerfInterval): string {
  const d = new Date(iso);
  if (interval === "1h" || interval === "1d") return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  if (interval === "1w") return `${d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })} ${d.getHours()}:00`;
  if (interval === "1m") return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
  return d.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

function chartRows(points: PerfPoint[], interval: PerfInterval) {
  return points.map((p) => ({
    time: timeLabel(p.timestamp, interval),
    "IOPS Lesen": p.iops_read ?? null,
    "IOPS Schreiben": p.iops_write ?? null,
    "Latenz Lesen": p.latency_read_ms ?? null,
    "Latenz Schreiben": p.latency_write_ms ?? null,
    "Durchsatz Lesen": p.throughput_read != null ? p.throughput_read / 1024 ** 2 : null,
    "Durchsatz Schreiben": p.throughput_write != null ? p.throughput_write / 1024 ** 2 : null,
  }));
}

function Chart({ title, data, keys, unit, format }: {
  title: string;
  data: Record<string, string | number | null>[];
  keys: [string, string];
  unit: string;
  format: (v: number) => string;
}) {
  return (
    <div>
      <Text size="xs" fw={600} c="dimmed" mb={4}>
        {title} ({unit})
      </Text>
      <LineChart
        h={170}
        data={data}
        dataKey="time"
        series={[
          { name: keys[0], label: "Lesen", color: "blue.6" },
          { name: keys[1], label: "Schreiben", color: "orange.6" },
        ]}
        withDots={false}
        curveType="linear"
        strokeWidth={1.5}
        connectNulls
        valueFormatter={format}
        withLegend
        legendProps={{ verticalAlign: "bottom", height: 24 }}
        xAxisProps={{ minTickGap: 40 }}
      />
    </div>
  );
}

export function PerformancePanel({
  netappClusterId,
  objectType,
  uuid,
}: {
  netappClusterId?: string | null;
  objectType?: PerfObjectType | null;
  uuid?: string | null;
}) {
  const [interval, setRange] = useState<PerfInterval>("1d");
  const { data: points, isLoading, error } = usePerformanceHistory({ netappClusterId, objectType, uuid }, interval);
  const data = useMemo(() => chartRows(points ?? [], interval), [points, interval]);
  const mb = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });

  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          Quelle: ONTAP-Statistik ({objectType === "lun" ? "LUN" : "Volume"}), Mittelwerte je Messpunkt
        </Text>
        <SegmentedControl size="xs" value={interval} onChange={(v) => setRange(v as PerfInterval)} data={INTERVALS} />
      </Group>
      {error ? (
        <Alert color="red" variant="light">
          {apiErrorMessage(error, "Verlauf konnte nicht geladen werden.")}
        </Alert>
      ) : isLoading ? (
        <Skeleton height={170} />
      ) : data.length < 2 ? (
        <Alert color="gray" variant="light" icon={<IconInfoCircle size={16} />}>
          Für diesen Zeitraum liefert ONTAP keine Messwerte.
        </Alert>
      ) : (
        <SimpleGrid cols={{ base: 1, lg: 3 }}>
          <Chart title="IOPS" unit="Ops/s" data={data} keys={["IOPS Lesen", "IOPS Schreiben"]} format={(v) => NUMBER.format(v)} />
          <Chart title="Latenz" unit="ms" data={data} keys={["Latenz Lesen", "Latenz Schreiben"]} format={(v) => DECIMAL.format(v)} />
          <Chart title="Durchsatz" unit="MB/s" data={data} keys={["Durchsatz Lesen", "Durchsatz Schreiben"]} format={(v) => mb.format(v)} />
        </SimpleGrid>
      )}
    </Stack>
  );
}

/** Fuer Inventory (CSV/SMB3): sucht die zugehoerige LUN bzw. das Volume in
 *  der Performance-Uebersicht und zeigt dessen Verlauf. */
export function HyperVPerformancePanel({ kind, name, hypervClusterName }: {
  kind: "csv" | "smb_share";
  name: string;
  hypervClusterName?: string | null;
}) {
  const { data, isLoading, error } = usePerformanceOverview();
  if (isLoading) return <Skeleton height={170} />;
  if (error) return <Alert color="red" variant="light">{apiErrorMessage(error, "Performance konnte nicht geladen werden.")}</Alert>;
  const row: PerfRow | undefined = data?.rows.find(
    (r) => r.kind === kind && r.name.toLowerCase() === name.toLowerCase() && (!hypervClusterName || r.hyperv_cluster_name === hypervClusterName),
  );
  if (!row?.object_uuid || !row.netapp_cluster_id) {
    return (
      <Alert color="gray" variant="light" icon={<IconInfoCircle size={16} />}>
        {row?.note ?? "Keine NetApp-Zuordnung gefunden."}
      </Alert>
    );
  }
  return <PerformancePanel netappClusterId={row.netapp_cluster_id} objectType={row.object_type} uuid={row.object_uuid} />;
}
