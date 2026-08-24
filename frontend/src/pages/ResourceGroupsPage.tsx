import { useState } from "react";
import { Badge, Button, Group, Menu, Paper, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlus, IconTrash } from "@tabler/icons-react";

import { useDeleteResourceGroup, useResourceGroups } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { ResourceGroupFormModal } from "@/components/ResourceGroupFormModal";
import type { ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const SCOPE_LABEL: Record<string, string> = { vm: "VMs", csv: "CSVs", lun: "LUNs" };

export function ResourceGroupsPage() {
  const { data: groups } = useResourceGroups();
  const deleteGroup = useDeleteResourceGroup();
  const menu = useContextMenu<ResourceGroup>();
  const [formOpen, setFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<ResourceGroup | null>(null);

  function openCreate() {
    setEditingGroup(null);
    setFormOpen(true);
  }

  function openEdit(group: ResourceGroup) {
    setEditingGroup(group);
    setFormOpen(true);
  }

  function removeGroup(group: ResourceGroup) {
    if (!window.confirm(`Resource Group '${group.name}' wirklich löschen?`)) return;
    deleteGroup.mutate(group.id, {
      onSuccess: () => notifications.show({ title: "Resource Group gelöscht", message: group.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Resource Group konnte nicht gelöscht werden."), color: "red" }),
    });
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Resource Groups</Title>
        <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
          Resource Group anlegen
        </Button>
      </Group>

      <Paper p="md">
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Typ</Table.Th>
              <Table.Th>Mitglieder</Table.Th>
              <Table.Th>Verknüpfte Policies</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {groups?.map((group) => (
              <Table.Tr key={group.id} onContextMenu={(e) => menu.open(e, group)} style={{ cursor: "context-menu" }}>
                <Table.Td>{group.name}</Table.Td>
                <Table.Td>
                  <Badge variant="light" color="blue">
                    {SCOPE_LABEL[group.scope] ?? group.scope}
                  </Badge>
                </Table.Td>
                <Table.Td>{group.members.length ? group.members.join(", ") : "-"}</Table.Td>
                <Table.Td>
                  {group.policies.length ? (
                    <Group gap={4}>
                      {group.policies.map((p) => (
                        <Badge key={p.id} color="indigo" variant="light">
                          {p.name}
                        </Badge>
                      ))}
                    </Group>
                  ) : (
                    <Text c="dimmed" size="sm">
                      keine
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {groups?.length === 0 && (
          <Text c="dimmed" size="sm" ta="center" py="md">
            Noch keine Resource Groups angelegt.
          </Text>
        )}
      </Paper>

      <ContextMenuDropdown position={menu.state?.position ?? null} opened={!!menu.state} onClose={menu.close}>
        <Menu.Label>{menu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconEdit size={16} />} onClick={() => menu.state && openEdit(menu.state.data)}>
          Bearbeiten
        </Menu.Item>
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={() => menu.state && removeGroup(menu.state.data)}>
          Loeschen
        </Menu.Item>
      </ContextMenuDropdown>

      <ResourceGroupFormModal opened={formOpen} onClose={() => setFormOpen(false)} group={editingGroup} />
    </Stack>
  );
}
