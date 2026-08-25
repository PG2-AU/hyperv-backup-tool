import { useState } from "react";
import { ActionIcon, Badge, Box, Group, Menu, Paper, Progress, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconChevronsRight,
  IconDatabase,
  IconFileText,
  IconFolder,
  IconInfoCircle,
  IconPlayerPlay,
  IconServer,
  IconServer2,
  IconServerCog,
  IconStack2,
  IconTerminal2,
  IconX,
} from "@tabler/icons-react";
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

function ChainNode({
  icon,
  label,
  title,
  usedBytes,
  capacityBytes,
}: {
  icon: React.ReactNode;
  label: string;
  title: string;
  usedBytes?: number | null;
  capacityBytes?: number | null;
}) {
  const hasUsage = usedBytes != null && capacityBytes != null && capacityBytes > 0;
  const pct = hasUsage ? Math.round((usedBytes! / capacityBytes!) * 100) : null;
  return (
    <Paper withBorder p="xs" miw={150}>
      <Group gap={6} mb={2} wrap="nowrap">
        {icon}
        <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
          {label}
        </Text>
      </Group>
      <Text size="sm" fw={600} truncate maw={170}>
        {title}
      </Text>
      {capacityBytes != null && (
        <Text size="xs" c="dimmed">
          {hasUsage ? `${formatBytes(usedBytes)} / ${formatBytes(capacityBytes)}` : formatBytes(capacityBytes)}
        </Text>
      )}
      {pct !== null && <Progress value={pct} size={4} mt={4} color={pct >= 90 ? "red" : pct >= 75 ? "yellow" : "blue"} />}
    </Paper>
  );
}

function VmChainHeader({ vm, csvs, onClose }: { vm: Vm; csvs: Csv[] | undefined; onClose: () => void }) {
  const vhds = vm.vhds.length
    ? vm.vhds
    : vm.csv_paths.map((p) => ({ name: `${vm.name}.vhdx`, size_bytes: vm.vhdx_size_bytes ?? 0, csv_path: p }));

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>
          Speicherkette: {vm.name}
        </Text>
        <ActionIcon variant="subtle" size="sm" onClick={onClose}>
          <IconX size={14} />
        </ActionIcon>
      </Group>
      <Stack gap="sm">
        {vhds.map((vhd, i) => {
          const csvName = vhd.csv_path.split(/[\\/]/).pop();
          const csv = csvs?.find((c) => c.name === csvName);
          return (
            <Box key={i} style={{ overflowX: "auto" }}>
              <Group gap={6} wrap="nowrap">
                <ChainNode icon={<IconServer2 size={14} />} label="VM" title={vm.name} />
                <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                <ChainNode icon={<IconFileText size={14} />} label="VHD" title={vhd.name} capacityBytes={vhd.size_bytes} />
                <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                {csv ? (
                  <>
                    <ChainNode
                      icon={<IconFolder size={14} />}
                      label="CSV"
                      title={csv.name}
                      usedBytes={csv.used_bytes}
                      capacityBytes={csv.capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode
                      icon={<IconStack2 size={14} />}
                      label="LUN"
                      title={csv.lun_name ?? "-"}
                      usedBytes={csv.lun_used_bytes}
                      capacityBytes={csv.lun_capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode
                      icon={<IconDatabase size={14} />}
                      label="Volume"
                      title={csv.volume_name ?? "-"}
                      usedBytes={csv.volume_used_bytes}
                      capacityBytes={csv.volume_capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServerCog size={14} />} label="SVM" title={csv.svm_name ?? "-"} />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServer size={14} />} label="Cluster" title={csv.netapp_cluster_name ?? "-"} />
                  </>
                ) : (
                  <Text c="dimmed" size="sm">
                    CSV-Details nicht verfügbar
                  </Text>
                )}
              </Group>
            </Box>
          );
        })}
      </Stack>
    </Paper>
  );
}

export function VmsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "csv" ? "csv" : "vms";
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const vmMenu = useContextMenu<Vm>();
  const csvMenu = useContextMenu<Csv>();
  const [selectedVm, setSelectedVm] = useState<Vm | null>(null);

  function toggleSelectedVm(vm: Vm) {
    setSelectedVm((prev) => (prev?.id === vm.id ? null : vm));
  }

  function runBackupNow(target: string) {
    notifications.show({
      title: "Backup ausgeloest",
      message: `Ad-hoc Backup fuer '${target}' wurde in die Warteschlange gestellt.`,
      color: "blue",
    });
  }

  return (
    <Stack>
      <Title order={3}>Inventory</Title>

      {selectedVm && <VmChainHeader vm={selectedVm} csvs={csvs} onClose={() => setSelectedVm(null)} />}

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
                <Table.Th>Protection Group</Table.Th>
                <Table.Th>Protected</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {vms?.map((vm) => (
                <Table.Tr
                  key={vm.id}
                  onClick={() => toggleSelectedVm(vm)}
                  onContextMenu={(e) => vmMenu.open(e, vm)}
                  style={{ cursor: "pointer", backgroundColor: selectedVm?.id === vm.id ? "var(--mantine-color-blue-light)" : undefined }}
                >
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
                <Table.Th>Protection Group</Table.Th>
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
        <Menu.Item leftSection={<IconInfoCircle size={16} />} onClick={() => vmMenu.state && setSelectedVm(vmMenu.state.data)}>
          Details anzeigen
        </Menu.Item>
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
