import { useEffect, useState } from "react";
import { Alert, Badge, Button, Divider, Group, Loader, Modal, Radio, ScrollArea, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { useRecreateVm, useVmBackupRuns, useVmRecreateRun } from "@/api/hooks";
import type { VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

interface VmRecreateWizardModalProps {
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

export function VmRecreateWizardModal({ opened, onClose, vm }: VmRecreateWizardModalProps) {
  const [active, setActive] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const [recreateRunId, setRecreateRunId] = useState<string | null>(null);

  const { data: backupRuns, isLoading: runsLoading } = useVmBackupRuns(vm?.name, opened && active === 0);
  const recreateVm = useRecreateVm(vm?.name);
  const { data: run } = useVmRecreateRun(recreateRunId ?? undefined, true);

  const selectedRun = backupRuns?.find((r) => r.run_id === runId);
  const done = run?.status === "succeeded" || run?.status === "failed";

  useEffect(() => {
    if (!opened) {
      setActive(0);
      setRunId(null);
      setRecreateRunId(null);
    }
  }, [opened]);

  function handleStart() {
    if (!vm || !runId) return;
    recreateVm.mutate(runId, {
      onSuccess: (result) => {
        setRecreateRunId(result.id);
        setActive(2);
      },
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Neuerstellung konnte nicht gestartet werden."), color: "red" }),
    });
  }

  return (
    <Modal
      opened={opened}
      onClose={done || active < 2 ? onClose : () => {}}
      title={`VM neu erstellen: ${vm?.name ?? ""}`}
      size="xl"
      closeOnClickOutside={done || active < 2}
      closeOnEscape={done || active < 2}
    >
      <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light" mb="md">
        Diese VM existiert nicht mehr im Inventory. Aus dem gewählten Backup-Punkt wird eine komplett neue VM
        angelegt (Hardware/Netzwerk/VHDs gemäß gespeicherter Konfiguration). MAC-Adressen werden neu vergeben,
        PCI-Passthrough-Geräte werden nicht automatisch neu zugewiesen.
      </Alert>

      {active === 0 && (
        <Stack>
          {runsLoading && <Loader size="sm" />}
          {!runsLoading && backupRuns?.length === 0 && (
            <Text c="dimmed" size="sm">
              Keine Backup-Punkte für diese VM gefunden.
            </Text>
          )}
          <ScrollArea.Autosize mah={420} type="auto">
            <Radio.Group value={runId} onChange={setRunId}>
              <Stack gap="xs">
                {backupRuns?.map((r) => (
                  <Radio
                    key={r.run_id}
                    value={r.run_id}
                    label={
                      <Group gap="xs">
                        <Text size="sm">{new Date(r.created_at).toLocaleString("de-DE")}</Text>
                        <Badge color={r.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light" size="sm">
                          {r.consistency === "ApplicationConsistent" ? "App-konsistent" : "Crash-konsistent"}
                        </Badge>
                        <Badge color={r.restore_source === "secondary" ? "orange" : "blue"} variant="light" size="sm">
                          {r.restore_source === "secondary" ? "Sekundär" : "Primär"}
                        </Badge>
                        <Text size="xs" c="dimmed">
                          {r.policy_name} · {r.vhds.length} VHD(s)
                        </Text>
                      </Group>
                    }
                  />
                ))}
              </Stack>
            </Radio.Group>
          </ScrollArea.Autosize>
          <Group justify="flex-end">
            <Button onClick={() => setActive(1)} disabled={!runId}>
              Weiter
            </Button>
          </Group>
        </Stack>
      )}

      {active === 1 && selectedRun && (
        <Stack>
          <Text size="sm" fw={600}>
            Gespeicherte Konfiguration
          </Text>
          <Group gap="lg">
            <Text size="sm">{selectedRun.cpu_count ?? "-"} vCPU</Text>
            <Text size="sm">RAM: {formatBytes(selectedRun.memory_startup_bytes)}</Text>
            <Text size="sm">Generation {selectedRun.generation ?? "-"}</Text>
            <Text size="sm">Ziel-Host: {selectedRun.host_name ?? "-"}</Text>
            <Badge color={selectedRun.restore_source === "secondary" ? "orange" : "blue"} variant="light">
              {selectedRun.restore_source === "secondary" ? "Von Sekundärsystem (SnapMirror-Ziel)" : "Von Primärsystem"}
            </Badge>
          </Group>

          {selectedRun.network_adapters.length > 0 && (
            <>
              <Divider label="Netzwerkadapter" labelPosition="left" />
              <Group gap="xs">
                {selectedRun.network_adapters.map((n, i) => (
                  <Badge key={i} variant="light" color="grape">
                    {n.name}: {n.switch_name ?? "-"}
                    {n.vlan_id ? ` (VLAN ${n.vlan_id})` : ""}
                  </Badge>
                ))}
              </Group>
            </>
          )}

          <Divider label="VHDs" labelPosition="left" />
          <Table striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>CSV</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {selectedRun.vhds.map((v, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{v.name}</Table.Td>
                  <Table.Td>{formatBytes(v.size_bytes)}</Table.Td>
                  <Table.Td>{v.csv_name ?? "-"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          {selectedRun.pci_devices.length > 0 && (
            <Text size="xs" c="dimmed">
              PCI-Devices (werden NICHT automatisch neu zugewiesen): {selectedRun.pci_devices.join(", ")}
            </Text>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={() => setActive(0)}>
              Zurück
            </Button>
            <Button onClick={handleStart} loading={recreateVm.isPending}>
              VM neu erstellen
            </Button>
          </Group>
        </Stack>
      )}

      {active === 2 && (
        <Stack gap="sm">
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
              VM erfolgreich neu erstellt.
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
      )}
    </Modal>
  );
}
