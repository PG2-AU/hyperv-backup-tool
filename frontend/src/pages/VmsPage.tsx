import { Badge, Group, Menu, Progress, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle, IconPlayerPlay, IconTerminal2 } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useCsvs, useVms } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import type { Csv, Vm } from "@/api/types";
import { formatBytes } from "@/utils/format";

const STATE_COLOR: Record<string, string> = { Running: "green", Off: "gray", Saved: "yellow" };

function ResourceGroupCell({ groups, policies }: { groups: string[]; policies: string[] }) {
  if (!groups.length) {
    return (
      <Text c="dimmed" size="sm">
        keine
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      <Group gap={4}>
        {groups.map((g) => (
          <Badge key={g} color="blue" variant="light">
            {g}
          </Badge>
        ))}
      </Group>
      {policies.length > 0 && (
        <Text size="xs" c="dimmed">
          Policy: {policies.join(", ")}
        </Text>
      )}
    </Stack>
  );
}

function ProtectedBadge({ protected: isProtected }: { protected: boolean }) {
  return (
    <Badge color={isProtected ? "green" : "red"} variant="light">
      {isProtected ? "Protected" : "Ungeschützt"}
    </Badge>
  );
}

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
                <Table.Th>VHDX-Größe</Table.Th>
                <Table.Th>Resource Group</Table.Th>
                <Table.Th>Protected</Table.Th>
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
                  <Table.Td>{formatBytes(vm.vhdx_size_bytes)}</Table.Td>
                  <Table.Td>
                    <ResourceGroupCell groups={vm.resource_group_names} policies={vm.policy_names} />
                  </Table.Td>
                  <Table.Td>
                    <ProtectedBadge protected={vm.protected} />
                  </Table.Td>
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
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>LUN</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>Resource Group</Table.Th>
                <Table.Th>Protected</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {csvs?.map((csv) => {
                const usedPct =
                  csv.capacity_bytes && csv.capacity_bytes > 0
                    ? Math.round(((csv.used_bytes ?? 0) / csv.capacity_bytes) * 100)
                    : null;
                return (
                  <Table.Tr key={csv.name} onContextMenu={(e) => csvMenu.open(e, csv)} style={{ cursor: "context-menu" }}>
                    <Table.Td>{csv.name}</Table.Td>
                    <Table.Td>{csv.owner_node}</Table.Td>
                    <Table.Td>
                      <Badge color="green" variant="light">
                        {csv.state}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{csv.volume_path}</Table.Td>
                    <Table.Td>{formatBytes(csv.capacity_bytes)}</Table.Td>
                    <Table.Td miw={160}>
                      {usedPct === null ? (
                        "-"
                      ) : (
                        <Stack gap={2}>
                          <Progress value={usedPct} color={usedPct >= 90 ? "red" : usedPct >= 75 ? "yellow" : "blue"} size="sm" />
                          <Group justify="space-between">
                            <Text size="xs" c="dimmed">
                              {formatBytes(csv.used_bytes)} / {formatBytes(csv.capacity_bytes)}
                            </Text>
                            <Text size="xs" c="dimmed">
                              {usedPct}%
                            </Text>
                          </Group>
                        </Stack>
                      )}
                    </Table.Td>
                    <Table.Td>{csv.lun_name ?? "-"}</Table.Td>
                    <Table.Td>{csv.volume_name ?? "-"}</Table.Td>
                    <Table.Td>
                      <ResourceGroupCell groups={csv.resource_group_names} policies={csv.policy_names} />
                    </Table.Td>
                    <Table.Td>
                      <ProtectedBadge protected={csv.protected} />
                    </Table.Td>
                  </Table.Tr>
                );
              })}
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
