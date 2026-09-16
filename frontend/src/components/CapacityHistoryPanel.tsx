import { LineChart } from "@mantine/charts";
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

function buildChartData(series: CapacitySeries[]): Record<string, number | string | null>[] {
  const dateSet = new Set<string>();
  series.forEach((s) => s.points.forEach((p) => dateSet.add(p.sampled_at.slice(0, 10))));
  const dates = [...dateSet].sort();
  return dates.map((date) => {
    const row: Record<string, number | string | null> = { date };
    series.forEach((s) => {
      const point = s.points.find((p) => p.sampled_at.slice(0, 10) === date);
      row[s.object_name] = point?.used_bytes != null ? point.used_bytes / BYTES_PER_GIB : null;
    });
    return row;
  });
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
  const totalPoints = (series ?? []).reduce((sum, s) => sum + s.points.length, 0);

  return (
    <Group align="flex-start" justify="space-between" wrap="nowrap" gap="md">
      <div style={{ flexGrow: 1, minWidth: 0 }}>
        {isLoading ? (
          <Skeleton height={180} />
        ) : totalPoints < 2 ? (
          <Alert color="gray" variant="light" icon={<IconInfoCircle size={16} />}>
            Noch zu wenige Messpunkte für einen Verlauf -- der Kapazitäts-Sammler läuft einmal täglich, ein Backfill
            vergangener Werte ist nicht möglich.
          </Alert>
        ) : (
          <LineChart
            h={220}
            data={chartData}
            dataKey="date"
            withLegend={(series ?? []).length > 1}
            series={(series ?? []).map((s, i) => ({ name: s.object_name, color: SERIES_COLORS[i % SERIES_COLORS.length] }))}
            valueFormatter={(v) => formatBytes(v * BYTES_PER_GIB)}
            curveType="linear"
            connectNulls
          />
        )}
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
