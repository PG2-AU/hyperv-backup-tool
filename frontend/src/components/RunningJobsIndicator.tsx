import { useEffect, useState } from "react";
import { ActionIcon, Badge, Button, Divider, Group, Indicator, Loader, Popover, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconActivity, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { useCancelJobRun, useJobRun, useRunningJobRuns } from "@/api/hooks";
import { useAuthStore } from "@/store/authStore";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconMinus size={13} color="var(--mantine-color-gray-5)" />,
  running: <Loader size={11} />,
  success: <IconCheck size={13} color="var(--mantine-color-green-6)" />,
  error: <IconX size={13} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={13} color="var(--mantine-color-gray-5)" />,
};

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

// Zeigt die einzelnen Tasks eines laufenden Jobs live an (gepollt), damit
// "laeuft gerade" nicht mehr eine Blackbox ist -- man sieht genau, welcher
// Schritt (Checkpoint/Snapshot/SnapMirror je Ziel) gerade dran ist.
function RunningJobSteps({ runId }: { runId: string }) {
  const { data: run } = useJobRun(runId, true);

  if (!run || run.steps.length === 0) {
    return (
      <Text size="xs" c="dimmed" ml={20}>
        Wird gestartet…
      </Text>
    );
  }

  return (
    <Stack gap={3} ml={20}>
      {run.steps.map((s) => (
        <Group key={s.step} gap={6} wrap="nowrap" align="flex-start">
          {STEP_STATUS_ICON[s.status] ?? STEP_STATUS_ICON.pending}
          <Text size="xs" c={s.status === "error" ? "red" : "dimmed"} style={{ flex: 1 }}>
            {s.label}
            {s.status === "error" && s.message ? `: ${s.message}` : ""}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

function CancelJobButton({ runId, jobName }: { runId: string; jobName: string }) {
  const cancelRun = useCancelJobRun();

  function handleCancel() {
    confirmAction({
      title: "Backup-Lauf abbrechen",
      message:
        `'${jobName}' wirklich abbrechen? Wird nach dem aktuellen Schritt gestoppt (kann je nach Schritt bis zu ` +
        "ca. 1 Minute dauern), bereits erstellte Checkpoints werden aufgeräumt. Bereits erstellte Snapshots bleiben gültig.",
      confirmLabel: "Abbrechen",
      color: "red",
      onConfirm: () =>
        cancelRun.mutate(runId, {
          onSuccess: () => notifications.show({ title: "Abbruch angefordert", message: "", color: "orange" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Abbruch konnte nicht angefordert werden."), color: "red" }),
        }),
    });
  }

  return (
    <Button size="xs" variant="subtle" color="red" ml={20} onClick={handleCancel} loading={cancelRun.isPending} style={{ alignSelf: "flex-start" }}>
      Abbrechen
    </Button>
  );
}

export function RunningJobsIndicator() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canView = hasPermission("backup:view");
  const { data: runs } = useRunningJobRuns(canView);
  useNowTick(1000);

  if (!canView) return null;

  const count = runs?.length ?? 0;

  return (
    <Popover width={360} position="bottom-end" withArrow shadow="md">
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
        <Stack gap="sm">
          <Text size="sm" fw={600}>
            Laufende Backup-Jobs
          </Text>
          {count === 0 && (
            <Text size="xs" c="dimmed">
              Aktuell läuft kein Backup-Job.
            </Text>
          )}
          {runs?.map((run, idx) => (
            <Stack key={run.id} gap={4}>
              {idx > 0 && <Divider />}
              <Group justify="space-between" wrap="nowrap" gap="xs">
                <Text size="sm" truncate>
                  {run.job_name}
                  {run.resource_group_name && (
                    <Text span c="dimmed" size="xs">
                      {" "}
                      ({run.resource_group_name})
                    </Text>
                  )}
                </Text>
                <Badge color="blue" variant="light">
                  {formatElapsed(run.started_at)}
                </Badge>
              </Group>
              {run.cancel_requested_at ? (
                <Text size="xs" c="orange" ml={20}>
                  Abbruch angefordert – wird nach dem aktuellen Schritt gestoppt…
                </Text>
              ) : (
                <CancelJobButton runId={run.id} jobName={run.job_name} />
              )}
              <RunningJobSteps runId={run.id} />
            </Stack>
          ))}
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}
