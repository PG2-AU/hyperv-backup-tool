import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Progress,
  Radio,
  ScrollArea,
  Stack,
  Stepper,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { useBackupsForObject, useRestoreRun, useTriggerRestore, useVms } from "@/api/hooks";
import type { RestoreMode, RestoreRun, VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

interface RestoreWizardModalProps {
  opened: boolean;
  onClose: () => void;
  vm: VmWithBackups | null;
}

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

interface FinishedRun {
  vhdPath: string;
  run: RestoreRun;
}

export function RestoreWizardModal({ opened, onClose, vm }: RestoreWizardModalProps) {
  const [active, setActive] = useState(0);
  const [snapshotId, setSnapshotId] = useState<string | null>(null);
  const [selectedVhdPaths, setSelectedVhdPaths] = useState<string[]>([]);
  const [mode, setMode] = useState<RestoreMode>("add");

  const [queue, setQueue] = useState<string[]>([]);
  const [currentVhdPath, setCurrentVhdPath] = useState<string | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [finishedRuns, setFinishedRuns] = useState<FinishedRun[]>([]);

  const { data: backups, isLoading: backupsLoading } = useBackupsForObject("vm", vm?.name, opened && active === 0);
  const { data: vms } = useVms();
  const triggerRestore = useTriggerRestore();
  const { data: run } = useRestoreRun(currentRunId ?? undefined, true);

  const vmFull = vms?.find((v) => v.name === vm?.name);
  const totalCount = finishedRuns.length + (currentVhdPath ? 1 : 0) + queue.length;

  useEffect(() => {
    if (!opened) {
      setActive(0);
      setSnapshotId(null);
      setSelectedVhdPaths([]);
      setMode("add");
      setQueue([]);
      setCurrentVhdPath(null);
      setCurrentRunId(null);
      setFinishedRuns([]);
    }
  }, [opened]);

  // Verarbeitet die Warteschlange sequenziell: startet den naechsten Restore
  // erst, wenn kein anderer mehr aktiv ist (currentVhdPath === null). Der
  // Restore-Proxy-Host/die Igroup werden pro Lauf exklusiv genutzt, parallele
  // Laeufe waeren riskant -- daher bewusst nacheinander statt gleichzeitig.
  useEffect(() => {
    if (currentVhdPath !== null || queue.length === 0 || !vm || !snapshotId) return;
    const [next, ...rest] = queue;
    setQueue(rest);
    setCurrentVhdPath(next);
    triggerRestore.mutate(
      { vm_name: vm.name, snapshot_id: snapshotId, source_vhd_path: next, mode },
      {
        onSuccess: (result) => setCurrentRunId(result.id),
        onError: (err) => {
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Restore konnte nicht gestartet werden."), color: "red" });
          setCurrentVhdPath(null);
        },
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentVhdPath, queue, vm, snapshotId, mode]);

  useEffect(() => {
    if (!run || (run.status !== "succeeded" && run.status !== "failed")) return;
    setFinishedRuns((prev) => {
      if (prev.some((f) => f.run.id === run.id)) return prev;
      return [...prev, { vhdPath: currentVhdPath ?? run.source_vhd_path, run }];
    });
    setCurrentRunId(null);
    setCurrentVhdPath(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status, run?.id]);

  function handleTrigger() {
    if (!vm || !snapshotId || selectedVhdPaths.length === 0) return;
    setFinishedRuns([]);
    setCurrentRunId(null);
    setCurrentVhdPath(null);
    setQueue(selectedVhdPaths);
    setActive(3);
  }

  const batchDone = currentRunId === null && queue.length === 0 && finishedRuns.length > 0;
  const anyFailed = finishedRuns.some((f) => f.run.status === "failed");

  return (
    <Modal
      opened={opened}
      onClose={batchDone || active < 3 ? onClose : () => {}}
      title={`VM wiederherstellen: ${vm?.name ?? ""}`}
      size="xl"
      closeOnClickOutside={batchDone || active < 3}
      closeOnEscape={batchDone || active < 3}
    >
      <Stepper active={active} size="sm">
        <Stepper.Step label="Snapshot" description="Zeitpunkt wählen">
          <Stack mt="md">
            {backupsLoading && <Loader size="sm" />}
            {!backupsLoading && backups?.length === 0 && (
              <Text c="dimmed" size="sm">
                Keine vorhandenen Backups für diese VM.
              </Text>
            )}
            <ScrollArea.Autosize mah={420} type="auto">
              <Radio.Group value={snapshotId} onChange={setSnapshotId}>
                <Stack gap="xs">
                  {backups?.map((b) => (
                    <Radio
                      key={b.id}
                      value={b.id}
                      label={
                        <Group gap="xs">
                          <Text size="sm">{new Date(b.created_at).toLocaleString("de-DE")}</Text>
                          <Badge color={b.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light" size="sm">
                            {b.consistency === "ApplicationConsistent" ? "App-konsistent" : "Crash-konsistent"}
                          </Badge>
                          <Text size="xs" c="dimmed">
                            {b.policy_name}
                          </Text>
                        </Group>
                      }
                    />
                  ))}
                </Stack>
              </Radio.Group>
            </ScrollArea.Autosize>
            <Group justify="flex-end">
              <Button onClick={() => setActive(1)} disabled={!snapshotId}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="VHDX & Modus" description="Was wiederherstellen">
          <Stack mt="md">
            <Checkbox.Group
              value={selectedVhdPaths}
              onChange={setSelectedVhdPaths}
              label="Welche VHDX sollen wiederhergestellt werden? (Mehrfachauswahl möglich)"
            >
              <Stack gap="xs" mt="xs">
                {vmFull?.vhds.map((vhd) => (
                  <Checkbox
                    key={vhd.full_path}
                    value={vhd.full_path}
                    label={
                      <Group gap="xs">
                        <Text size="sm">{vhd.name}</Text>
                        <Text size="xs" c="dimmed">
                          ({formatBytes(vhd.size_bytes)})
                        </Text>
                      </Group>
                    }
                  />
                ))}
              </Stack>
            </Checkbox.Group>
            <Radio.Group value={mode} onChange={(v) => setMode(v as RestoreMode)} label="Was soll damit passieren?">
              <Stack gap="xs" mt="xs">
                <Radio value="add" label="Als zusätzliche Disk anhängen (kein Downtime, manueller Cleanup später möglich)" />
                <Radio value="replace" label="Laufende VHDX ersetzen (VM wird kurz gestoppt, alte Datei wird gelöscht)" />
              </Stack>
            </Radio.Group>
            {mode === "replace" && (
              <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
                Die aktuelle VHDX wird nach dem Umhängen unwiderruflich gelöscht, nicht nur umbenannt.
                {selectedVhdPaths.length > 1 && " Bei mehreren VHDX wird die VM dafür pro Datei kurz gestoppt und wieder gestartet."}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(0)}>
                Zurück
              </Button>
              <Button onClick={() => setActive(2)} disabled={selectedVhdPaths.length === 0}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Start" description="Bestätigen">
          <Stack mt="md">
            <Text size="sm">
              VM <strong>{vm?.name}</strong>, Modus <strong>{mode === "add" ? "Zusatzdisk anhängen" : "Ersetzen"}</strong>.
            </Text>
            <Stack gap={4}>
              {selectedVhdPaths.map((p) => (
                <Text key={p} size="sm" ff="monospace">
                  • {p.split("\\").pop()}
                </Text>
              ))}
            </Stack>
            {selectedVhdPaths.length > 1 && (
              <Text size="xs" c="dimmed">
                Die {selectedVhdPaths.length} VHDX werden nacheinander wiederhergestellt.
              </Text>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(1)}>
                Zurück
              </Button>
              <Button onClick={handleTrigger} loading={triggerRestore.isPending}>
                Restore starten
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Fortschritt" description="Live-Status">
          <Stack mt="md" gap="md">
            {totalCount > 1 && (
              <Text size="sm" fw={600}>
                {finishedRuns.length} von {totalCount} VHDX verarbeitet
              </Text>
            )}

            {finishedRuns.map((f) => (
              <Group key={f.run.id} gap="xs" wrap="nowrap">
                {f.run.status === "succeeded" ? (
                  <IconCheck size={16} color="var(--mantine-color-green-6)" />
                ) : (
                  <IconX size={16} color="var(--mantine-color-red-6)" />
                )}
                <Text size="sm" ff="monospace">
                  {f.vhdPath.split("\\").pop()}
                </Text>
                {f.run.status === "failed" && (
                  <Text size="xs" c="red">
                    {f.run.error_message}
                  </Text>
                )}
              </Group>
            ))}

            {run && (
              <Stack gap="sm" p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
                <Text size="sm" fw={600} ff="monospace">
                  {currentVhdPath?.split("\\").pop()}
                </Text>
                {run.steps.map((s) => (
                  <Stack key={s.step} gap={4}>
                    <Group gap="xs" wrap="nowrap" align="flex-start">
                      {STEP_STATUS_ICON[s.status]}
                      <Stack gap={0} style={{ flex: 1 }}>
                        <Text size="sm" fw={600}>
                          {s.label}
                        </Text>
                        {s.status === "error" && (
                          <Text size="xs" c="red">
                            {s.message}
                          </Text>
                        )}
                      </Stack>
                    </Group>
                    {s.status === "running" && !!s.progress_total && (
                      <Stack gap={2} ml={24}>
                        <Progress value={Math.min(100, (100 * (s.progress_current ?? 0)) / s.progress_total)} size="sm" animated />
                        <Text size="xs" c="dimmed">
                          {formatBytes(s.progress_current)} / {formatBytes(s.progress_total)}
                        </Text>
                      </Stack>
                    )}
                  </Stack>
                ))}
              </Stack>
            )}

            {batchDone && !anyFailed && (
              <Alert icon={<IconCheck size={16} />} color="green" variant="light">
                {totalCount > 1 ? "Alle Restores erfolgreich abgeschlossen." : "Restore erfolgreich abgeschlossen."}
                {mode === "add" && " Denk daran, die Zusatzdisk(en) später über den Cleanup in der Restore-Übersicht zu entfernen."}
              </Alert>
            )}
            {batchDone && anyFailed && (
              <Alert icon={<IconX size={16} />} color="red" variant="light">
                Mindestens ein Restore ist fehlgeschlagen, siehe Details oben.
              </Alert>
            )}
            {batchDone && (
              <Group justify="flex-end">
                <Button onClick={onClose}>Schließen</Button>
              </Group>
            )}
          </Stack>
        </Stepper.Step>
      </Stepper>
    </Modal>
  );
}
