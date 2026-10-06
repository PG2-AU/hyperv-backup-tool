import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  List,
  Loader,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Stepper,
  Switch,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconArrowBackUp, IconCheck, IconPlus, IconTrash, IconX } from "@tabler/icons-react";

import { useStartVmSettings, useVmSettingsInfo, useVmSettingsRun } from "@/api/hooks.vmSettings";
import type { VmSettingsInfo, VmSettingsRequest } from "@/api/hooks.vmSettings";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

interface VmSettingsModalProps {
  vm: { name: string; cluster_id?: string | null } | null;
  onClose: () => void;
}

const MB = 1024 ** 2;
const GB = 1024 ** 3;
const DISCONNECTED = "__none__";

// GB-Eingabe -> Bytes: Arbeitsspeicher in 2-MB-Schritten, Disks in ganzen MB.
const memoryBytes = (gb: number) => Math.round((gb * GB) / (2 * MB)) * 2 * MB;
const diskBytes = (gb: number) => Math.round((gb * GB) / MB) * MB;
const toGb = (bytes: number) => Math.round((bytes / GB) * 1000) / 1000;
const num = (v: number | string, fallback: number) => (typeof v === "number" && !Number.isNaN(v) ? v : fallback);

// VM-Einstellungen aendern (Backlog #81): CPU, RAM, Netzwerkadapter,
// Festplatten. Der Dialog fragt den Live-Stand ab und zeigt je Bereich, was
// im aktuellen Zustand der VM moeglich ist; gesendet wird der gewuenschte
// Stand, das Backend fuehrt nur echte Abweichungen aus.
export function VmSettingsModal({ vm, onClose }: VmSettingsModalProps) {
  const opened = vm !== null;
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: info, isLoading, error } = useVmSettingsInfo(vm?.cluster_id, vm?.name, opened && !runId);
  const { data: run } = useVmSettingsRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) setRunId(undefined);
  }, [opened]);

  return (
    <Modal opened={opened} onClose={onClose} closeOnClickOutside={false} title={`VM-Einstellungen: ${vm?.name ?? ""}`} size={920}>
      {runId ? (
        <RunView runId={runId} onClose={onClose} />
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">Aktuelle Einstellungen werden live vom Hyper-V-Knoten abgefragt…</Text>
            </Group>
          )}
          {error && <Alert color="red">{apiErrorMessage(error, "Abfrage fehlgeschlagen.")}</Alert>}
          {info && <SettingsForm info={info} onStarted={setRunId} onClose={onClose} />}
        </Stack>
      )}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

interface AdapterDraft {
  id: string;
  switchName: string; // DISCONNECTED = getrennt
  vlan: number | string;
  remove: boolean;
}

interface NewAdapterDraft {
  key: number;
  switchName: string;
  vlan: number | string;
}

interface NewDiskDraft {
  key: number;
  sizeGb: number | string;
  dynamic: boolean;
}

function SettingsForm({ info, onStarted, onClose }: { info: VmSettingsInfo; onStarted: (id: string) => void; onClose: () => void }) {
  const start = useStartVmSettings();
  const blocked = info.blocked_reasons.length > 0;

  const [cpu, setCpu] = useState<number | string>(info.cpu_count);
  const [memGb, setMemGb] = useState<number | string>(toGb(info.memory_startup_bytes));
  const [dynamic, setDynamic] = useState(info.dynamic_memory_enabled);
  const [minGb, setMinGb] = useState<number | string>(toGb(info.dynamic_memory_enabled ? info.memory_minimum_bytes : 512 * MB));
  const [maxGb, setMaxGb] = useState<number | string>(
    toGb(info.dynamic_memory_enabled ? info.memory_maximum_bytes : Math.max(info.memory_startup_bytes, 4 * GB)),
  );
  const [adapters, setAdapters] = useState<AdapterDraft[]>(
    info.adapters.map((a) => ({ id: a.id, switchName: a.switch_name ?? DISCONNECTED, vlan: a.vlan_id ?? "", remove: false })),
  );
  const [newAdapters, setNewAdapters] = useState<NewAdapterDraft[]>([]);
  const [diskGb, setDiskGb] = useState<Record<string, number | string>>(
    Object.fromEntries(info.disks.map((d) => [d.path, d.size_bytes != null ? toGb(d.size_bytes) : ""])),
  );
  const [newDisks, setNewDisks] = useState<NewDiskDraft[]>([]);

  const switchOptions = [
    { value: DISCONNECTED, label: "— getrennt —" },
    ...info.switches.map((s) => ({ value: s.name, label: s.type ? `${s.name} (${s.type})` : s.name })),
  ];
  // Ein bereits verbundener Switch, den die Knoten-Abfrage nicht lieferte, bleibt waehlbar.
  for (const a of info.adapters) {
    if (a.switch_name && !switchOptions.some((o) => o.value === a.switch_name)) switchOptions.push({ value: a.switch_name, label: a.switch_name });
  }
  const defaultSwitch = info.switches[0]?.name ?? "";

  const payload: VmSettingsRequest = useMemo(() => {
    const byId = new Map(info.adapters.map((a) => [a.id, a]));
    return {
      cluster_id: info.cluster_id,
      vm_name: info.vm_name,
      cpu_count: info.hardware_editable ? num(cpu, info.cpu_count) : null,
      memory: info.hardware_editable
        ? {
            startup_bytes: memoryBytes(num(memGb, toGb(info.memory_startup_bytes))),
            dynamic,
            minimum_bytes: dynamic ? memoryBytes(num(minGb, 0.5)) : null,
            maximum_bytes: dynamic ? memoryBytes(num(maxGb, 4)) : null,
          }
        : null,
      // Adapter mit besonderem VLAN-Modus (Trunk o.ae.) gar nicht mitsenden
      adapters: adapters
        .filter((a) => !a.remove && ["Access", "Untagged"].includes(byId.get(a.id)?.vlan_mode ?? "Untagged"))
        .map((a) => ({
          id: a.id,
          switch_name: a.switchName === DISCONNECTED ? null : a.switchName,
          vlan_id: typeof a.vlan === "number" && a.vlan > 0 ? a.vlan : null,
        })),
      add_adapters: newAdapters
        .filter((a) => a.switchName)
        .map((a) => ({ switch_name: a.switchName, vlan_id: typeof a.vlan === "number" && a.vlan > 0 ? a.vlan : null })),
      remove_adapter_ids: adapters.filter((a) => a.remove).map((a) => a.id),
      expand_disks: info.disks
        // nur, wenn die Eingabe vom angezeigten Ausgangswert abweicht (krumme
        // Byte-Groessen wuerden sonst durch die GB-Rundung als Aenderung gelten)
        .filter(
          (d) =>
            d.size_bytes != null &&
            !d.expand_blocked_reason &&
            typeof diskGb[d.path] === "number" &&
            diskGb[d.path] !== toGb(d.size_bytes),
        )
        .map((d) => ({ path: d.path, size_bytes: diskBytes(diskGb[d.path] as number) })),
      add_disks: newDisks
        .filter((d) => typeof d.sizeGb === "number" && d.sizeGb > 0)
        .map((d) => ({ size_bytes: diskBytes(d.sizeGb as number), dynamic: d.dynamic })),
    };
  }, [info, cpu, memGb, dynamic, minGb, maxGb, adapters, newAdapters, diskGb, newDisks]);

  // Grobe lokale Zusammenfassung fuer den Knopf -- massgeblich ist die Pruefung im Backend.
  const changes: string[] = [];
  if (payload.cpu_count != null && payload.cpu_count !== info.cpu_count) changes.push(`vCPU ${info.cpu_count} → ${payload.cpu_count}`);
  if (payload.memory) {
    const m = payload.memory;
    const same =
      m.startup_bytes === info.memory_startup_bytes &&
      m.dynamic === info.dynamic_memory_enabled &&
      (!m.dynamic || (m.minimum_bytes === info.memory_minimum_bytes && m.maximum_bytes === info.memory_maximum_bytes));
    if (!same)
      changes.push(
        `Arbeitsspeicher ${formatBytes(m.startup_bytes)}` +
          (m.dynamic ? `, dynamisch ${formatBytes(m.minimum_bytes)}–${formatBytes(m.maximum_bytes)}` : ", statisch"),
      );
  }
  for (const a of payload.adapters) {
    const current = info.adapters.find((x) => x.id === a.id);
    if (current && ((current.switch_name ?? null) !== a.switch_name || (current.vlan_id ?? null) !== a.vlan_id))
      changes.push(`Adapter ${current.mac_address ?? current.name} → ${a.switch_name ?? "getrennt"}${a.vlan_id ? ` VLAN ${a.vlan_id}` : ""}`);
  }
  for (const id of payload.remove_adapter_ids) {
    const current = info.adapters.find((x) => x.id === id);
    changes.push(`Adapter ${current?.mac_address ?? current?.name ?? ""} entfernen`);
  }
  for (const a of payload.add_adapters) changes.push(`Neuer Adapter an ${a.switch_name}${a.vlan_id ? ` VLAN ${a.vlan_id}` : ""}`);
  for (const e of payload.expand_disks) {
    const disk = info.disks.find((d) => d.path === e.path);
    changes.push(`${disk?.name} vergrößern ${formatBytes(disk?.size_bytes)} → ${formatBytes(e.size_bytes)}`);
  }
  for (const d of payload.add_disks) changes.push(`Neue Festplatte ${formatBytes(d.size_bytes)} (${d.dynamic ? "dynamisch" : "fest"})`);

  function handleStart() {
    start
      .mutateAsync(payload)
      .then((r) => onStarted(r.id))
      .catch((err) =>
        notifications.show({ title: "Nicht möglich", message: apiErrorMessage(err, "Änderung konnte nicht gestartet werden."), color: "red" }),
      );
  }

  const offHint = info.hardware_editable ? undefined : "nur bei ausgeschalteter VM";

  return (
    <Stack gap="md">
      <Group gap="lg">
        <Text size="sm">
          <Text span c="dimmed" size="xs">
            Knoten{" "}
          </Text>
          {info.node}
        </Text>
        <Badge variant="light" color={info.state === "Running" ? "green" : info.state === "Off" ? "gray" : "yellow"}>
          {info.state}
        </Badge>
        <Text size="sm">Generation {info.generation}</Text>
        {info.checkpoint_count > 0 && <Text size="sm">{info.checkpoint_count} Checkpoint(s)</Text>}
      </Group>

      {info.blocked_reasons.map((r) => (
        <Alert key={r} color="red" icon={<IconX size={16} />} py={6}>
          {r}
        </Alert>
      ))}
      {info.warnings.map((w) => (
        <Alert key={w} color="yellow" icon={<IconAlertTriangle size={16} />} py={6}>
          {w}
        </Alert>
      ))}

      <Divider label="Prozessor und Arbeitsspeicher" labelPosition="left" />
      <SimpleGrid cols={{ base: 1, sm: 4 }} spacing="sm">
        <NumberInput
          label="vCPU"
          description={offHint ?? `Knoten: ${info.host_logical_cpus} logische Prozessoren`}
          min={1}
          max={info.host_logical_cpus || 240}
          value={cpu}
          onChange={setCpu}
          disabled={!info.hardware_editable || blocked}
        />
        <NumberInput
          label={dynamic ? "Arbeitsspeicher beim Start" : "Arbeitsspeicher"}
          description={offHint}
          min={0.5}
          step={1}
          decimalScale={3}
          suffix=" GB"
          value={memGb}
          onChange={setMemGb}
          disabled={!info.hardware_editable || blocked}
        />
        {dynamic && (
          <>
            <NumberInput
              label="Minimum"
              min={0.03125}
              step={0.5}
              decimalScale={3}
              suffix=" GB"
              value={minGb}
              onChange={setMinGb}
              disabled={!info.hardware_editable || blocked}
            />
            <NumberInput
              label="Maximum"
              min={0.5}
              step={1}
              decimalScale={3}
              suffix=" GB"
              value={maxGb}
              onChange={setMaxGb}
              disabled={!info.hardware_editable || blocked}
            />
          </>
        )}
      </SimpleGrid>
      <Switch
        label="Dynamischer Arbeitsspeicher"
        checked={dynamic}
        onChange={(e) => setDynamic(e.currentTarget.checked)}
        disabled={!info.hardware_editable || blocked}
      />

      <Divider label="Netzwerk" labelPosition="left" />
      <Table verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Adapter (MAC)</Table.Th>
            <Table.Th>Virtueller Switch</Table.Th>
            <Table.Th w={150}>VLAN (leer = ohne)</Table.Th>
            <Table.Th w={50} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {adapters.map((a, index) => {
            const current = info.adapters[index];
            const special = !["Access", "Untagged"].includes(current.vlan_mode);
            const update = (patch: Partial<AdapterDraft>) => setAdapters((list) => list.map((x) => (x.id === a.id ? { ...x, ...patch } : x)));
            return (
              <Table.Tr key={a.id} style={{ opacity: a.remove ? 0.45 : 1 }}>
                <Table.Td>
                  <Text size="sm" ff="monospace" td={a.remove ? "line-through" : undefined}>
                    {current.mac_address ?? current.name}
                  </Text>
                  {special && (
                    <Text size="xs" c="dimmed">
                      VLAN-Modus {current.vlan_mode} -- hier nicht änderbar
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Select
                    size="xs"
                    data={switchOptions}
                    value={a.switchName}
                    onChange={(v) => v && update({ switchName: v })}
                    allowDeselect={false}
                    disabled={a.remove || special || blocked}
                  />
                </Table.Td>
                <Table.Td>
                  <NumberInput
                    size="xs"
                    min={1}
                    max={4094}
                    value={a.vlan}
                    onChange={(v) => update({ vlan: v })}
                    disabled={a.remove || special || blocked || a.switchName === DISCONNECTED}
                    hideControls
                  />
                </Table.Td>
                <Table.Td>
                  <Tooltip
                    label={
                      !info.adapters_addable
                        ? "Generation 1: nur bei ausgeschalteter VM"
                        : a.remove
                          ? "Entfernen zurücknehmen"
                          : "Adapter entfernen"
                    }
                  >
                    <ActionIcon
                      variant="subtle"
                      color={a.remove ? "gray" : "red"}
                      disabled={!info.adapters_addable || blocked}
                      onClick={() => update({ remove: !a.remove })}
                    >
                      {a.remove ? <IconArrowBackUp size={16} /> : <IconTrash size={16} />}
                    </ActionIcon>
                  </Tooltip>
                </Table.Td>
              </Table.Tr>
            );
          })}
          {newAdapters.map((a) => {
            const update = (patch: Partial<NewAdapterDraft>) =>
              setNewAdapters((list) => list.map((x) => (x.key === a.key ? { ...x, ...patch } : x)));
            return (
              <Table.Tr key={a.key}>
                <Table.Td>
                  <Badge size="sm" variant="light" color="blue">
                    neu
                  </Badge>
                </Table.Td>
                <Table.Td>
                  <Select
                    size="xs"
                    data={switchOptions.filter((o) => o.value !== DISCONNECTED)}
                    value={a.switchName}
                    onChange={(v) => v && update({ switchName: v })}
                    allowDeselect={false}
                  />
                </Table.Td>
                <Table.Td>
                  <NumberInput size="xs" min={1} max={4094} value={a.vlan} onChange={(v) => update({ vlan: v })} hideControls />
                </Table.Td>
                <Table.Td>
                  <ActionIcon variant="subtle" color="gray" onClick={() => setNewAdapters((list) => list.filter((x) => x.key !== a.key))}>
                    <IconX size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            );
          })}
          {adapters.length + newAdapters.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={4}>
                <Text size="sm" c="dimmed">
                  Kein Netzwerkadapter.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
      <Group>
        <Button
          size="compact-sm"
          variant="light"
          leftSection={<IconPlus size={14} />}
          disabled={!info.adapters_addable || blocked || !defaultSwitch}
          onClick={() => setNewAdapters((list) => [...list, { key: Date.now(), switchName: defaultSwitch, vlan: "" }])}
        >
          Adapter hinzufügen
        </Button>
        <Text size="xs" c="dimmed">
          Switch und VLAN lassen sich im laufenden Betrieb ändern
          {info.adapters_addable ? "" : "; Adapter hinzufügen/entfernen bei Generation 1 nur ausgeschaltet"}.
        </Text>
      </Group>

      <Divider label="Festplatten" labelPosition="left" />
      <Table verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Datei</Table.Th>
            <Table.Th>Controller</Table.Th>
            <Table.Th>Typ / belegt</Table.Th>
            <Table.Th w={170}>Größe</Table.Th>
            <Table.Th w={50} />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {info.disks.map((d) => (
            <Table.Tr key={d.path}>
              <Table.Td>
                <Tooltip label={d.path} multiline maw={600}>
                  <Text size="sm">{d.name}</Text>
                </Tooltip>
                {d.expand_blocked_reason && (
                  <Text size="xs" c="dimmed">
                    Vergrößern nicht möglich: {d.expand_blocked_reason}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{d.controller}</Table.Td>
              <Table.Td>
                <Text size="xs">
                  {d.vhd_type === "Dynamic" ? "dynamisch" : d.vhd_type === "Fixed" ? "fest" : (d.vhd_type ?? "–")}
                  {d.file_size_bytes != null ? ` · ${formatBytes(d.file_size_bytes)} belegt` : ""}
                </Text>
              </Table.Td>
              <Table.Td>
                <NumberInput
                  size="xs"
                  suffix=" GB"
                  decimalScale={3}
                  min={d.size_bytes != null ? toGb(d.size_bytes) : 1}
                  max={64 * 1024}
                  step={10}
                  value={diskGb[d.path]}
                  onChange={(v) => setDiskGb((all) => ({ ...all, [d.path]: v }))}
                  disabled={!!d.expand_blocked_reason || blocked}
                />
              </Table.Td>
              <Table.Td />
            </Table.Tr>
          ))}
          {newDisks.map((d) => {
            const update = (patch: Partial<NewDiskDraft>) => setNewDisks((list) => list.map((x) => (x.key === d.key ? { ...x, ...patch } : x)));
            return (
              <Table.Tr key={d.key}>
                <Table.Td>
                  <Badge size="sm" variant="light" color="blue">
                    neu
                  </Badge>
                </Table.Td>
                <Table.Td>SCSI</Table.Td>
                <Table.Td>
                  <Checkbox size="xs" label="dynamisch" checked={d.dynamic} onChange={(e) => update({ dynamic: e.currentTarget.checked })} />
                </Table.Td>
                <Table.Td>
                  <NumberInput size="xs" suffix=" GB" min={1} max={64 * 1024} step={10} value={d.sizeGb} onChange={(v) => update({ sizeGb: v })} />
                </Table.Td>
                <Table.Td>
                  <ActionIcon variant="subtle" color="gray" onClick={() => setNewDisks((list) => list.filter((x) => x.key !== d.key))}>
                    <IconX size={16} />
                  </ActionIcon>
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
      <Group>
        <Button
          size="compact-sm"
          variant="light"
          leftSection={<IconPlus size={14} />}
          disabled={blocked || !info.new_disk_folder}
          onClick={() => setNewDisks((list) => [...list, { key: Date.now(), sizeGb: 50, dynamic: true }])}
        >
          Festplatte hinzufügen
        </Button>
        <Text size="xs" c="dimmed" style={{ flex: 1, overflowWrap: "anywhere" }}>
          {info.new_disk_folder ? `Neue Disks liegen in ${info.new_disk_folder}. ` : ""}
          Vergrößern geht im laufenden Betrieb, Verkleinern nicht. Die Partition im Gast muss danach dort erweitert werden.
        </Text>
      </Group>

      <Divider />
      {changes.length > 0 ? (
        <div>
          <Text size="sm" fw={600} mb={4}>
            Wird geändert:
          </Text>
          <List size="sm" spacing={2}>
            {changes.map((c) => (
              <List.Item key={c}>{c}</List.Item>
            ))}
          </List>
        </div>
      ) : (
        <Text size="sm" c="dimmed">
          Noch keine Änderung.
        </Text>
      )}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Abbrechen
        </Button>
        <Button onClick={handleStart} loading={start.isPending} disabled={blocked || changes.length === 0}>
          Änderungen anwenden
        </Button>
      </Group>
    </Stack>
  );
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useVmSettingsRun(runId);
  if (!run) return <Loader size="sm" />;
  const active = run.steps.findIndex((s) => s.status === "running");
  const activeIndex = run.status === "running" ? (active === -1 ? run.steps.length : active) : run.steps.length;
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
      {run.status === "succeeded" && (
        <Alert color="green" icon={<IconCheck size={16} />}>
          Einstellungen von {run.vm_name} geändert: {run.changes.join("; ")}.
        </Alert>
      )}
      {run.status === "failed" && (
        <Alert color="red" icon={<IconX size={16} />}>
          {run.error_message}
        </Alert>
      )}
      {run.status !== "running" && (
        <Group justify="flex-end">
          <Button onClick={onClose}>Schließen</Button>
        </Group>
      )}
    </Stack>
  );
}
