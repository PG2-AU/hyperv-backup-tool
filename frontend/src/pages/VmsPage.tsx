import { Badge, Menu, Stack, Table, Tabs, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle, IconPlayerPlay, IconTerminal2 } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useCsvs, useVms } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import type { Csv, Vm } from "@/api/types";

const STATE_COLOR: Record<string, string> = { Running: "green", Off: "gray", Saved: "yellow" };

export function VmsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "csv" ? "csv" : "vms";
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const vmMenu = useContextMenu<Vm>();
  const csvMenu = useContextMenu<Csv>();

  function runBackupNow(target: string) {
    notifications.show({
      title: "Backup ausgeloest",
      message: `Ad-hoc Backup fuer '${target}' wurde in die Warteschlange gestellt.`,
      color: "blue",
    });
  }

  return (
    <Stack>
      <Title order={3}>VMs & CSVs</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "vms" })}>
        <Tabs.List>
          <Tabs.Tab value="vms">Virtuelle Maschinen</Tabs.Tab>
          <Tabs.Tab value="csv">Cluster Shared Volumes</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="vms" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Host</Table.Th>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>CSV-Pfade</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {vms?.map((vm) => (
                <Table.Tr key={vm.id} onContextMenu={(e) => vmMenu.open(e, vm)} style={{ cursor: "context-menu" }}>
                  <Table.Td>{vm.name}</Table.Td>
                  <Table.Td>
                    <Badge color={STATE_COLOR[vm.state] ?? "gray"} variant="light">
                      {vm.state}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{vm.host}</Table.Td>
                  <Table.Td>{vm.cluster ?? "-"}</Table.Td>
                  <Table.Td>{vm.csv_paths.join(", ")}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>

        <Tabs.Panel value="csv" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Owner-Node</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Pfad</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {csvs?.map((csv) => (
                <Table.Tr key={csv.name} onContextMenu={(e) => csvMenu.open(e, csv)} style={{ cursor: "context-menu" }}>
                  <Table.Td>{csv.name}</Table.Td>
                  <Table.Td>{csv.owner_node}</Table.Td>
                  <Table.Td>
                    <Badge color="green" variant="light">
                      {csv.state}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{csv.volume_path}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>
      </Tabs>

      <ContextMenuDropdown position={vmMenu.state?.position ?? null} opened={!!vmMenu.state} onClose={vmMenu.close}>
        <Menu.Label>{vmMenu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconPlayerPlay size={16} />} onClick={() => vmMenu.state && runBackupNow(vmMenu.state.data.name)}>
          Backup jetzt starten
        </Menu.Item>
        <Menu.Item leftSection={<IconInfoCircle size={16} />}>Details anzeigen</Menu.Item>
        <Menu.Item leftSection={<IconTerminal2 size={16} />}>Log anzeigen</Menu.Item>
      </ContextMenuDropdown>

      <ContextMenuDropdown position={csvMenu.state?.position ?? null} opened={!!csvMenu.state} onClose={csvMenu.close}>
        <Menu.Label>{csvMenu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconPlayerPlay size={16} />} onClick={() => csvMenu.state && runBackupNow(csvMenu.state.data.name)}>
          Backup jetzt starten (CSV-Scope)
        </Menu.Item>
        <Menu.Item leftSection={<IconInfoCircle size={16} />}>Details anzeigen</Menu.Item>
      </ContextMenuDropdown>
    </Stack>
  );
}
