import { useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Loader, Modal, Radio, Stack, Stepper, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { useBackupsForObject, useRestoreRun, useTriggerRestore, useVms } from "@/api/hooks";
import type { RestoreMode, VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

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

export function RestoreWizardModal({ opened, onClose, vm }: RestoreWizardModalProps) {
  const [active, setActive] = useState(0);
  const [snapshotId, setSnapshotId] = useState<string | null>(null);
  const [vhdPath, setVhdPath] = useState<string | null>(null);
  const [mode, setMode] = useState<RestoreMode>("add");
  const [runId, setRunId] = useState<string | null>(null);

  const { data: backups, isLoading: backupsLoading } = useBackupsForObject("vm", vm?.name, opened && active === 0);
  const { data: vms } = useVms();
  const triggerRestore = useTriggerRestore();
  const { data: run } = useRestoreRun(runId ?? undefined, true);

  const vmFull = vms?.find((v) => v.name === vm?.name);

  useEffect(() => {
    if (!opened) {
      setActive(0);
      setSnapshotId(null);
      setVhdPath(null);
      setMode("add");
      setRunId(null);
    }
  }, [opened]);

  function handleTrigger() {
    if (!vm || !snapshotId || !vhdPath) return;
    triggerRestore.mutate(
      { vm_name: vm.name, snapshot_id: snapshotId, source_vhd_path: vhdPath, mode },
      {
        onSuccess: (result) => {
          setRunId(result.id);
          setActive(3);
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Restore konnte nicht gestartet werden."), color: "red" }),
      },
    );
  }

  const done = run?.status === "succeeded" || run?.status === "failed";

  return (
    <Modal
      opened={opened}
      onClose={done || active < 3 ? onClose : () => {}}
      title={`VM wiederherstellen: ${vm?.name ?? ""}`}
      size="lg"
      closeOnClickOutside={done || active < 3}
      closeOnEscape={done || active < 3}
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
            <Group justify="flex-end">
              <Button onClick={() => setActive(1)} disabled={!snapshotId}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="VHDX & Modus" description="Was wiederherstellen">
          <Stack mt="md">
            <Radio.Group value={vhdPath} onChange={setVhdPath} label="Welche VHDX soll wiederhergestellt werden?">
              <Stack gap="xs" mt="xs">
                {vmFull?.vhds.map((vhd) => (
                  <Radio key={vhd.full_path} value={vhd.full_path} label={vhd.name} />
                ))}
              </Stack>
            </Radio.Group>
            <Radio.Group value={mode} onChange={(v) => setMode(v as RestoreMode)} label="Was soll damit passieren?">
              <Stack gap="xs" mt="xs">
                <Radio value="add" label="Als zusätzliche Disk anhängen (kein Downtime, manueller Cleanup später möglich)" />
                <Radio value="replace" label="Laufende VHDX ersetzen (VM wird kurz gestoppt, alte Datei wird gelöscht)" />
              </Stack>
            </Radio.Group>
            {mode === "replace" && (
              <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
                Die aktuelle VHDX wird nach dem Umhängen unwiderruflich gelöscht, nicht nur umbenannt.
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(0)}>
                Zurück
              </Button>
              <Button onClick={() => setActive(2)} disabled={!vhdPath}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Start" description="Bestätigen">
          <Stack mt="md">
            <Text size="sm">
              VM <strong>{vm?.name}</strong>, VHDX <strong>{vhdPath?.split("\\").pop()}</strong>, Modus{" "}
              <strong>{mode === "add" ? "Zusatzdisk anhängen" : "Ersetzen"}</strong>.
            </Text>
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
          <Stack mt="md" gap="sm">
            {run?.steps.map((s) => (
              <Group key={s.step} gap="xs" wrap="nowrap" align="flex-start">
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
            ))}
            {run?.status === "succeeded" && (
              <Alert icon={<IconCheck size={16} />} color="green" variant="light">
                Restore erfolgreich abgeschlossen.
                {mode === "add" && " Denk daran, die Zusatzdisk später über den Cleanup in der Restore-Übersicht zu entfernen."}
              </Alert>
            )}
            {run?.status === "failed" && (
              <Alert icon={<IconX size={16} />} color="red" variant="light">
                {run.error_message}
              </Alert>
            )}
            {done && (
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
