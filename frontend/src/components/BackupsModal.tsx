import { ActionIcon, Badge, Loader, Menu, Modal, ScrollArea, Stack, Table, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDotsVertical, IconDatabaseImport, IconTrash, IconUnlink } from "@tabler/icons-react";

import { useBackupsForObject, useDeleteBackupSnapshot, useDetachVmFromBackupSnapshot } from "@/api/hooks";
import type { BackupScope, BackupSnapshot } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

interface BackupsModalProps {
  opened: boolean;
  onClose: () => void;
  scope: BackupScope;
  name: string | undefined;
  // Hyper-V-Cluster der VM/des CSVs -- macht die Aufloesung cluster-sicher
  // (siehe Backend _resolve_volume_keys_for_object). Ohne das kann bei
  // zwei Clustern mit gleichnamigem CSV/derselben VM das falsche Volume
  // getroffen werden (live als Bug beobachtet).
  clusterId: string | undefined | null;
  // Nur fuer scope "vm" relevant -- oeffnet den Restore-Wizard mit diesem
  // Snapshot bereits vorausgewaehlt (siehe RestoreWizardModal.initialSnapshotId).
  onOpenRestoreWizard?: (snapshotId: string) => void;
}

export function BackupsModal({ opened, onClose, scope, name, clusterId, onOpenRestoreWizard }: BackupsModalProps) {
  const { data: backups, isLoading } = useBackupsForObject(scope, name, clusterId, opened);
  const deleteSnapshot = useDeleteBackupSnapshot(scope, name);
  const detachVm = useDetachVmFromBackupSnapshot(scope, name);

  function handleDeleteSnapshot(b: BackupSnapshot) {
    const otherVms = b.vm_names.filter((v) => v !== name);
    confirmAction({
      title: "Snapshot löschen",
      message:
        otherVms.length > 0
          ? `Diesen Snapshot wirklich unwiderruflich löschen? Betrifft auch: ${otherVms.join(", ")} -- deren Backup an diesem Snapshot geht ebenfalls verloren.`
          : "Diesen Snapshot wirklich unwiderruflich löschen (auf der NetApp und in der Datenbank)?",
      confirmLabel: "Löschen",
      onConfirm: () =>
        deleteSnapshot.mutate(b.id, {
          onSuccess: () => notifications.show({ title: "Snapshot gelöscht", message: b.snapshot_name ?? b.id, color: "blue" }),
          onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Snapshot konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  function handleDetachVm(b: BackupSnapshot) {
    if (!name) return;
    confirmAction({
      title: "VM-Informationen aus Backup entfernen",
      message: `'${name}' aus diesem Backup-Eintrag entfernen? Der Snapshot bleibt für andere VMs und das CSV unverändert erhalten -- es wird nur die Zuordnung in der Datenbank entfernt.`,
      confirmLabel: "Entfernen",
      color: "orange",
      onConfirm: () =>
        detachVm.mutate(
          { snapshotId: b.id, vmName: name },
          {
            onSuccess: () => notifications.show({ title: "Entfernt", message: `${name} aus Backup-Historie entfernt.`, color: "blue" }),
            onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Konnte nicht entfernt werden."), color: "red" }),
          },
        ),
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Vorhandene Backups: ${name ?? ""}`} size="1400px">
      <Stack>
        {isLoading && <Loader size="sm" />}
        {!isLoading && backups?.length === 0 && (
          <Text c="dimmed" size="sm">
            Keine vorhandenen Backups fuer dieses Objekt gefunden.
          </Text>
        )}
        {!isLoading && backups && backups.length > 0 && (
          <ScrollArea type="auto" offsetScrollbars>
            <Table striped highlightOnHover style={{ whiteSpace: "nowrap" }}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Erstellt</Table.Th>
                  <Table.Th>Konsistenz</Table.Th>
                  <Table.Th>System</Table.Th>
                  <Table.Th>Policy</Table.Th>
                  <Table.Th>Volume</Table.Th>
                  <Table.Th>SVM / Cluster</Table.Th>
                  <Table.Th>CSVs</Table.Th>
                  <Table.Th>VMs</Table.Th>
                  <Table.Th>Snapshot</Table.Th>
                  <Table.Th />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {backups.map((b) => (
                  <Table.Tr key={b.id}>
                    <Table.Td>{new Date(b.created_at).toLocaleString("de-DE")}</Table.Td>
                    <Table.Td>
                      <Badge color={b.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light">
                        {b.consistency === "ApplicationConsistent" ? "App-konsistent" : "Crash-konsistent"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={b.restore_source === "secondary" ? "orange" : "blue"} variant="light">
                        {b.restore_source === "secondary" ? "Sekundär" : "Primär"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{b.policy_name}</Table.Td>
                    <Table.Td>{b.volume_name ?? "-"}</Table.Td>
                    <Table.Td>
                      {b.svm_name ?? "-"} / {b.netapp_cluster_name ?? "-"}
                    </Table.Td>
                    <Table.Td>{b.csv_names.join(", ") || "-"}</Table.Td>
                    <Table.Td>{b.vm_names.join(", ") || "-"}</Table.Td>
                    <Table.Td ff="monospace" fz="xs">
                      {b.snapshot_name ?? "-"}
                    </Table.Td>
                    <Table.Td>
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <ActionIcon variant="subtle" color="gray">
                            <IconDotsVertical size={16} />
                          </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          {scope === "vm" && onOpenRestoreWizard && (
                            <Menu.Item leftSection={<IconDatabaseImport size={14} />} onClick={() => onOpenRestoreWizard(b.id)}>
                              Im Restore-Wizard öffnen
                            </Menu.Item>
                          )}
                          {scope === "vm" && (
                            <Menu.Item leftSection={<IconUnlink size={14} />} onClick={() => handleDetachVm(b)}>
                              VM-Informationen aus diesem Backup entfernen
                            </Menu.Item>
                          )}
                          <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => handleDeleteSnapshot(b)}>
                            Snapshot komplett löschen
                          </Menu.Item>
                        </Menu.Dropdown>
                      </Menu>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Stack>
    </Modal>
  );
}
