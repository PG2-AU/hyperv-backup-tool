import { Box, Group, Paper, Stack, Text } from "@mantine/core";

export function StatRibbon({ children }: { children: React.ReactNode }) {
  return (
    <Group gap="sm" mb="md" wrap="wrap" align="stretch">
      {children}
    </Group>
  );
}

export function StatCard({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Paper withBorder p="xs" miw={130}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
        {label}
      </Text>
      <Text size="lg" fw={700}>
        {value}
      </Text>
    </Paper>
  );
}

export interface DistributionEntry {
  key: string;
  count: number;
  color?: string;
}

// Fixed categorical order (never cycled/reassigned by rank) so a given slot
// always maps to the same hue across renders.
const CATEGORICAL_COLORS = ["blue", "orange", "teal", "yellow", "pink", "green", "violet", "red"];

function colorForIndex(item: DistributionEntry, index: number): string {
  const name = item.color ?? CATEGORICAL_COLORS[index % CATEGORICAL_COLORS.length];
  return `var(--mantine-color-${name}-6)`;
}

function Donut({ items }: { items: DistributionEntry[] }) {
  const total = items.reduce((sum, i) => sum + i.count, 0);
  const radius = 28;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  return (
    <svg width={72} height={72} viewBox="0 0 72 72" style={{ flexShrink: 0 }}>
      <g transform="rotate(-90 36 36)">
        {total === 0 ? (
          <circle cx={36} cy={36} r={radius} fill="none" stroke="var(--mantine-color-gray-3)" strokeWidth={11} />
        ) : (
          items.map((item, i) => {
            const segLen = (item.count / total) * circumference;
            const gap = items.length > 1 ? 2 : 0;
            const dash = Math.max(segLen - gap, 0.001);
            const dashOffset = -offset;
            offset += segLen;
            return (
              <circle
                key={item.key}
                cx={36}
                cy={36}
                r={radius}
                fill="none"
                stroke={colorForIndex(item, i)}
                strokeWidth={11}
                strokeDasharray={`${dash} ${circumference - dash}`}
                strokeDashoffset={dashOffset}
              />
            );
          })
        )}
      </g>
    </svg>
  );
}

export function DistributionCard({ label, items }: { label: string; items: DistributionEntry[] }) {
  const total = items.reduce((sum, i) => sum + i.count, 0);
  const capped = items.slice(0, 8);
  const overflowCount = items.slice(8).reduce((sum, i) => sum + i.count, 0);
  const legendItems = overflowCount > 0 ? [...capped, { key: "Andere", count: overflowCount }] : capped;

  return (
    <Paper withBorder p="xs" miw={230}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb={6}>
        {label}
      </Text>
      {total === 0 ? (
        <Text size="sm" c="dimmed">
          -
        </Text>
      ) : (
        <Group gap="sm" wrap="nowrap" align="center">
          <Donut items={legendItems} />
          <Stack gap={2}>
            {legendItems.map((item, i) => (
              <Group key={item.key} gap={6} wrap="nowrap">
                <Box w={8} h={8} style={{ borderRadius: 2, background: colorForIndex(item, i), flexShrink: 0 }} />
                <Text size="xs" style={{ whiteSpace: "nowrap" }}>
                  {item.key}: {item.count}
                </Text>
              </Group>
            ))}
          </Stack>
        </Group>
      )}
    </Paper>
  );
}

export function CapacityBarCard({
  label,
  used,
  total,
  formatValue,
}: {
  label: string;
  used: number;
  total: number;
  formatValue: (bytes: number) => string;
}) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  return (
    <Paper withBorder p="xs" miw={230}>
      <Group justify="space-between" mb={4}>
        <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
          {label}
        </Text>
        <Text size="xs" c="dimmed">
          {pct.toFixed(0)}%
        </Text>
      </Group>
      <Box h={10} style={{ borderRadius: 5, background: "var(--mantine-color-gray-2)", overflow: "hidden" }}>
        <Box h="100%" style={{ width: `${pct}%`, background: "var(--mantine-color-blue-6)", borderRadius: 5 }} />
      </Box>
      <Group gap={12} mt={6}>
        <Group gap={4}>
          <Box w={8} h={8} style={{ borderRadius: 2, background: "var(--mantine-color-blue-6)" }} />
          <Text size="xs" c="dimmed">
            Belegt: {formatValue(used)}
          </Text>
        </Group>
        <Group gap={4}>
          <Box w={8} h={8} style={{ borderRadius: 2, background: "var(--mantine-color-gray-3)" }} />
          <Text size="xs" c="dimmed">
            Gesamt: {formatValue(total)}
          </Text>
        </Group>
      </Group>
    </Paper>
  );
}

export function groupCount<T>(items: T[] | undefined, keyFn: (item: T) => string | null | undefined, fallback = "Unbekannt"): DistributionEntry[] {
  const counts = new Map<string, number>();
  for (const item of items ?? []) {
    const key = keyFn(item) || fallback;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([key, count]) => ({ key, count }))
    .sort((a, b) => b.count - a.count);
}
