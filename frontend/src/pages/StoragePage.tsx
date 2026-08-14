import { Badge, Group, Menu, Paper, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle, IconRefresh } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useMetroClusterStatus, useSnapMirrorRelationships, useSvms } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import type { SnapMirrorRelationship } from "@/api/types";

export function StoragePage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") ?? "svms";
  const { data: svms } = useSvms();
  const { data: relationships } = useSnapMirrorRelationships();
  const { data: mcc } = useMetroClusterStatus();
  const relMenu = useContextMenu<SnapMirrorRelationship>();

  function triggerUpdate(rel: SnapMirrorRelationship) {
    notifications.show({
      title: "SnapMirror-Update ausgeloest",
      message: `${rel.source_path} -> ${rel.destination_path}`,
      color: "blue",
    });
  }

  return (
    <Stack>
      <Title order={3}>Storage</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "svms" })}>
        <Tabs.List>
          <Tabs.Tab value="svms">Storage Virtual Machines</Tabs.Tab>
          <Tabs.Tab value="snapmirror">SnapMirror-Beziehungen</Tabs.Tab>
          <Tabs.Tab value="metrocluster">MetroCluster</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="svms" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>MetroCluster</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {svms?.map((svm) => (
                <Table.Tr key={svm.name}>
                  <Table.Td>{svm.name}</Table.Td>
                  <Table.Td>
                    <Badge color="green" variant="light">
                      {svm.state}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{svm.is_metrocluster ? "Ja" : "Nein"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>

        <Tabs.Panel value="snapmirror" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Quelle</Table.Th>
                <Table.Th>Ziel</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Healthy</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {relationships?.map((rel) => (
                <Table.Tr key={rel.uuid} onContextMenu={(e) => relMenu.open(e, rel)} style={{ cursor: "context-menu" }}>
                  <Table.Td>{rel.source_path}</Table.Td>
                  <Table.Td>{rel.destination_path}</Table.Td>
                  <Table.Td>{rel.state}</Table.Td>
                  <Table.Td>
                    <Badge color={rel.healthy ? "green" : "red"} variant="light">
                      {rel.healthy ? "OK" : "Fehler"}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>

        <Tabs.Panel value="metrocluster" pt="md">
          <Paper p="md" maw={480}>
            <Stack gap="xs">
              <Group justify="space-between">
                <Text c="dimmed">Konfiguriert</Text>
                <Text fw={600}>{mcc?.configured ? "Ja" : "Nein"}</Text>
              </Group>
              <Group justify="space-between">
                <Text c="dimmed">Modus</Text>
                <Text fw={600}>{mcc?.mode ?? "-"}</Text>
              </Group>
              <Group justify="space-between">
                <Text c="dimmed">Switchover aktiv</Text>
                <Badge color={mcc?.switchover_in_progress ? "orange" : "green"}>
                  {mcc?.switchover_in_progress ? "Ja" : "Nein"}
                </Badge>
              </Group>
            </Stack>
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <ContextMenuDropdown position={relMenu.state?.position ?? null} opened={!!relMenu.state} onClose={relMenu.close}>
        <Menu.Label>{relMenu.state?.data.source_path}</Menu.Label>
        <Menu.Item leftSection={<IconRefresh size={16} />} onClick={() => relMenu.state && triggerUpdate(relMenu.state.data)}>
          SnapMirror-Update erzwingen
        </Menu.Item>
        <Menu.Item leftSection={<IconInfoCircle size={16} />}>Details anzeigen</Menu.Item>
      </ContextMenuDropdown>
    </Stack>
  );
}
