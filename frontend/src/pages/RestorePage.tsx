import { useState } from "react";
import { Badge, Button, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDatabaseImport, IconTrash } from "@tabler/icons-react";

import { useCleanupRestoreRun, useRestoreRuns, useVmsWithBackups } from "@/api/hooks";
import { RestoreWizardModal } from "@/components/RestoreWizardModal";
import type { VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const STATE_COLOR: Record<string, string> = { Running: "green", Off: "gray", Saved: "yellow" };

export function RestorePage() {
  const { data: vms } = useVmsWithBackups();
  const { data: runs } = useRestoreRuns();
  const cleanupRun = useCleanupRestoreRun();
  const [wizardVm, setWizardVm] = useState<VmWithBackups | null>(null);

  const cleanupPending = runs?.filter((r) => r.cleanup_needed) ?? [];

  function handleCleanup(runId: string, vmName: string) {
    if (!window.confirm(`Zusatzdisk für '${vmName}' abhängen und löschen?`)) return;
    cleanupRun.mutate(runId, {
      onSuccess: () => notifications.show({ title: "Cleanup abgeschlossen", message: vmName, color: "blue" }),
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Cleanup fehlgeschlagen."), color: "red" }),
    });
  }

  return (
    <Stack>
      <Title order={3}>Restore</Title>

      {cleanupPending.length > 0 && (
        <Stack gap="xs">
          <Text size="sm" fw={600}>
            Ausstehender Cleanup
          </Text>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>VM</Table.Th>
                <Table.Th>Angehängte VHDX</Table.Th>
                <Table.Th>Abgeschlossen</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {cleanupPending.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>{r.vm_name}</Table.Td>
                  <Table.Td ff="monospace" fz="xs">
                    {r.restored_vhd_path}
                  </Table.Td>
                  <Table.Td>{r.finished_at ? new Date(r.finished_at).toLocaleString("de-DE") : "-"}</Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      color="red"
                      variant="light"
                      leftSection={<IconTrash size={14} />}
                      loading={cleanupRun.isPending}
                      onClick={() => handleCleanup(r.id, r.vm_name)}
                    >
                      Cleanup durchführen
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}

      <Text size="sm" fw={600}>
        VMs mit vorhandenen Backups
      </Text>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Host</Table.Th>
            <Table.Th>Cluster</Table.Th>
            <Table.Th>Backups</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {vms?.map((vm) => (
            <Table.Tr key={vm.name}>
              <Table.Td>{vm.name}</Table.Td>
              <Table.Td>
                <Badge color={STATE_COLOR[vm.state ?? ""] ?? "gray"} variant="light">
                  {vm.state ?? "-"}
                </Badge>
              </Table.Td>
              <Table.Td>{vm.host ?? "-"}</Table.Td>
              <Table.Td>{vm.cluster ?? "-"}</Table.Td>
              <Table.Td>
                <Badge variant="filled" color="blue">
                  {vm.backup_count}
                </Badge>
              </Table.Td>
              <Table.Td>
                <Button size="xs" leftSection={<IconDatabaseImport size={14} />} onClick={() => setWizardVm(vm)}>
                  Restore
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {vms?.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="md">
          Noch keine VM mit vorhandenen Backups.
        </Text>
      )}

      <RestoreWizardModal opened={!!wizardVm} onClose={() => setWizardVm(null)} vm={wizardVm} />
    </Stack>
  );
}
