import { useEffect, useState } from "react";
import {
  Alert,
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
  TagsInput,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import {
  useSmbCreateHyperVOptions,
  useSmbCreateKeep,
  useSmbCreateNetAppOptions,
  useSmbCreateRollback,
  useSmbCreateRun,
  useStartSmbCreate,
} from "@/api/hooks.smbShares";
import { useHyperVClusters, useNetAppClusters, useResourceGroups } from "@/api/hooks";
import type { SmbCreateHyperVOptions, SmbCreateNetAppOptions, SmbCreatePayload, SmbCreateRun } from "@/api/types";
import { UsageBar } from "@/components/CsvResizeModal";
import { useAuthStore } from "@/store/authStore";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

const GIB = 1024 ** 3;

// Neue SMB3-Freigabe fuer Hyper-V (Nutzer-Vorgaben 2026-09-30): Volume
// (eingehaengt, NTFS, thin, Snapshot-Policy none) + CIFS-Freigabe
// (continuously available) mit Vollzugriff fuer Knoten, CNO, Restore-Proxy-
// Host und Administratoren -- ohne Everyone. Fehler: Rueckfrage wie bei der CSV.
export function SmbShareCreateModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: run } = useSmbCreateRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      queryClient.removeQueries({ queryKey: ["smb-create-hyperv"] });
      queryClient.removeQueries({ queryKey: ["smb-create-netapp"] });
    }
  }, [opened, queryClient]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title="Neue SMB3-Freigabe anlegen"
      size={900}
    >
      {runId ? <RunView runId={runId} onClose={onClose} /> : <CreateForm opened={opened} onStarted={setRunId} onClose={onClose} />}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

function nextVolumeName(existing: string[]): string {
  const numbers = existing
    .map((s) => /vol_smb3_(\d+)$/i.exec(s.split("\\").pop() ?? ""))
    .filter(Boolean)
    .map((m) => Number(m![1]));
  return `vol_smb3_${String((numbers.length ? Math.max(...numbers) : 0) + 1).padStart(2, "0")}`;
}

function CreateForm({ opened, onStarted, onClose }: { opened: boolean; onStarted: (id: string) => void; onClose: () => void }) {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  const canAssignGroup = hasPermission("backup:create");
  const { data: hypervClusters } = useHyperVClusters();
  const { data: netappClusters } = useNetAppClusters();
  const { data: resourceGroups } = useResourceGroups();
  const start = useStartSmbCreate();
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [netappId, setNetappId] = useState<string | null>(null);
  useEffect(() => {
    if (!clusterId && hypervClusters?.length === 1) setClusterId(hypervClusters[0].id);
  }, [hypervClusters, clusterId]);
  useEffect(() => {
    if (!netappId && netappClusters?.length === 1) setNetappId(netappClusters[0].id);
  }, [netappClusters, netappId]);
  const hv = useSmbCreateHyperVOptions(clusterId, opened);
  const na = useSmbCreateNetAppOptions(netappId, opened);

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
          <Text size="sm">Computerkonten, SVMs und Aggregate werden live abgefragt…</Text>
        </Group>
      )}
      {hv.error && <Alert color="red">{apiErrorMessage(hv.error, "Hyper-V-Abfrage fehlgeschlagen.")}</Alert>}
      {na.error && <Alert color="red">{apiErrorMessage(na.error, "NetApp-Abfrage fehlgeschlagen.")}</Alert>}
      {hv.data && na.data && clusterId && netappId ? (
        <Details
          key={`${clusterId}-${netappId}`}
          clusterId={clusterId}
          netappId={netappId}
          hv={hv.data}
          na={na.data}
          groups={canAssignGroup ? (resourceGroups ?? []).filter((g) => g.scope === "smb_share") : []}
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
  hv: SmbCreateHyperVOptions;
  na: SmbCreateNetAppOptions;
  groups: { id: string; name: string; policies: { name: string }[] }[];
  canAssignGroup: boolean;
  starting: boolean;
  onClose: () => void;
  onStart: (payload: SmbCreatePayload) => void;
}) {
  // SVM vorschlagen, auf der dieser Cluster schon eine Freigabe nutzt.
  const usedServers = hv.existing_shares.map((s) => s.split("\\")[0].toLowerCase());
  const [svmName, setSvmName] = useState<string | null>(
    na.svms.find((s) => usedServers.includes(s.cifs_server.toLowerCase()))?.name ?? (na.svms.length === 1 ? na.svms[0].name : null),
  );
  const bestAggregate = [...na.aggregates]
    .filter((a) => !a.state || a.state === "online")
    .sort((a, b) => (b.available_bytes ?? 0) - (a.available_bytes ?? 0))[0];
  const [aggregateName, setAggregateName] = useState<string | null>(bestAggregate?.name ?? null);
  const [volumeName, setVolumeName] = useState(() => nextVolumeName(hv.existing_shares));
  const [shareName, setShareName] = useState<string | null>(null);
  const [sizeGb, setSizeGb] = useState<number | string>(500);
  const [autosize, setAutosize] = useState(true);
  const [accounts, setAccounts] = useState<string[]>(hv.accounts.map((a) => a.account));
  const [groupId, setGroupId] = useState<string | null>(null);

  const effShareName = shareName ?? volumeName;
  const volumeBytes = Math.round(Number(sizeGb) * GIB) || 0;
  const svm = na.svms.find((s) => s.name === svmName);
  const aggregate = na.aggregates.find((a) => a.name === aggregateName);
  const unc = svm ? `\\\\${svm.cifs_server}\\${effShareName}` : "";

  const errors: string[] = [];
  const warnings: string[] = [...hv.warnings];
  const notes: string[] = [];
  if (hv.busy_reason) errors.push(hv.busy_reason);
  if (!svmName) errors.push("SVM mit CIFS-Server wählen.");
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,202}$/.test(volumeName)) errors.push("Volume-Name: nur Buchstaben, Ziffern und '_'.");
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}$/.test(effShareName)) errors.push("Freigabename: nur Buchstaben, Ziffern, '_', '-', '.'.");
  if (svm && hv.existing_shares.some((s) => s.toLowerCase() === `${svm.cifs_server}\\${effShareName}`.toLowerCase()))
    errors.push(`Die Freigabe ${unc} gibt es bereits.`);
  if (volumeBytes < GIB) errors.push("Das Volume muss mindestens 1 GB groß sein.");
  if (accounts.length === 0) errors.push("Mindestens ein Konto für die Freigaberechte angeben.");
  const badAccounts = accounts.filter((a) => !a.includes("\\"));
  if (badAccounts.length) errors.push(`Konten bitte als DOMÄNE\\Name angeben: ${badAccounts.join(", ")}.`);
  if (accounts.some((a) => a.toLowerCase() === "everyone")) errors.push("'Everyone' wird für Hyper-V-Freigaben nicht vergeben.");
  if (aggregate?.available_bytes != null && volumeBytes > aggregate.available_bytes)
    warnings.push(
      `Das Volume (${formatBytes(volumeBytes)}) ist größer als der freie Platz im Aggregat (${formatBytes(aggregate.available_bytes)}). Thin geht das, aber das Aggregat läuft voll, wenn die Freigabe voll beschrieben wird.`,
    );
  if (!na.aggregates.length) notes.push("Aggregate sind für dieses System nicht sichtbar (SVM-Login) -- ONTAP wählt das Aggregat selbst.");
  const missing = hv.accounts.filter((a) => !accounts.includes(a.account));
  if (missing.length)
    warnings.push(`Nicht mehr in den Freigaberechten: ${missing.map((a) => `${a.account} (${a.source})`).join(", ")}.`);
  const group = groups.find((g) => g.id === groupId);

  return (
    <Stack gap="md">
      <Divider label="NetApp" labelPosition="left" />
      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Select
          label="SVM (CIFS-Server)"
          data={na.svms.map((s) => ({ value: s.name, label: `${s.name} (\\\\${s.cifs_server})` }))}
          value={svmName}
          onChange={setSvmName}
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
          disabled={!na.aggregates.length}
        />
      </SimpleGrid>
      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <TextInput label="Volume-Name" required value={volumeName} onChange={(e) => setVolumeName(e.currentTarget.value.trim())} />
        <TextInput
          label="Freigabename"
          description={unc || " "}
          required
          value={effShareName}
          onChange={(e) => setShareName(e.currentTarget.value.trim())}
        />
        <NumberInput
          label="Größe"
          suffix=" GB"
          min={1}
          decimalScale={0}
          thousandSeparator="."
          decimalSeparator=","
          value={sizeGb}
          onChange={setSizeGb}
          required
        />
      </SimpleGrid>
      <Checkbox
        label="Volume-Autosize (grow)"
        description="ONTAP vergrößert das Volume, bevor es vollläuft"
        checked={autosize}
        onChange={(e) => setAutosize(e.currentTarget.checked)}
      />
      {aggregate?.size_bytes && aggregate.available_bytes != null && volumeBytes > 0 && (
        <UsageBar
          title={`Aggregat ${aggregate.name}`}
          scale={aggregate.size_bytes}
          segments={[
            {
              value: aggregate.size_bytes - aggregate.available_bytes,
              color: "gray.6",
              label: `belegt ${formatBytes(aggregate.size_bytes - aggregate.available_bytes)}`,
            },
            {
              value: Math.min(volumeBytes, aggregate.available_bytes),
              color: "teal.3",
              label: `neues Volume ${formatBytes(volumeBytes)} (thin)`,
              striped: true,
            },
          ]}
          summary={`frei ${formatBytes(aggregate.available_bytes)}`}
          level={volumeBytes > aggregate.available_bytes ? "warn" : "ok"}
        />
      )}

      <Divider label="Freigaberechte (Vollzugriff)" labelPosition="left" />
      <TagsInput
        label="Konten"
        description="Vorschlag: Computerkonten der Knoten, des Clusters (CNO) und des Restore-Proxy-Hosts sowie die lokalen Administratoren. Everyone wird nicht vergeben."
        value={accounts}
        onChange={setAccounts}
        splitChars={[",", ";"]}
        clearable
      />
      <List size="xs" c="dimmed">
        {hv.accounts.map((a) => (
          <List.Item key={a.account}>
            {a.account} -- {a.source}
          </List.Item>
        ))}
      </List>

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
        <Text size="xs" c="dimmed" maw={560}>
          Ablauf: Volume (/{volumeName}, NTFS, thin, Snapshot-Policy none) → Freigabe (continuously available) → Zugriff von
          allen Knoten prüfen → Inventory{group ? ` → Protection Group ${group.name}` : ""}
        </Text>
        <Group>
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button
            loading={starting}
            disabled={errors.length > 0}
            onClick={() =>
              onStart({
                cluster_id: clusterId,
                netapp_cluster_id: netappId,
                svm_name: svmName!,
                aggregate_name: aggregateName,
                volume_name: volumeName,
                share_name: effShareName,
                volume_size_bytes: volumeBytes,
                autosize_grow: autosize,
                accounts,
                resource_group_id: groupId,
              })
            }
          >
            Freigabe anlegen
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

function createdObjects(run: SmbCreateRun): string[] {
  const items: string[] = [];
  if (run.share_created) items.push(`Freigabe ${run.unc_path ?? run.share_name}`);
  if (run.created_volume_uuid) items.push(`Volume ${run.volume_name} auf ${run.svm_name}`);
  return items;
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useSmbCreateRun(runId);
  const rollback = useSmbCreateRollback();
  const keep = useSmbCreateKeep();
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
            description={s.message && s.message !== "OK" ? s.message : undefined}
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
          Freigabe {run.unc_path} angelegt ({formatBytes(run.volume_size_bytes)}).
        </Alert>
      )}
      {run.status === "succeeded" && run.error_message && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          Freigabe {run.unc_path} angelegt. {run.error_message}
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
            „Zurückrollen“ entfernt genau diese Objekte. „Behalten“ lässt alles stehen, z. B. um den Fehler von Hand zu beheben.
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
