import { Badge, Group, Paper, Text } from "@mantine/core";

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

export function DistributionCard({ label, items }: { label: string; items: DistributionEntry[] }) {
  return (
    <Paper withBorder p="xs" miw={180}>
      <Text size="xs" c="dimmed" tt="uppercase" fw={700} mb={4}>
        {label}
      </Text>
      <Group gap={4}>
        {items.length === 0 && (
          <Text size="sm" c="dimmed">
            -
          </Text>
        )}
        {items.map((item) => (
          <Badge key={item.key} variant="light" color={item.color ?? "gray"}>
            {item.key}: {item.count}
          </Badge>
        ))}
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
