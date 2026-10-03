import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Autocomplete,
  Button,
  Checkbox,
  Divider,
  Group,
  List,
  Loader,
  Modal,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconPlus, IconTrash, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import {
  useStartVmCreate,
  useVmCreateIsos,
  useVmCreateKeep,
  useVmCreateOptions,
  useVmCreateRollback,
  useVmCreateRun,
} from "@/api/hooks.vmCreate";
import { useHyperVClusters, useResourceGroups } from "@/api/hooks";
import type { VmCreateOptions, VmCreatePayload, VmCreateRun } from "@/api/types";
import { UsageBar } from "@/components/CsvResizeModal";
import { useAuthStore } from "@/store/authStore";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

const GIB = 1024 ** 3;

// Neue VM per Assistent (Backlog #74): leere VM auf einer CSV oder SMB3-
// Freigabe, optional mit Installationsmedium; Fehler -> Rueckfrage
// Zurueckrollen/Behalten wie bei CSV anlegen.
export function VmCreateModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: run } = useVmCreateRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      queryClient.removeQueries({ queryKey: ["vm-create-options"] });
      queryClient.removeQueries({ queryKey: ["vm-create-isos"] });
    }
  }, [opened, queryClient]);

  return (
    <Modal opened={opened} onClose={onClose}
      closeOnClickOutside={!running} title="Neue VM anlegen" size={940}>
      {runId ? <RunView runId={runId} onClose={onClose} /> : <CreateForm opened={opened} onStarted={setRunId} onClose={onClose} />}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

function CreateForm({ opened, onStarted, onClose }: { opened: boolean; onStarted: (id: string) => void; onClose: () => void }) {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canAssignGroup = hasPermission("backup:create");
  const { data: clusters } = useHyperVClusters();
  const { data: resourceGroups } = useResourceGroups();
  const start = useStartVmCreate();
  const [clusterId, setClusterId] = useState<string | null>(null);
  useEffect(() => {
    if (!clusterId && clusters?.length === 1) setClusterId(clusters[0].id);
  }, [clusters, clusterId]);
  const options = useVmCreateOptions(clusterId, opened);

  return (
    <Stack gap="md">
      {(clusters?.length ?? 0) !== 1 && (
        <Select
          label="Hyper-V-Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={clusterId}
          onChange={setClusterId}
          required
        />
      )}
      {options.isLoading && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm">Knoten, freier RAM und virtuelle Switches werden live abgefragt…</Text>
        </Group>
      )}
      {options.error && <Alert color="red">{apiErrorMessage(options.error, "Hyper-V-Abfrage fehlgeschlagen.")}</Alert>}
      {options.data && clusterId ? (
        <Details
          key={clusterId}
          clusterId={clusterId}
          options={options.data}
          groups={canAssignGroup ? (resourceGroups ?? []).filter((g) => g.scope === "vm") : []}
          canAssignGroup={canAssignGroup}
          starting={start.isPending}
          onClose={onClose}
          onStart={(payload) =>
            start
              .mutateAsync(payload)
              .then((r) => onStarted(r.id))
              .catch((err) =>
                notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Anlegen konnte nicht gestartet werden."), color: "red" }),
              )
          }
        />
      ) : (
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
  options,
  groups,
  canAssignGroup,
  starting,
  onClose,
  onStart,
}: {
  clusterId: string;
  options: VmCreateOptions;
  groups: { id: string; name: string; policies: { name: string }[] }[];
  canAssignGroup: boolean;
  starting: boolean;
  onClose: () => void;
  onStart: (payload: VmCreatePayload) => void;
}) {
  const [name, setName] = useState("");
  const [locationKey, setLocationKey] = useState<string | null>(null);
  const [nodeChoice, setNodeChoice] = useState<string | null>(null);
  const [generation, setGeneration] = useState<"1" | "2">("2");
  const [cpu, setCpu] = useState<number | string>(2);
  const [ramGb, setRamGb] = useState<number | string>(4);
  const [dynamicMemory, setDynamicMemory] = useState(false);
  const [ramMinGb, setRamMinGb] = useState<number | string>(2);
  const [ramMaxGb, setRamMaxGb] = useState<number | string>(8);
  const [diskGb, setDiskGb] = useState<number | string>(100);
  const [diskDynamic, setDiskDynamic] = useState(true);
  const [dataDisks, setDataDisks] = useState<{ gb: number | string; dynamic: boolean }[]>([]);
  const [switchName, setSwitchName] = useState<string | null | undefined>(undefined);
  const [vlan, setVlan] = useState<number | string>("");
  const [secureBoot, setSecureBoot] = useState(true);
  const [template, setTemplate] = useState("MicrosoftWindows");
  const [tpm, setTpm] = useState(false);
  const [isoPath, setIsoPath] = useState("");
  const [highAvailability, setHighAvailability] = useState(true);
  const [startAfter, setStartAfter] = useState(false);
  const [groupId, setGroupId] = useState<string | null>(null);
  const isos = useVmCreateIsos(clusterId, true);

  const locationId = (l: { kind: string; key: string }) => `${l.kind}:${l.key}`;
  const location = options.locations.find((l) => locationId(l) === locationKey);
  const ramBytes = Math.round(Number(ramGb) * GIB) || 0;

  // Vorschlag: betriebsbereiter Knoten am Standort der Ablage mit dem meisten
  // freien RAM (wie beim Host-Move).
  const recommended = useMemo(() => {
    let candidates = options.nodes.filter((n) => n.state === "Up");
    const siteId = location?.site?.id;
    if (siteId && candidates.some((n) => n.site?.id === siteId)) candidates = candidates.filter((n) => n.site?.id === siteId);
    return [...candidates].sort((a, b) => (b.memory_free_bytes ?? -1) - (a.memory_free_bytes ?? -1))[0]?.name ?? null;
  }, [options.nodes, location]);
  const nodeName = nodeChoice ?? recommended;
  const node = options.nodes.find((n) => n.name === nodeName);
  const switches = node?.switches ?? [];
  // undefined = noch nichts gewaehlt -> ersten externen Switch vorschlagen
  const effSwitch =
    switchName === undefined
      ? (switches.find((s) => s.type === "External")?.name ?? switches[0]?.name ?? null)
      : switchName && switches.some((s) => s.name === switchName)
        ? switchName
        : null;

  const diskBytes = Math.round(Number(diskGb) * GIB) || 0;
  const dataBytes = dataDisks.map((d) => Math.round(Number(d.gb) * GIB) || 0);
  const fixedBytes = (diskDynamic ? 0 : diskBytes) + dataDisks.reduce((sum, d, i) => sum + (d.dynamic ? 0 : dataBytes[i]), 0);
  const totalBytes = diskBytes + dataBytes.reduce((a, b) => a + b, 0);
  const freeBytes = location?.capacity_bytes != null ? location.capacity_bytes - (location.used_bytes ?? 0) : null;
  const gen2 = generation === "2";

  const errors: string[] = [];
  const warnings: string[] = [];
  const notes: string[] = [];
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9 _.-]{0,61}[A-Za-z0-9_-])?$/.test(name))
    errors.push("VM-Name: Buchstaben, Ziffern, Leerzeichen, '_', '-', '.' (max. 63 Zeichen).");
  else if (options.vm_names.some((n) => n.toLowerCase() === name.toLowerCase())) errors.push(`Eine VM '${name}' gibt es bereits.`);
  if (!location) errors.push("Ablageort wählen.");
  if (!node) errors.push("Knoten wählen.");
  else if (node.state !== "Up") errors.push(`Knoten ${node.name} ist nicht betriebsbereit (${node.state}).`);
  if (ramBytes < GIB / 2) errors.push("Mindestens 0,5 GB RAM angeben.");
  if (dynamicMemory && !(Number(ramMinGb) <= Number(ramGb) && Number(ramGb) <= Number(ramMaxGb)))
    errors.push("Dynamischer RAM: Minimum ≤ Start-RAM ≤ Maximum.");
  if (diskBytes < GIB || dataBytes.some((b) => b < GIB)) errors.push("Jede Festplatte muss mindestens 1 GB groß sein.");
  if (freeBytes != null && fixedBytes > freeBytes)
    errors.push(`Feste Festplatten (${formatBytes(fixedBytes)}) passen nicht auf ${location!.label} (frei ${formatBytes(freeBytes)}).`);
  else if (freeBytes != null && totalBytes > freeBytes)
    warnings.push(
      `Die Festplatten können zusammen ${formatBytes(totalBytes)} groß werden, auf ${location!.label} sind ${formatBytes(freeBytes)} frei -- dynamische Disks wachsen erst bei Nutzung.`,
    );
  if (node?.memory_free_bytes != null && ramBytes > node.memory_free_bytes)
    warnings.push(
      `Auf ${node.name} sind nur ${formatBytes(node.memory_free_bytes)} RAM frei -- die VM lässt sich dort mit ${formatBytes(ramBytes)} nicht starten.`,
    );
  if (node && location?.site && node.site && node.site.id !== location.site.id)
    warnings.push(`Standort-Abweichung: ${node.name} steht in ${node.site.name}, die Ablage in ${location.site.name}.`);
  if (effSwitch) {
    const missing = options.nodes.filter((n) => n.state === "Up" && !n.switches.some((s) => s.name === effSwitch));
    if (missing.length)
      warnings.push(
        `Der Switch '${effSwitch}' fehlt auf ${missing.map((n) => n.name).join(", ")} -- dorthin lässt sich die VM nicht live migrieren.`,
      );
  } else notes.push("Ohne virtuellen Switch bleibt der Netzwerkadapter der VM unverbunden.");
  if (gen2 && tpm)
    warnings.push(
      "vTPM mit lokalem Schlüsselschutz: die VM startet nur auf Knoten, die das Zertifikat „Shielded VM Local Certificates“ dieses Knotens kennen. Für Failover/Live-Migration muss es auf die anderen Knoten exportiert werden.",
    );
  if (isoPath && !isoPath.toLowerCase().endsWith(".iso")) errors.push("Das Installationsmedium muss eine .iso-Datei sein.");
  if (!diskDynamic && diskBytes > 200 * GIB) notes.push("Eine große feste Festplatte anzulegen kann je nach Storage mehrere Minuten dauern.");
  if (location?.kind === "smb" || isoPath.startsWith("\\\\"))
    notes.push("Ablage oder ISO auf einer SMB3-Freigabe: die Schritte auf dem Knoten laufen per CredSSP.");
  (isos.data?.warnings ?? []).forEach((w) => notes.push(w));
  const group = groups.find((g) => g.id === groupId);

  function handleStart() {
    if (!location || !node) return;
    onStart({
      cluster_id: clusterId,
      vm_name: name,
      node_name: node.name,
      location_kind: location.kind,
      location_key: location.key,
      generation: gen2 ? 2 : 1,
      cpu_count: Number(cpu) || 1,
      memory_startup_bytes: ramBytes,
      dynamic_memory: dynamicMemory,
      memory_minimum_bytes: dynamicMemory ? Math.round(Number(ramMinGb) * GIB) : null,
      memory_maximum_bytes: dynamicMemory ? Math.round(Number(ramMaxGb) * GIB) : null,
      disk_size_bytes: diskBytes,
      disk_dynamic: diskDynamic,
      data_disks: dataDisks.map((d, i) => ({ size_bytes: dataBytes[i], dynamic: d.dynamic })),
      switch_name: effSwitch,
      vlan_id: effSwitch && vlan !== "" ? Number(vlan) : null,
      secure_boot: gen2 && secureBoot,
      secure_boot_template: template,
      tpm: gen2 && tpm,
      iso_path: isoPath.trim() || null,
      high_availability: highAvailability,
      start_after: startAfter,
      resource_group_id: groupId,
    });
  }

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <TextInput label="VM-Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} placeholder="z. B. SRV-APP01" />
        <Select
          label="Ablageort"
          required
          data={options.locations.map((l) => ({
            value: locationId(l),
            label: `${l.label}${l.capacity_bytes != null ? ` · frei ${formatBytes(l.capacity_bytes - (l.used_bytes ?? 0))}` : ""}${l.site ? ` · ${l.site.name}` : ""}`,
          }))}
          value={locationKey}
          onChange={setLocationKey}
          searchable
        />
        <Select
          label="Knoten"
          description={nodeName && nodeName === recommended && !nodeChoice ? "Vorschlag: Standort der Ablage, meister freier RAM" : " "}
          required
          data={options.nodes.map((n) => ({
            value: n.name,
            disabled: n.state !== "Up",
            label: `${n.name}${n.site ? ` · ${n.site.name}` : ""} · ${n.state === "Up" ? `frei ${n.memory_free_bytes != null ? formatBytes(n.memory_free_bytes) : "?"} RAM` : n.state}`,
          }))}
          value={nodeName}
          onChange={setNodeChoice}
        />
      </SimpleGrid>
      {location && name && (
        <Text size="xs" c="dimmed">
          Ordner: {location.root}\{name}\ (Konfiguration) und {location.root}\{name}\Virtual Hard Disks\
        </Text>
      )}
      {location?.capacity_bytes != null && totalBytes > 0 && (
        <UsageBar
          title={location.label}
          scale={Math.max(location.capacity_bytes, (location.used_bytes ?? 0) + totalBytes)}
          segments={[
            { value: location.used_bytes ?? 0, color: "gray.6", label: `belegt ${formatBytes(location.used_bytes ?? 0)}` },
            ...(fixedBytes > 0 ? [{ value: fixedBytes, color: "blue.6", label: `feste Disks ${formatBytes(fixedBytes)}` }] : []),
            ...(totalBytes - fixedBytes > 0
              ? [{ value: totalBytes - fixedBytes, color: "blue.3", label: `dynamische Disks bis ${formatBytes(totalBytes - fixedBytes)}`, striped: true }]
              : []),
          ]}
          summary={`frei ${formatBytes(freeBytes ?? 0)}`}
          level={freeBytes != null && fixedBytes > freeBytes ? "error" : freeBytes != null && totalBytes > freeBytes ? "warn" : "ok"}
        />
      )}

      <Divider label="Hardware" labelPosition="left" />
      <Group align="flex-end" gap="lg">
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Generation
          </Text>
          <SegmentedControl
            value={generation}
            onChange={(v) => setGeneration(v as "1" | "2")}
            data={[
              { value: "2", label: "2 (UEFI)" },
              { value: "1", label: "1 (BIOS)" },
            ]}
          />
        </Stack>
        <NumberInput label="vCPUs" min={1} max={240} value={cpu} onChange={setCpu} w={100} />
        <NumberInput label={dynamicMemory ? "Start-RAM" : "RAM"} suffix=" GB" min={0.5} step={1} decimalScale={1} value={ramGb} onChange={setRamGb} w={130} />
        <Checkbox label="Dynamischer RAM" checked={dynamicMemory} onChange={(e) => setDynamicMemory(e.currentTarget.checked)} mb={8} />
        {dynamicMemory && (
          <>
            <NumberInput label="Minimum" suffix=" GB" min={0.5} decimalScale={1} value={ramMinGb} onChange={setRamMinGb} w={120} />
            <NumberInput label="Maximum" suffix=" GB" min={0.5} decimalScale={1} value={ramMaxGb} onChange={setRamMaxGb} w={120} />
          </>
        )}
      </Group>

      <Divider label="Festplatten" labelPosition="left" />
      <Stack gap="xs">
        <Group align="flex-end">
          <NumberInput label="Systemdisk" suffix=" GB" min={1} decimalScale={0} value={diskGb} onChange={setDiskGb} w={150} />
          <SegmentedControl
            value={diskDynamic ? "dynamic" : "fixed"}
            onChange={(v) => setDiskDynamic(v === "dynamic")}
            data={[
              { value: "dynamic", label: "dynamisch" },
              { value: "fixed", label: "fest" },
            ]}
          />
          <Text size="xs" c="dimmed" mb={8}>
            {name || "<VM-Name>"}.vhdx
          </Text>
        </Group>
        {dataDisks.map((disk, index) => (
          <Group align="flex-end" key={index}>
            <NumberInput
              label={`Datendisk ${index + 1}`}
              suffix=" GB"
              min={1}
              decimalScale={0}
              value={disk.gb}
              onChange={(v) => setDataDisks((d) => d.map((x, i) => (i === index ? { ...x, gb: v } : x)))}
              w={150}
            />
            <SegmentedControl
              value={disk.dynamic ? "dynamic" : "fixed"}
              onChange={(v) => setDataDisks((d) => d.map((x, i) => (i === index ? { ...x, dynamic: v === "dynamic" } : x)))}
              data={[
                { value: "dynamic", label: "dynamisch" },
                { value: "fixed", label: "fest" },
              ]}
            />
            <ActionIcon variant="light" color="red" mb={4} onClick={() => setDataDisks((d) => d.filter((_, i) => i !== index))}>
              <IconTrash size={16} />
            </ActionIcon>
          </Group>
        ))}
        {dataDisks.length < 8 && (
          <Group>
            <Button
              size="compact-xs"
              variant="light"
              leftSection={<IconPlus size={12} />}
              onClick={() => setDataDisks((d) => [...d, { gb: 100, dynamic: true }])}
            >
              Datendisk hinzufügen
            </Button>
          </Group>
        )}
      </Stack>

      <Divider label="Netzwerk" labelPosition="left" />
      <Group align="flex-end">
        <Select
          label="Virtueller Switch"
          placeholder="nicht verbunden"
          data={switches.map((s) => ({ value: s.name, label: `${s.name} (${s.type})` }))}
          value={effSwitch}
          onChange={setSwitchName}
          clearable
          w={320}
          disabled={!node}
        />
        <NumberInput label="VLAN-ID (optional)" min={1} max={4094} value={vlan} onChange={setVlan} w={160} disabled={!effSwitch} />
      </Group>

      <Divider label="Firmware und Installationsmedium" labelPosition="left" />
      {gen2 && (
        <Group align="flex-end" gap="lg">
          <Checkbox label="Secure Boot" checked={secureBoot} onChange={(e) => setSecureBoot(e.currentTarget.checked)} mb={8} />
          <Select
            label="Secure-Boot-Vorlage"
            data={[
              { value: "MicrosoftWindows", label: "Microsoft Windows" },
              { value: "MicrosoftUEFICertificateAuthority", label: "Microsoft UEFI CA (Linux)" },
            ]}
            value={template}
            onChange={(v) => setTemplate(v ?? "MicrosoftWindows")}
            allowDeselect={false}
            disabled={!secureBoot}
            w={260}
          />
          <Checkbox
            label="vTPM"
            description="nötig für Windows 11 / Server 2025"
            checked={tpm}
            onChange={(e) => setTpm(e.currentTarget.checked)}
          />
        </Group>
      )}
      <Autocomplete
        label="Installationsmedium (ISO, optional)"
        description={
          isos.isLoading
            ? "CSVs und SMB3-Freigaben werden nach *.iso durchsucht…"
            : `${isos.data?.isos.length ?? 0} ISO-Datei(en) auf CSVs und SMB3-Freigaben gefunden -- oder Pfad von Hand eintragen`
        }
        placeholder="kein Medium -- z. B. C:\ClusterStorage\CSV01\ISO\server.iso"
        data={(isos.data?.isos ?? []).map((i) => i.path)}
        value={isoPath}
        onChange={setIsoPath}
        rightSection={isos.isLoading ? <Loader size="xs" /> : undefined}
      />

      <Divider label="Optionen" labelPosition="left" />
      <Group gap="xl">
        <Checkbox
          label="Hochverfügbar (Cluster-Rolle)"
          checked={highAvailability}
          onChange={(e) => setHighAvailability(e.currentTarget.checked)}
        />
        <Checkbox label="Nach dem Anlegen starten" checked={startAfter} onChange={(e) => setStartAfter(e.currentTarget.checked)} />
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

      {name !== "" &&
        errors.map((e) => (
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
          Ablauf: VM anlegen → CPU/RAM → Festplatte(n){effSwitch ? " → Netzwerk" : ""} → Firmware{isoPath ? " + ISO" : ""}
          {highAvailability ? " → Cluster-Rolle" : ""}
          {startAfter ? " → Starten" : ""} → Inventory{group ? ` → Protection Group ${group.name}` : ""}
        </Text>
        <Group>
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleStart} loading={starting} disabled={errors.length > 0}>
            VM anlegen
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

function createdObjects(run: VmCreateRun): string[] {
  const items: string[] = [];
  if (run.cluster_role_added) items.push(`Cluster-Rolle '${run.vm_name}'`);
  if (run.vm_created) items.push(`VM '${run.vm_name}' auf ${run.node_name} mit Ordner ${run.vm_folder}`);
  return items;
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useVmCreateRun(runId);
  const rollback = useVmCreateRollback();
  const keep = useVmCreateKeep();
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
          VM {run.vm_name} auf {run.node_name} angelegt.
        </Alert>
      )}
      {run.status === "succeeded" && run.error_message && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          VM {run.vm_name} angelegt. {run.error_message}
        </Alert>
      )}
      {run.status === "cleaned_up" && (
        <Alert color="gray" icon={<IconCheck size={16} />}>
          Vollständig zurückgerollt -- VM und Ordner wurden entfernt.
        </Alert>
      )}
      {run.status === "failed" && (
        <Alert color="red" icon={<IconX size={16} />}>
          {run.error_message}
        </Alert>
      )}
      {askRollback && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Angelegte VM zurückrollen?">
          <Text size="sm">Dieser Lauf hat bereits angelegt:</Text>
          <List size="sm" my="xs">
            {createdObjects(run).map((o) => (
              <List.Item key={o}>{o}</List.Item>
            ))}
          </List>
          <Text size="sm" mb="sm">
            „Zurückrollen“ entfernt die VM samt ihrem Ordner. „Behalten“ lässt sie stehen, z. B. um den fehlenden Schritt von Hand
            nachzuholen.
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
      {run.status !== "running" && !askRollback && (
        <Group justify="flex-end">
          <Button onClick={onClose}>Schließen</Button>
        </Group>
      )}
    </Stack>
  );
}
