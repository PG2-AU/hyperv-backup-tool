import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  List,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Stepper,
  Table,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import {
  useCsvCreateHyperVOptions,
  useCsvCreateKeep,
  useCsvCreateNetAppOptions,
  useCsvCreateRollback,
  useCsvCreateRun,
  useStartCsvCreate,
} from "@/api/hooks.csvCreate";
import { useHyperVClusters, useNetAppClusters, useResourceGroups } from "@/api/hooks";
import type { CsvCreateHyperVOptions, CsvCreateNetAppOptions, CsvCreateRun } from "@/api/types";
import { UsageBar } from "@/components/CsvResizeModal";
import { useAuthStore } from "@/store/authStore";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

const GIB = 1024 ** 3;

interface CsvCreateModalProps {
  opened: boolean;
  onClose: () => void;
}

// Neue CSV per Assistent (Backlog #68, Nutzer-Vorgaben 2026-09-30): Volume +
// LUN auf der NetApp, Mapping auf die igroups der Knoten, Formatieren auf
// einem Knoten, Cluster-Disk + CSV, optional Protection Group. Bei einem
// Fehler wird nichts automatisch entfernt -- der Ablauf fragt nach.
export function CsvCreateModal({ opened, onClose }: CsvCreateModalProps) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: run } = useCsvCreateRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      queryClient.removeQueries({ queryKey: ["csv-create-hyperv"] });
      queryClient.removeQueries({ queryKey: ["csv-create-netapp"] });
    }
  }, [opened, queryClient]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title="Neue CSV anlegen"
      size={960}
    >
      {runId ? <RunView runId={runId} onClose={onClose} /> : <CreateForm opened={opened} onStarted={setRunId} onClose={onClose} />}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

// --- Hilfen -------------------------------------------------------------------------

// Wie normalize_initiator im Backend: IQN kleingeschrieben, WWPN ohne ':'/'-'.
function normalizeInitiator(address: string): string {
  const v = address.trim().toLowerCase();
  return /^(iqn|eui|naa)\./.test(v) ? v : v.replace(/[:-]/g, "");
}

function nextCsvName(existing: string[]): string {
  const numbers = existing.map((n) => /^csv(\d+)$/i.exec(n)).filter(Boolean).map((m) => Number(m![1]));
  const next = (numbers.length ? Math.max(...numbers) : 0) + 1;
  return `CSV${String(next).padStart(2, "0")}`;
}

function volumeNameFor(csv: string): string {
  const base = csv.toLowerCase().replace(/[^a-z0-9_]/g, "_");
  return `vol_${base}`;
}

function lunNameFor(csv: string): string {
  return csv.replace(/[^A-Za-z0-9_.-]/g, "_");
}

const ACTIVE_STATES = ["Up", "Paused"];

// --- Formular -----------------------------------------------------------------------

function CreateForm({ opened, onStarted, onClose }: { opened: boolean; onStarted: (id: string) => void; onClose: () => void }) {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canAssignGroup = hasPermission("backup:create");
  const { data: hypervClusters } = useHyperVClusters();
  const { data: netappClusters } = useNetAppClusters();
  const { data: resourceGroups } = useResourceGroups();
  const start = useStartCsvCreate();

  const [clusterId, setClusterId] = useState<string | null>(null);
  const [netappId, setNetappId] = useState<string | null>(null);
  useEffect(() => {
    if (!clusterId && hypervClusters?.length === 1) setClusterId(hypervClusters[0].id);
  }, [hypervClusters, clusterId]);
  useEffect(() => {
    if (!netappId && netappClusters?.length === 1) setNetappId(netappClusters[0].id);
  }, [netappClusters, netappId]);

  const hv = useCsvCreateHyperVOptions(clusterId, opened);
  const na = useCsvCreateNetAppOptions(netappId, opened);

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Select
          label="Hyper-V-Cluster"
          data={(hypervClusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={clusterId}
          onChange={setClusterId}
          required
        />
        <Select
          label="NetApp-System"
          data={(netappClusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={netappId}
          onChange={setNetappId}
          required
        />
      </SimpleGrid>
      {(hv.isLoading || na.isLoading) && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm">
            {hv.isLoading ? "Knoten und Initiatoren werden live abgefragt… " : ""}
            {na.isLoading ? "SVMs, Aggregate und igroups werden live abgefragt…" : ""}
          </Text>
        </Group>
      )}
      {hv.error && <Alert color="red">{apiErrorMessage(hv.error, "Hyper-V-Abfrage fehlgeschlagen.")}</Alert>}
      {na.error && <Alert color="red">{apiErrorMessage(na.error, "NetApp-Abfrage fehlgeschlagen.")}</Alert>}
      {hv.data && na.data && clusterId && netappId && (
        <Details
          key={`${clusterId}-${netappId}`}
          clusterId={clusterId}
          netappId={netappId}
          hv={hv.data}
          na={na.data}
          groups={canAssignGroup ? (resourceGroups ?? []).filter((g) => g.scope === "csv") : []}
          canAssignGroup={canAssignGroup}
          starting={start.isPending}
          onClose={onClose}
          onStart={(payload) =>
            start
              .mutateAsync(payload)
              .then((r) => onStarted(r.id))
              .catch((err) =>
                notifications.show({
                  title: "Fehler",
                  message: apiErrorMessage(err, "Anlegen konnte nicht gestartet werden."),
                  color: "red",
                }),
              )
          }
        />
      )}
      {!(hv.data && na.data) && (
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
        </Group>
      )}
    </Stack>
  );
}

function Details({
  clusterId,
  netappId,
  hv,
  na,
  groups,
  canAssignGroup,
  starting,
  onClose,
  onStart,
}: {
  clusterId: string;
  netappId: string;
  hv: CsvCreateHyperVOptions;
  na: CsvCreateNetAppOptions;
  groups: { id: string; name: string; policies: { name: string }[] }[];
  canAssignGroup: boolean;
  starting: boolean;
  onClose: () => void;
  onStart: (payload: Parameters<ReturnType<typeof useStartCsvCreate>["mutateAsync"]>[0]) => void;
}) {
  const [svmName, setSvmName] = useState<string | null>(na.svms.length === 1 ? na.svms[0].name : null);
  const bestAggregate = [...na.aggregates]
    .filter((a) => !a.state || a.state === "online")
    .sort((a, b) => (b.available_bytes ?? 0) - (a.available_bytes ?? 0))[0];
  const [aggregateName, setAggregateName] = useState<string | null>(bestAggregate?.name ?? null);
  const [csvName, setCsvName] = useState(() => nextCsvName(hv.csv_names));
  const [volumeName, setVolumeName] = useState<string | null>(null);
  const [lunName, setLunName] = useState<string | null>(null);
  const [lunGb, setLunGb] = useState<number | string>(500);
  const [bufferPct, setBufferPct] = useState<number | string>(20);
  const [autosize, setAutosize] = useState(true);
  const [igroups, setIgroups] = useState<string[] | null>(null);
  const [fileSystem, setFileSystem] = useState<"NTFS" | "ReFS">("NTFS");
  const [allocation, setAllocation] = useState<string>("65536");
  const [renameFolder, setRenameFolder] = useState(true);
  const [groupId, setGroupId] = useState<string | null>(null);

  const effVolumeName = volumeName ?? volumeNameFor(csvName);
  const effLunName = lunName ?? lunNameFor(csvName);
  const lunBytes = Math.round(Number(lunGb) * GIB) || 0;
  const volumeBytes = Math.ceil((lunBytes * (1 + (Number(bufferPct) || 0) / 100)) / GIB) * GIB;

  const activeNodes = hv.nodes.filter((n) => ACTIVE_STATES.includes(n.state));
  const svmIgroups = useMemo(() => na.igroups.filter((g) => g.svm_name === svmName), [na.igroups, svmName]);
  // igroup -> Knoten, deren IQN/WWPN darin steht
  const igroupNodes = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const g of svmIgroups) {
      const set = new Set(g.initiators.map(normalizeInitiator));
      map.set(
        g.name,
        hv.nodes.filter((n) => n.initiators.some((i) => set.has(normalizeInitiator(i.address)))).map((n) => n.name),
      );
    }
    return map;
  }, [svmIgroups, hv.nodes]);

  // Vorschlag: eine igroup, die alle aktiven Knoten enthaelt -- sonst alle,
  // die mindestens einen Knoten enthalten (z.B. eine igroup je Knoten).
  const suggested = useMemo(() => {
    const full = svmIgroups.find((g) => activeNodes.every((n) => igroupNodes.get(g.name)?.includes(n.name)));
    if (full) return [full.name];
    return svmIgroups.filter((g) => (igroupNodes.get(g.name) ?? []).length > 0).map((g) => g.name);
  }, [svmIgroups, igroupNodes, activeNodes]);
  const selectedIgroups = igroups ?? suggested;

  const covered = new Set(selectedIgroups.flatMap((g) => igroupNodes.get(g) ?? []));
  const uncovered = activeNodes.filter((n) => !covered.has(n.name));
  const aggregate = na.aggregates.find((a) => a.name === aggregateName);

  const errors: string[] = [];
  const warnings: string[] = [];
  const notes: string[] = [];
  if (hv.busy_reason) errors.push(hv.busy_reason);
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/.test(csvName))
    errors.push("CSV-Name: nur Buchstaben, Ziffern, '_', '-', '.' (max. 63 Zeichen).");
  if (hv.csv_names.some((n) => n.toLowerCase() === csvName.toLowerCase())) errors.push(`CSV '${csvName}' gibt es bereits.`);
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,202}$/.test(effVolumeName)) errors.push("Volume-Name: nur Buchstaben, Ziffern und '_'.");
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,254}$/.test(effLunName)) errors.push("LUN-Name: nur Buchstaben, Ziffern, '_', '-', '.'.");
  if (lunBytes < GIB) errors.push("Die LUN muss mindestens 1 GB groß sein.");
  if (!svmName) errors.push("SVM wählen.");
  if (svmName && selectedIgroups.length === 0) errors.push("Mindestens eine Initiator-Gruppe wählen.");
  if (selectedIgroups.length > 0 && uncovered.length > 0)
    warnings.push(
      `Kein Initiator von ${uncovered.map((n) => n.name).join(", ")} steht in den gewählten igroups -- dieser Knoten würde die LUN nicht sehen, das Anlegen bricht dann beim Einlesen ab.`,
    );
  const nodesWithoutInitiators = hv.nodes.filter((n) => n.error);
  if (nodesWithoutInitiators.length)
    warnings.push(`Initiatoren nicht lesbar: ${nodesWithoutInitiators.map((n) => `${n.name} (${n.error})`).join("; ")}`);
  const down = hv.nodes.filter((n) => !ACTIVE_STATES.includes(n.state));
  if (down.length) notes.push(`Nicht betriebsbereit (wird übersprungen): ${down.map((n) => `${n.name} (${n.state})`).join(", ")}.`);
  if (aggregate?.available_bytes != null && volumeBytes > aggregate.available_bytes)
    warnings.push(
      `Das Volume (${formatBytes(volumeBytes)}) ist größer als der freie Platz im Aggregat (${formatBytes(aggregate.available_bytes)}). Thin geht das, aber das Aggregat läuft voll, wenn die CSV voll beschrieben wird.`,
    );
  if (!na.aggregates.length)
    notes.push("Aggregate sind für dieses System nicht sichtbar (SVM-Login) -- ONTAP wählt das Aggregat selbst.");
  if (fileSystem === "ReFS")
    warnings.push(
      "ReFS auf einer SAN-CSV läuft immer im umgeleiteten Modus (File System Redirected) -- jeder Knoten außer dem Owner schreibt über das Netz. Für LUN-basierte CSVs empfiehlt Microsoft NTFS.",
    );
  const group = groups.find((g) => g.id === groupId);

  function handleStart() {
    onStart({
      cluster_id: clusterId,
      netapp_cluster_id: netappId,
      svm_name: svmName!,
      aggregate_name: aggregateName,
      csv_name: csvName,
      volume_name: effVolumeName,
      lun_name: effLunName,
      lun_size_bytes: lunBytes,
      volume_size_bytes: volumeBytes,
      autosize_grow: autosize,
      igroup_names: selectedIgroups,
      file_system: fileSystem,
      allocation_unit: Number(allocation) as 4096 | 65536,
      rename_folder: renameFolder,
      resource_group_id: groupId,
    });
  }

  return (
    <Stack gap="md">
      <Divider label="NetApp" labelPosition="left" />
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Select
          label="SVM"
          data={na.svms.map((s) => ({ value: s.name, label: s.allowed_protocols ? `${s.name} (${s.allowed_protocols})` : s.name }))}
          value={svmName}
          onChange={(v) => {
            setSvmName(v);
            setIgroups(null);
          }}
          required
          searchable
        />
        <Select
          label="Aggregat"
          placeholder={na.aggregates.length ? undefined : "automatisch (ONTAP wählt)"}
          data={na.aggregates.map((a) => ({
            value: a.name,
            label: a.available_bytes != null ? `${a.name} · frei ${formatBytes(a.available_bytes)}` : a.name,
          }))}
          value={aggregateName}
          onChange={setAggregateName}
          clearable={!na.aggregates.some((a) => a.available_bytes != null)}
          disabled={!na.aggregates.length}
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <TextInput label="CSV-Name" required value={csvName} onChange={(e) => setCsvName(e.currentTarget.value.trim())} />
        <TextInput
          label="Volume-Name"
          required
          value={effVolumeName}
          onChange={(e) => setVolumeName(e.currentTarget.value.trim())}
        />
        <TextInput label="LUN-Name" required value={effLunName} onChange={(e) => setLunName(e.currentTarget.value.trim())} />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <NumberInput
          label="LUN-/CSV-Größe"
          suffix=" GB"
          min={1}
          decimalScale={0}
          thousandSeparator="."
          decimalSeparator=","
          value={lunGb}
          onChange={setLunGb}
          required
        />
        <NumberInput
          label="Puffer für Snapshots"
          description={`Volume = LUN + Puffer = ${formatBytes(volumeBytes)}`}
          suffix=" %"
          min={0}
          max={200}
          value={bufferPct}
          onChange={setBufferPct}
        />
        <Checkbox
          mt="xl"
          label="Volume-Autosize (grow)"
          description="ONTAP vergrößert das Volume, bevor Snapshots es füllen"
          checked={autosize}
          onChange={(e) => setAutosize(e.currentTarget.checked)}
        />
      </SimpleGrid>

      {lunBytes > 0 && (
        <Stack gap="sm">
          {aggregate?.size_bytes && aggregate.available_bytes != null && (
            <UsageBar
              title={`Aggregat ${aggregate.name}`}
              scale={aggregate.size_bytes}
              segments={[
                {
                  value: aggregate.size_bytes - aggregate.available_bytes,
                  color: "gray.6",
                  label: `belegt ${formatBytes(aggregate.size_bytes - aggregate.available_bytes)}`,
                },
                { value: Math.min(volumeBytes, aggregate.available_bytes), color: "teal.3", label: `neues Volume ${formatBytes(volumeBytes)} (thin)`, striped: true },
              ]}
              summary={`frei ${formatBytes(aggregate.available_bytes)}`}
              level={volumeBytes > aggregate.available_bytes ? "warn" : "ok"}
            />
          )}
          <UsageBar
            title={`Volume ${effVolumeName}`}
            scale={volumeBytes}
            segments={[
              { value: lunBytes, color: "blue.6", label: `LUN ${formatBytes(lunBytes)}` },
              { value: volumeBytes - lunBytes, color: "grape.3", label: `Puffer für Snapshots ${formatBytes(volumeBytes - lunBytes)}` },
            ]}
            summary={`${formatBytes(volumeBytes)} · thin · Snapshot-Policy none · Reserve 0 %`}
          />
        </Stack>
      )}

      <Divider label="Mapping" labelPosition="left" />
      <MultiSelect
        label="Initiator-Gruppen"
        description="Vorschlag anhand der IQNs/WWPNs der Cluster-Knoten; gleiche LUN-ID auf allen igroups"
        data={svmIgroups.map((g) => {
          const n = igroupNodes.get(g.name) ?? [];
          return {
            value: g.name,
            label: `${g.name} (${g.protocol ?? "?"}, ${g.os_type ?? "?"}${n.length ? `, Knoten: ${n.join(", ")}` : ", kein Cluster-Knoten"})`,
          };
        })}
        value={selectedIgroups}
        onChange={setIgroups}
        disabled={!svmName}
        searchable
      />
      <Table withTableBorder withColumnBorders fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Knoten</Table.Th>
            <Table.Th>Status</Table.Th>
            <Table.Th>Initiatoren</Table.Th>
            <Table.Th>Sieht die LUN über</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {hv.nodes.map((n) => {
            const via = selectedIgroups.filter((g) => igroupNodes.get(g)?.includes(n.name));
            const active = ACTIVE_STATES.includes(n.state);
            return (
              <Table.Tr key={n.name}>
                <Table.Td>{n.name}</Table.Td>
                <Table.Td>{n.state}</Table.Td>
                <Table.Td>{n.error ? <Text c="red" size="xs">nicht lesbar</Text> : n.initiators.map((i) => i.address).join(", ") || "–"}</Table.Td>
                <Table.Td>
                  {via.length ? (
                    <Badge color="green" variant="light" size="sm">
                      {via.join(", ")}
                    </Badge>
                  ) : active ? (
                    <Badge color="red" variant="light" size="sm">
                      keine igroup
                    </Badge>
                  ) : (
                    "–"
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>

      <Divider label="Windows / Cluster" labelPosition="left" />
      <Group align="flex-end" gap="xl">
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Dateisystem
          </Text>
          <SegmentedControl value={fileSystem} onChange={(v) => setFileSystem(v as "NTFS" | "ReFS")} data={["NTFS", "ReFS"]} />
        </Stack>
        <Select
          label="Blockgröße"
          data={[
            { value: "65536", label: "64 KB (empfohlen für VHDX)" },
            { value: "4096", label: "4 KB" },
          ]}
          value={allocation}
          onChange={(v) => setAllocation(v ?? "65536")}
          w={240}
        />
        <Checkbox
          label={`Mount-Ordner in „${csvName}“ umbenennen`}
          description="statt C:\ClusterStorage\VolumeN"
          checked={renameFolder}
          onChange={(e) => setRenameFolder(e.currentTarget.checked)}
        />
      </Group>

      {canAssignGroup && (
        <Select
          label="Protection Group (optional)"
          placeholder="keine -- später zuordnen"
          data={groups.map((g) => ({ value: g.id, label: `${g.name}${g.policies.length ? ` (${g.policies.map((p) => p.name).join(", ")})` : ""}` }))}
          value={groupId}
          onChange={setGroupId}
          clearable
        />
      )}

      {errors.map((e) => (
        <Alert key={e} color="red" icon={<IconX size={16} />} py={6}>
          {e}
        </Alert>
      ))}
      {warnings.map((w) => (
        <Alert key={w} color="yellow" icon={<IconAlertTriangle size={16} />} py={6}>
          {w}
        </Alert>
      ))}
      {notes.map((n) => (
        <Alert key={n} color="blue" variant="light" icon={<IconInfoCircle size={16} />} py={6}>
          {n}
        </Alert>
      ))}

      <Group justify="space-between">
        <Text size="xs" c="dimmed" maw={600}>
          Ablauf: Volume → LUN (hyper_v, thin, Space Allocation) → Mapping → Einlesen auf allen Knoten → GPT + {fileSystem}{" "}
          {Number(allocation) / 1024} KB → Cluster-Disk → CSV{renameFolder ? " → Ordner umbenennen" : ""} → Inventory
          {group ? ` → Protection Group ${group.name}` : ""}
        </Text>
        <Group>
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleStart} loading={starting} disabled={errors.length > 0}>
            CSV anlegen
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

// --- Ablauf -------------------------------------------------------------------------

function createdObjects(run: CsvCreateRun): string[] {
  const items: string[] = [];
  if (run.csv_added) items.push(`CSV '${run.cluster_resource_name}'${run.csv_path ? ` (${run.csv_path})` : ""}`);
  else if (run.cluster_resource_name) items.push(`Cluster-Disk '${run.cluster_resource_name}'`);
  if (run.disk_formatted && !run.cluster_resource_name) items.push(`formatierte Disk (online auf ${run.format_node})`);
  if (run.mapped_igroups.length) items.push(`LUN-Mapping auf ${run.mapped_igroups.join(", ")} (LUN-ID ${run.lun_id ?? "?"})`);
  if (run.created_lun_uuid) items.push(`LUN /vol/${run.volume_name}/${run.lun_name}`);
  if (run.created_volume_uuid) items.push(`Volume ${run.volume_name} auf ${run.svm_name}`);
  return items;
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useCsvCreateRun(runId);
  const rollback = useCsvCreateRollback();
  const keep = useCsvCreateKeep();
  if (!run) return <Loader size="sm" />;
  const active = run.steps.findIndex((s) => s.status === "running");
  const activeIndex = run.status === "running" ? (active === -1 ? run.steps.length : active) : run.steps.length;
  const askRollback = run.status === "failed" && run.has_created_objects && !run.rollback_declined;
  const onError = (err: unknown) =>
    notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Aktion fehlgeschlagen."), color: "red" });

  return (
    <Stack gap="md">
      <Stepper active={activeIndex} size="sm" orientation="vertical" allowNextStepsSelect={false}>
        {run.steps.map((s) => (
          <Stepper.Step
            key={s.step}
            label={s.label}
            description={s.message && s.message !== "OK" ? <span style={{ whiteSpace: "pre-line" }}>{s.message}</span> : undefined}
            color={s.status === "error" ? "red" : undefined}
            loading={s.status === "running"}
            completedIcon={s.status === "error" ? <IconX size={16} /> : <IconCheck size={16} />}
          />
        ))}
      </Stepper>
      {run.status === "running" && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm">Läuft…</Text>
        </Group>
      )}
      {run.status === "succeeded" && !run.error_message && (
        <Alert color="green" icon={<IconCheck size={16} />}>
          CSV {run.csv_name} angelegt ({formatBytes(run.lun_size_bytes)}){run.csv_path ? ` unter ${run.csv_path}` : ""}.
        </Alert>
      )}
      {run.status === "succeeded" && run.error_message && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          CSV {run.csv_name} angelegt und nutzbar. {run.error_message}
        </Alert>
      )}
      {run.status === "cleaned_up" && (
        <Alert color="gray" icon={<IconCheck size={16} />}>
          Vollständig zurückgerollt -- alle von diesem Lauf angelegten Objekte wurden entfernt.
        </Alert>
      )}
      {run.status === "failed" && (
        <Alert color="red" icon={<IconX size={16} />}>
          {run.error_message}
        </Alert>
      )}
      {askRollback && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Angelegte Objekte zurückrollen?">
          <Text size="sm">Dieser Lauf hat bereits angelegt:</Text>
          <List size="sm" my="xs">
            {createdObjects(run).map((o) => (
              <List.Item key={o}>{o}</List.Item>
            ))}
          </List>
          <Text size="sm" mb="sm">
            „Zurückrollen“ entfernt genau diese Objekte in umgekehrter Reihenfolge. „Behalten“ lässt alles stehen, z. B. um
            den Fehler zu beheben und die Schritte von Hand zu Ende zu führen.
          </Text>
          <Group>
            <Button color="red" loading={rollback.isPending} onClick={() => rollback.mutateAsync(run.id).catch(onError)}>
              Zurückrollen
            </Button>
            <Button variant="default" loading={keep.isPending} onClick={() => keep.mutateAsync(run.id).catch(onError)}>
              Behalten
            </Button>
          </Group>
        </Alert>
      )}
      {run.status === "failed" && run.rollback_declined && run.has_created_objects && (
        <Text size="sm" c="dimmed">
          Behalten: {createdObjects(run).join(" · ")}
        </Text>
      )}
      {run.status !== "running" && !askRollback && (
        <Group justify="flex-end">
          <Button onClick={onClose}>Schließen</Button>
        </Group>
      )}
    </Stack>
  );
}
