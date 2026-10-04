import { useMemo, useState } from "react";
import { ActionIcon, Alert, Badge, Box, Group, Paper, SegmentedControl, Stack, Table, Tabs, Text, Title, Tooltip } from "@mantine/core";
import { IconRefresh, IconX } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { usePerformanceOverview } from "@/api/hooks.performance";
import type { PerfRow } from "@/api/hooks.performance";
import { PerformancePanel, formatIops, formatLatency, formatThroughput, latencyColor } from "@/components/PerformancePanel";
import { SearchInput } from "@/components/SearchInput";
import { apiErrorMessage } from "@/utils/errors";
import { lunShortName } from "@/utils/format";
import { matchesAllColumns } from "@/utils/search";

// Monitoring > Performance (Backlog #80 Stufe 1): aktuelle IOPS, Latenz und
// Durchsatz je CSV (ueber ihre LUN), SMB3-Freigabe (ueber ihr Volume) und
// aller Volumes, live von ONTAP (15-s-Mittel, alle 30 s neu). Klick auf eine
// Zeile zeigt den Verlauf.

type SortKey = "iops" | "latency" | "throughput" | "name";

const KIND_LABEL: Record<PerfRow["kind"], string> = { csv: "CSV", smb_share: "SMB3", volume: "Volume" };

function sortRows(rows: PerfRow[], key: SortKey): PerfRow[] {
  const value = (r: PerfRow) =>
    key === "iops" ? r.values?.iops.total : key === "latency" ? r.values?.latency_ms.total : r.values?.throughput.total;
  if (key === "name") return [...rows].sort((a, b) => a.name.localeCompare(b.name, "de"));
  return [...rows].sort((a, b) => (value(b) ?? -1) - (value(a) ?? -1));
}

function rowKey(r: PerfRow): string {
  return `${r.kind}|${r.netapp_cluster_id}|${r.object_uuid ?? r.name}`;
}

function PerfTable({ rows, selected, onSelect, showHyperV }: {
  rows: PerfRow[];
  selected: string | null;
  onSelect: (row: PerfRow) => void;
  showHyperV: boolean;
}) {
  return (
    <Box style={{ maxHeight: "calc(100vh - 420px)", minHeight: 240, overflowY: "auto" }}>
      <Table striped highlightOnHover stickyHeader>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            {showHyperV && <Table.Th>Typ</Table.Th>}
            {showHyperV && <Table.Th>Hyper-V-Cluster</Table.Th>}
            <Table.Th>NetApp-Objekt</Table.Th>
            <Table.Th ta="right">IOPS gesamt</Table.Th>
            <Table.Th ta="right">Lesen / Schreiben</Table.Th>
            <Table.Th ta="right">Latenz Lesen</Table.Th>
            <Table.Th ta="right">Latenz Schreiben</Table.Th>
            <Table.Th ta="right">Durchsatz</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => {
            const v = r.values;
            const key = rowKey(r);
            const readColor = latencyColor(v?.latency_ms.read, v?.iops.read);
            const writeColor = latencyColor(v?.latency_ms.write, v?.iops.write);
            return (
              <Table.Tr
                key={key}
                onClick={() => r.object_uuid && onSelect(r)}
                bg={selected === key ? "var(--mantine-color-blue-light)" : undefined}
                style={{ cursor: r.object_uuid ? "pointer" : undefined }}
              >
                <Table.Td>
                  <Text size="sm" fw={500}>
                    {r.name}
                  </Text>
                </Table.Td>
                {showHyperV && (
                  <Table.Td>
                    <Badge variant="light" size="sm" color={r.kind === "csv" ? "blue" : "teal"}>
                      {KIND_LABEL[r.kind]}
                    </Badge>
                  </Table.Td>
                )}
                {showHyperV && <Table.Td>{r.hyperv_cluster_name ?? "–"}</Table.Td>}
                <Table.Td>
                  <Text size="xs">
                    {r.netapp_cluster_name ?? "–"} / {r.svm_name ?? "–"}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {r.object_type === "lun" ? `LUN ${lunShortName(r.object_name)}` : `Volume ${r.object_name ?? "–"}`}
                  </Text>
                </Table.Td>
                {v ? (
                  <>
                    <Table.Td ta="right" fw={600}>
                      {formatIops(v.iops.total)}
                    </Table.Td>
                    <Table.Td ta="right">
                      {formatIops(v.iops.read)} / {formatIops(v.iops.write)}
                    </Table.Td>
                    <Table.Td ta="right" c={readColor} fw={readColor ? 600 : undefined}>
                      {formatLatency(v.latency_ms.read)}
                    </Table.Td>
                    <Table.Td ta="right" c={writeColor} fw={writeColor ? 600 : undefined}>
                      {formatLatency(v.latency_ms.write)}
                    </Table.Td>
                    <Table.Td ta="right">
                      <Tooltip label={`Lesen ${formatThroughput(v.throughput.read)} · Schreiben ${formatThroughput(v.throughput.write)}`}>
                        <span>{formatThroughput(v.throughput.total)}</span>
                      </Tooltip>
                    </Table.Td>
                  </>
                ) : (
                  <Table.Td colSpan={5}>
                    <Text size="xs" c="dimmed">
                      {r.note ?? "Keine Messwerte"}
                    </Text>
                  </Table.Td>
                )}
              </Table.Tr>
            );
          })}
          {rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={showHyperV ? 9 : 7}>
                <Text size="sm" c="dimmed" ta="center" py="md">
                  Keine Objekte.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Box>
  );
}

export function PerformancePage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "hyperv";
  const { data, isLoading, error, refetch, isFetching, dataUpdatedAt } = usePerformanceOverview();
  const [sortKey, setSortKey] = useState<SortKey>("iops");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<PerfRow | null>(null);

  const hyperv = useMemo(() => sortRows((data?.rows ?? []).filter((r) => r.kind !== "volume"), sortKey), [data, sortKey]);
  const volumes = useMemo(
    () =>
      sortRows(
        (data?.rows ?? []).filter(
          (r) => r.kind === "volume" && matchesAllColumns({ name: r.name, svm: r.svm_name, system: r.netapp_cluster_name }, search),
        ),
        sortKey,
      ),
    [data, sortKey, search],
  );
  const selectedKey = selected ? rowKey(selected) : null;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={2}>Performance</Title>
        <Group>
          {dataUpdatedAt > 0 && (
            <Text size="xs" c="dimmed">
              Stand {new Date(dataUpdatedAt).toLocaleTimeString("de-DE")} · alle 30 s aktualisiert
            </Text>
          )}
          <ActionIcon variant="default" size="lg" onClick={() => refetch()} loading={isFetching} aria-label="Aktualisieren">
            <IconRefresh size={16} />
          </ActionIcon>
        </Group>
      </Group>
      {error && <Alert color="red">{apiErrorMessage(error, "Performance-Daten konnten nicht geladen werden.")}</Alert>}
      {data?.errors.map((e) => (
        <Alert key={e} color="orange" variant="light">
          {e}
        </Alert>
      ))}

      {selected && (
        <Paper withBorder p="sm">
          <Group justify="space-between" mb="xs">
            <Text fw={600}>
              Verlauf: {selected.name}
              {selected.kind !== "volume" && (
                <Text span size="sm" c="dimmed" fw={400}>
                  {" "}
                  ({selected.object_type === "lun" ? `LUN ${lunShortName(selected.object_name)}` : `Volume ${selected.object_name}`})
                </Text>
              )}
            </Text>
            <ActionIcon variant="subtle" size="sm" onClick={() => setSelected(null)} aria-label="Schließen">
              <IconX size={14} />
            </ActionIcon>
          </Group>
          <PerformancePanel netappClusterId={selected.netapp_cluster_id} objectType={selected.object_type} uuid={selected.object_uuid} />
        </Paper>
      )}

      <Tabs value={tab} onChange={(v) => setParams({ tab: v ?? "hyperv" })}>
        <Tabs.List>
          <Tabs.Tab value="hyperv">Hyper-V-Storage ({hyperv.length})</Tabs.Tab>
          <Tabs.Tab value="volumes">Alle Volumes ({data?.rows.filter((r) => r.kind === "volume").length ?? 0})</Tabs.Tab>
        </Tabs.List>
        <Group justify="space-between" mt="sm" mb="xs">
          <Group>
            {tab === "volumes" && <SearchInput value={search} onChange={setSearch} />}
            <SegmentedControl
              size="xs"
              value={sortKey}
              onChange={(v) => setSortKey(v as SortKey)}
              data={[
                { value: "iops", label: "nach IOPS" },
                { value: "latency", label: "nach Latenz" },
                { value: "throughput", label: "nach Durchsatz" },
                { value: "name", label: "nach Name" },
              ]}
            />
          </Group>
          <Text size="xs" c="dimmed">
            Werte: Mittel der letzten 15 s laut ONTAP. Latenz orange ab 10 ms, rot ab 20 ms (erst ab 10 IOPS bewertet). Zeile anklicken für den Verlauf.
          </Text>
        </Group>
        <Tabs.Panel value="hyperv">
          {isLoading ? (
            <Text size="sm" c="dimmed">
              Lade Messwerte …
            </Text>
          ) : (
            <PerfTable rows={hyperv} selected={selectedKey} onSelect={setSelected} showHyperV />
          )}
        </Tabs.Panel>
        <Tabs.Panel value="volumes">
          <PerfTable rows={volumes} selected={selectedKey} onSelect={setSelected} showHyperV={false} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
