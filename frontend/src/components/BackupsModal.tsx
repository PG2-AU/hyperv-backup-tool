import { Badge, Loader, Modal, Stack, Table, Text } from "@mantine/core";

import { useBackupsForObject } from "@/api/hooks";
import type { BackupScope } from "@/api/types";

interface BackupsModalProps {
  opened: boolean;
  onClose: () => void;
  scope: BackupScope;
  name: string | undefined;
}

export function BackupsModal({ opened, onClose, scope, name }: BackupsModalProps) {
  const { data: backups, isLoading } = useBackupsForObject(scope, name, opened);

  return (
    <Modal opened={opened} onClose={onClose} title={`Vorhandene Backups: ${name ?? ""}`} size="xl">
      <Stack>
        {isLoading && <Loader size="sm" />}
        {!isLoading && backups?.length === 0 && (
          <Text c="dimmed" size="sm">
            Keine vorhandenen Backups fuer dieses Objekt gefunden.
          </Text>
        )}
        {!isLoading && backups && backups.length > 0 && (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Erstellt</Table.Th>
                <Table.Th>Konsistenz</Table.Th>
                <Table.Th>Policy</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>SVM / Cluster</Table.Th>
                <Table.Th>CSVs</Table.Th>
                <Table.Th>VMs</Table.Th>
                <Table.Th>Snapshot</Table.Th>
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
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>
    </Modal>
  );
}
