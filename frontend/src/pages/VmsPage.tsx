import { useState } from "react";
import { ActionIcon, Badge, Box, Group, Paper, Progress, Stack, Table, Tabs, Text, Title, Tooltip } from "@mantine/core";
import {
  IconChevronsRight,
  IconCpu,
  IconDatabase,
  IconFileText,
  IconFolder,
  IconHistory,
  IconInfoCircle,
  IconNetwork,
  IconPlayerPlay,
  IconServer,
  IconServer2,
  IconServerCog,
  IconStack2,
  IconX,
} from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useCsvs, useVms } from "@/api/hooks";
import { BackupsModal } from "@/components/BackupsModal";
import { PolicyPickerModal } from "@/components/PolicyPickerModal";
import { RestoreWizardModal } from "@/components/RestoreWizardModal";
import { SearchInput } from "@/components/SearchInput";
import type { BackupScope, Csv, PolicySummary, Vm } from "@/api/types";
import { formatBytes } from "@/utils/format";
import { useRunPolicy } from "@/utils/runPolicy";
import { matchesAllColumns } from "@/utils/search";

// CsvRead hat (anders als VmRead.id) keine eigene stabile Zeilen-ID -- der
// Name allein ist NICHT eindeutig, sobald zwei Hyper-V-Cluster ein CSV mit
// identischem Namen haben (in der Praxis haeufig, z.B. beide "CSV01").
// cluster_id+Name ist dagegen stabil (bleibt ueber Discovery-Laeufe hinweg
// gleich, anders als eine rohe Zeilen-ID, die bei jeder Discovery neu
// vergeben wird) -- dieselbe Ueberlegung wie beim Backend-Fix fuer
// ResourceGroup.members (siehe app.models.resource_group).
function csvIdentity(csv: Csv): string {
  return `${csv.cluster_id ?? ""}::${csv.name}`;
}

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
    : vm.csv_paths.map((p) => ({ name: `${vm.name}.vhdx`, size_bytes: vm.vhdx_size_bytes ?? 0, used_bytes: vm.vhdx_used_bytes, csv_path: p }));

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

      <Group gap="lg" mb="sm">
        <Group gap={4}>
          <IconCpu size={14} />
          <Text size="xs" c="dimmed">
            {vm.cpu_count ?? "-"} vCPU
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          RAM: {formatBytes(vm.memory_startup_bytes)}
          {vm.dynamic_memory_enabled && ` (dynamisch: ${formatBytes(vm.memory_minimum_bytes)} – ${formatBytes(vm.memory_maximum_bytes)})`}
        </Text>
        <Text size="xs" c="dimmed">
          Generation {vm.generation ?? "-"}
        </Text>
      </Group>
      {vm.network_adapters.length > 0 && (
        <Group gap="xs" mb="xs">
          {vm.network_adapters.map((n, i) => (
            <Badge key={i} variant="light" color="grape" leftSection={<IconNetwork size={12} />}>
              {n.name}: {n.mac_address ?? "-"} @ {n.switch_name ?? "-"}
              {n.vlan_id ? ` (VLAN ${n.vlan_id})` : ""}
            </Badge>
          ))}
        </Group>
      )}
      {vm.pci_devices.length > 0 && (
        <Text size="xs" c="dimmed" mb="sm">
          PCI-Devices: {vm.pci_devices.join(", ")}
        </Text>
      )}

      <Stack gap="sm">
        {vhds.map((vhd, i) => {
          const csvName = vhd.csv_path.split(/[\\/]/).pop();
          const csv = csvs?.find((c) => c.name === csvName && c.cluster_id === vm.cluster_id);
          return (
            <Box key={i} style={{ overflowX: "auto" }}>
              <Group gap={6} wrap="nowrap">
                <ChainNode icon={<IconServer2 size={14} />} label="VM" title={vm.name} />
                <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                <ChainNode
                  icon={<IconFileText size={14} />}
                  label="VHD"
                  title={vhd.name}
                  usedBytes={vhd.used_bytes}
                  capacityBytes={vhd.size_bytes}
                />
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

function CsvChainHeader({ csv, vms, onClose }: { csv: Csv; vms: Vm[] | undefined; onClose: () => void }) {
  const vmsOnCsv =
    vms?.filter((vm) => vm.cluster_id === csv.cluster_id && vm.csv_paths.some((p) => p.split(/[\\/]/).pop() === csv.name)) ?? [];

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>
          Speicherkette: {csv.name}
        </Text>
        <ActionIcon variant="subtle" size="sm" onClick={onClose}>
          <IconX size={14} />
        </ActionIcon>
      </Group>
      <Stack gap="sm">
        <Group gap={6} wrap="nowrap">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            VMs auf diesem CSV:
          </Text>
          {vmsOnCsv.length ? (
            vmsOnCsv.map((vm) => (
              <Badge key={vm.id} color="teal" variant="light">
                {vm.name}
              </Badge>
            ))
          ) : (
            <Text size="xs" c="dimmed">
              keine
            </Text>
          )}
        </Group>
        <Box style={{ overflowX: "auto" }}>
          <Group gap={6} wrap="nowrap">
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
          </Group>
        </Box>
      </Stack>
    </Paper>
  );
}

export function VmsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "csv" ? "csv" : "vms";
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const [selectedVm, setSelectedVm] = useState<Vm | null>(null);
  const [selectedCsv, setSelectedCsv] = useState<Csv | null>(null);
  const [backupsTarget, setBackupsTarget] = useState<{ scope: BackupScope; name: string; clusterId: string | null } | null>(null);
  const [restoreWizardTarget, setRestoreWizardTarget] = useState<{ vmName: string; snapshotId: string; clusterId: string | null } | null>(
    null,
  );
  const [vmSearch, setVmSearch] = useState("");
  const filteredVms = (vms ?? []).filter((vm) => matchesAllColumns(vm, vmSearch));
  const [csvSearch, setCsvSearch] = useState("");
  const filteredCsvs = (csvs ?? []).filter((csv) => matchesAllColumns(csv, csvSearch));
  const { runOrPick, runPolicy, pickerPolicies, closePicker } = useRunPolicy();

  function showBackups(scope: BackupScope, name: string, clusterId: string | null | undefined) {
    setBackupsTarget({ scope, name, clusterId: clusterId ?? null });
  }

  function toggleSelectedVm(vm: Vm) {
    setSelectedCsv(null);
    setSelectedVm((prev) => (prev?.id === vm.id ? null : vm));
  }

  function toggleSelectedCsv(csv: Csv) {
    setSelectedVm(null);
    setSelectedCsv((prev) => (prev && csvIdentity(prev) === csvIdentity(csv) ? null : csv));
  }

  function policiesOf(names: string[], ids: string[]): PolicySummary[] {
    return ids.map((id, i) => ({ id, name: names[i] }));
  }

  function runBackupNow(vm: Vm) {
    runOrPick(policiesOf(vm.policy_names, vm.policy_ids));
  }

  function runBackupNowForCsv(csv: Csv) {
    runOrPick(policiesOf(csv.policy_names, csv.policy_ids));
  }

  return (
    <Stack>
      <Title order={3}>Inventory</Title>

      {selectedVm && <VmChainHeader vm={selectedVm} csvs={csvs} onClose={() => setSelectedVm(null)} />}
      {selectedCsv && <CsvChainHeader csv={selectedCsv} vms={vms} onClose={() => setSelectedCsv(null)} />}

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "vms" })}>
        <Tabs.List>
          <Tabs.Tab value="vms">Virtuelle Maschinen</Tabs.Tab>
          <Tabs.Tab value="csv">Cluster Shared Volumes</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="vms" pt="md">
          <Paper p="md">
            <Title order={5} mb="sm">Virtuelle Maschinen</Title>
            <Group justify="flex-start" mb="sm">
              <SearchInput value={vmSearch} onChange={setVmSearch} placeholder="VM-Name suchen…" />
            </Group>
            <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Host</Table.Th>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>CSV-Pfade</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>Protection Group</Table.Th>
                <Table.Th>Protected</Table.Th>
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredVms.map((vm) => {
                const hasVhdxUsage = vm.vhdx_used_bytes != null && vm.vhdx_size_bytes != null && vm.vhdx_size_bytes > 0;
                const vhdxPct = hasVhdxUsage ? Math.round((vm.vhdx_used_bytes! / vm.vhdx_size_bytes!) * 100) : null;
                return (
                <Table.Tr
                  key={vm.id}
                  onClick={() => toggleSelectedVm(vm)}
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
                  <Table.Td miw={140}>
                    <Text size="xs" c="dimmed">
                      {formatBytes(vm.vhdx_used_bytes)} / {formatBytes(vm.vhdx_size_bytes)}
                      {vhdxPct != null ? ` (${vhdxPct}%)` : ""}
                    </Text>
                    {vhdxPct != null && (
                      <Progress value={vhdxPct} size={6} mt={2} color={vhdxPct >= 90 ? "red" : vhdxPct >= 75 ? "yellow" : "blue"} />
                    )}
                  </Table.Td>
                  <Table.Td>
                    <ResourceGroupCell groups={vm.resource_group_names} policies={vm.policy_names} />
                  </Table.Td>
                  <Table.Td>
                    <ProtectedBadge protected={vm.protected} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                      <Tooltip label="Backup jetzt starten">
                        <ActionIcon variant="light" onClick={() => runBackupNow(vm)}>
                          <IconPlayerPlay size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Details anzeigen">
                        <ActionIcon variant="light" onClick={() => setSelectedVm(vm)}>
                          <IconInfoCircle size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Backups anzeigen">
                        <ActionIcon variant="light" onClick={() => showBackups("vm", vm.name, vm.cluster_id)}>
                          <IconHistory size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
            {(vms?.length ?? 0) > 0 && filteredVms.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Keine VM passt zur Suche „{vmSearch}“.
              </Text>
            )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="csv" pt="md">
          <Paper p="md">
            <Title order={5} mb="sm">
              Cluster Shared Volumes
            </Title>
            <Group justify="flex-start" mb="sm">
              <SearchInput value={csvSearch} onChange={setCsvSearch} />
            </Group>
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
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredCsvs.map((csv) => {
                const usedPct =
                  csv.capacity_bytes && csv.capacity_bytes > 0
                    ? Math.round(((csv.used_bytes ?? 0) / csv.capacity_bytes) * 100)
                    : null;
                return (
                  <Table.Tr
                    key={csvIdentity(csv)}
                    onClick={() => toggleSelectedCsv(csv)}
                    style={{
                      cursor: "pointer",
                      backgroundColor: selectedCsv && csvIdentity(selectedCsv) === csvIdentity(csv) ? "var(--mantine-color-blue-light)" : undefined,
                    }}
                  >
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
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                        <Tooltip label="Backup jetzt starten (CSV-Scope)">
                          <ActionIcon variant="light" onClick={() => runBackupNowForCsv(csv)}>
                            <IconPlayerPlay size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Details anzeigen">
                          <ActionIcon variant="light" onClick={() => setSelectedCsv(csv)}>
                            <IconInfoCircle size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Backups anzeigen">
                          <ActionIcon variant="light" onClick={() => showBackups("csv", csv.name, csv.cluster_id)}>
                            <IconHistory size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
          {(csvs?.length ?? 0) > 0 && filteredCsvs.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein CSV passt zur Suche „{csvSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <BackupsModal
        opened={!!backupsTarget}
        onClose={() => setBackupsTarget(null)}
        scope={backupsTarget?.scope ?? "vm"}
        name={backupsTarget?.name}
        clusterId={backupsTarget?.clusterId}
        onOpenRestoreWizard={
          backupsTarget?.scope === "vm"
            ? (snapshotId) => {
                setRestoreWizardTarget({ vmName: backupsTarget.name, snapshotId, clusterId: backupsTarget.clusterId });
                setBackupsTarget(null);
              }
            : undefined
        }
      />

      <RestoreWizardModal
        opened={!!restoreWizardTarget}
        onClose={() => setRestoreWizardTarget(null)}
        vm={
          restoreWizardTarget
            ? (() => {
                const vm = (vms ?? []).find(
                  (v) => v.name === restoreWizardTarget.vmName && v.cluster_id === restoreWizardTarget.clusterId,
                );
                return vm
                  ? {
                      name: vm.name,
                      host: vm.host,
                      state: vm.state,
                      cluster: vm.cluster,
                      cluster_id: vm.cluster_id,
                      backup_count: 0,
                      exists_in_inventory: true,
                    }
                  : null;
              })()
            : null
        }
        initialSnapshotId={restoreWizardTarget?.snapshotId}
      />

      <PolicyPickerModal opened={!!pickerPolicies} onClose={closePicker} policies={pickerPolicies ?? []} onPick={runPolicy} />
    </Stack>
  );
}
