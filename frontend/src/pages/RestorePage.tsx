import { useState } from "react";
import { ActionIcon, Alert, Badge, Button, Group, Paper, Stack, Table, Tabs, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDatabaseImport, IconInfoCircle, IconPlus, IconSearch, IconTrash } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useCleanupRestoreRun,
  useDeleteRestoreInfraConfig,
  useRestoreInfraConfigs,
  useRestoreRuns,
  useVmsWithBackups,
} from "@/api/hooks";
import { RestoreSetupWizardModal } from "@/components/RestoreSetupWizardModal";
import { RestoreWizardModal } from "@/components/RestoreWizardModal";
import type { VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const STATE_COLOR: Record<string, string> = { Running: "green", Off: "gray", Saved: "yellow" };

export function RestorePage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") ?? "overview";

  const { data: vms } = useVmsWithBackups();
  const { data: runs } = useRestoreRuns();
  const cleanupRun = useCleanupRestoreRun();
  const [wizardVm, setWizardVm] = useState<VmWithBackups | null>(null);

  const { data: restoreConfigs } = useRestoreInfraConfigs();
  const deleteRestoreConfig = useDeleteRestoreInfraConfig();
  const [restoreWizardOpen, setRestoreWizardOpen] = useState(false);
  const [vmSearch, setVmSearch] = useState("");

  const cleanupPending = runs?.filter((r) => r.cleanup_needed) ?? [];
  const filteredVms = (vms ?? []).filter((vm) => vm.name.toLowerCase().includes(vmSearch.trim().toLowerCase()));

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

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "overview" })}>
        <Tabs.List>
          <Tabs.Tab value="overview">Wiederherstellen</Tabs.Tab>
          <Tabs.Tab value="setup">Setup</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="overview" pt="md">
          <Stack>

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

      <Group justify="space-between" align="center">
        <Text size="sm" fw={600}>
          VMs mit vorhandenen Backups
        </Text>
        <TextInput
          placeholder="VM-Name suchen…"
          leftSection={<IconSearch size={14} />}
          value={vmSearch}
          onChange={(e) => setVmSearch(e.currentTarget.value)}
          w={280}
        />
      </Group>
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
          {filteredVms.map((vm) => {
            const pendingCleanup = cleanupPending.find((r) => r.vm_name === vm.name);
            return (
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
                  <Group gap="xs" wrap="nowrap">
                    {pendingCleanup && (
                      <Badge color="orange" variant="light" size="sm">
                        Restore aktiv
                      </Badge>
                    )}
                    <Button size="xs" leftSection={<IconDatabaseImport size={14} />} onClick={() => setWizardVm(vm)}>
                      Restore
                    </Button>
                    {pendingCleanup && (
                      <Button
                        size="xs"
                        color="orange"
                        variant="light"
                        leftSection={<IconTrash size={14} />}
                        loading={cleanupRun.isPending}
                        onClick={() => handleCleanup(pendingCleanup.id, vm.name)}
                      >
                        Restore abschließen
                      </Button>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      {vms?.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="md">
          Noch keine VM mit vorhandenen Backups.
        </Text>
      )}
      {(vms?.length ?? 0) > 0 && filteredVms.length === 0 && (
        <Text c="dimmed" size="sm" ta="center" py="md">
          Keine VM passt zur Suche „{vmSearch}“.
        </Text>
      )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="setup" pt="md">
          <Stack>
            <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
              Der VHDX-Restore-Workflow klont eine LUN aus einem Snapshot und meldet sich per nativem
              Windows-iSCSI-Initiator auf einem dedizierten Restore-Proxy-Host (siehe HVNB_RESTORE_PROXY_* in der
              Server-Konfiguration) bei der NetApp-SVM an, um die wiederhergestellte VHDX per SMB auf die Ziel-CSV
              zu kopieren. Voraussetzung: der Proxy-Host braucht Netzwerkzugriff auf ein iSCSI-Interface der
              Ziel-SVM sowie auf Port 445 (SMB) eines Hyper-V-Knotens, und ist per WinRM vom Server aus erreichbar.
            </Alert>
            <Paper p="md">
              <Group justify="space-between" mb="sm">
                <Title order={5}>Konfigurierte SVMs</Title>
                <Button leftSection={<IconPlus size={16} />} onClick={() => setRestoreWizardOpen(true)}>
                  Restore-Infrastruktur einrichten
                </Button>
              </Group>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>SVM</Table.Th>
                    <Table.Th>iSCSI-Interface</Table.Th>
                    <Table.Th>Igroup</Table.Th>
                    <Table.Th>Initiator</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {restoreConfigs?.map((c) => (
                    <Table.Tr key={c.id}>
                      <Table.Td>{c.svm_name}</Table.Td>
                      <Table.Td>
                        {c.iscsi_lif_name ?? "-"} ({c.iscsi_lif_address}:{c.iscsi_lif_port})
                      </Table.Td>
                      <Table.Td>{c.igroup_name}</Table.Td>
                      <Table.Td ff="monospace" fz="xs">
                        {c.initiator_iqn}
                      </Table.Td>
                      <Table.Td>
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          onClick={() => {
                            if (window.confirm(`Restore-Setup für '${c.svm_name}' entfernen?`)) {
                              deleteRestoreConfig.mutate(c.id);
                            }
                          }}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {restoreConfigs?.length === 0 && (
                <Text c="dimmed" size="sm" ta="center" py="md">
                  Noch keine SVM für Restore eingerichtet.
                </Text>
              )}
            </Paper>
          </Stack>
        </Tabs.Panel>
      </Tabs>

      <RestoreWizardModal opened={!!wizardVm} onClose={() => setWizardVm(null)} vm={wizardVm} />
      <RestoreSetupWizardModal opened={restoreWizardOpen} onClose={() => setRestoreWizardOpen(false)} />
    </Stack>
  );
}
