import { useEffect, useState } from "react";
import { ActionIcon, Badge, Group, Indicator, Popover, Stack, Text, Tooltip } from "@mantine/core";
import { IconActivity } from "@tabler/icons-react";

import { useRunningJobRuns } from "@/api/hooks";
import { useAuthStore } from "@/store/authStore";

function useNowTick(intervalMs: number) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
}

function formatElapsed(startedAt: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

export function RunningJobsIndicator() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canView = hasPermission("backup:view");
  const { data: runs } = useRunningJobRuns(canView);
  useNowTick(1000);

  if (!canView) return null;

  const count = runs?.length ?? 0;

  return (
    <Popover width={320} position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Tooltip label="Laufende Backup-Jobs">
          <Indicator color="blue" label={count} size={16} disabled={count === 0} offset={4}>
            <ActionIcon variant="default" size="lg">
              <IconActivity size={18} />
            </ActionIcon>
          </Indicator>
        </Tooltip>
      </Popover.Target>
      <Popover.Dropdown>
        <Stack gap="xs">
          <Text size="sm" fw={600}>
            Laufende Backup-Jobs
          </Text>
          {count === 0 && (
            <Text size="xs" c="dimmed">
              Aktuell läuft kein Backup-Job.
            </Text>
          )}
          {runs?.map((run) => (
            <Group key={run.id} justify="space-between" wrap="nowrap" gap="xs">
              <Text size="sm" truncate>
                {run.job_name}
              </Text>
              <Badge color="blue" variant="light">
                {formatElapsed(run.started_at)}
              </Badge>
            </Group>
          ))}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
