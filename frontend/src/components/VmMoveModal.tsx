import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Progress,
  Radio,
  SegmentedControl,
  Stack,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import {
  useCancelVmMove,
  useStartStorageMove,
  useStartVmMove,
  useVmMoveRun,
  useVmMoveTargets,
  useVmStorageTargets,
} from "@/api/hooks.vmMoves";
import type { VmMoveTargetNode, VmStorageTargetCsv } from "@/api/types";
import { SiteBadgeView } from "@/components/SiteBadge";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

type MoveMode = "host" | "storage";

// Minimal noetig: Name + Cluster -- so kann der Dialog auch von der
// Alarme-Seite aus geoeffnet werden, die kein volles Vm-Objekt hat.
interface MoveVmRef {
  name: string;
  cluster_id?: string | null;
}

interface VmMoveModalProps {
  opened: boolean;
  onClose: () => void;
  vm: MoveVmRef | null;
  // Stufe 3: "Standort-Abweichung beheben" -- erklaert die Abweichung,
  // waehlt den passenden Weg vor und selektiert das empfohlene Ziel.
  fixSiteMismatch?: boolean;
}

// VM verschieben: Stufe 1 = Host-Move (Live-Migration auf einen anderen
// Knoten des Clusters), Stufe 2 = Storage-Move (Move-VMStorage auf eine
// andere CSV, Ordnerstruktur der Quelle bleibt erhalten). Standort-Badges
// (Settings > Standorte) helfen bei der Wahl eines passenden Ziels.
export function VmMoveModal({ opened, onClose, vm, fixSiteMismatch = false }: VmMoveModalProps) {
  const [mode, setMode] = useState<MoveMode>("host");
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: run } = useVmMoveRun(runId);
  const cancelMove = useCancelVmMove();
  // Im Beheben-Modus braucht der Dialog die Standorte schon hier, um den
  // passenden Weg vorzuwaehlen -- gleiche Query wie in HostMoveForm, wird
  // von React Query geteilt (kein zweiter Aufruf).
  const { data: hostTargets } = useVmMoveTargets(vm?.cluster_id, vm?.name, opened && fixSiteMismatch && !runId);
  // Disks an mehreren Standorten: nur ein Storage-Move behebt das.
  const storageOnly = fixSiteMismatch && (hostTargets?.storage_sites.length ?? 0) > 1;

  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      setMode("host");
    }
  }, [opened]);

  useEffect(() => {
    if (storageOnly) setMode("storage");
  }, [storageOnly]);

  const running = run?.status === "running";

  function handleCancel() {
    if (!runId) return;
    cancelMove.mutate(runId, {
      onError: (err) =>
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Abbruch fehlgeschlagen."),
          color: "red",
        }),
    });
  }

  return (
    <Modal
      opened={opened}
      onClose={running ? () => undefined : onClose}
      withCloseButton={!running}
      title={`${fixSiteMismatch ? "Standort-Abweichung beheben" : "VM verschieben"}: ${vm?.name ?? ""}`}
      size="lg"
    >
      {!runId && (
        <Stack gap="sm">
          {fixSiteMismatch && hostTargets && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
              <Stack gap={4}>
                <Group gap={6}>
                  <Text size="sm">Host {hostTargets.current_node ?? "?"}</Text>
                  {hostTargets.host_site && <SiteBadgeView site={hostTargets.host_site} />}
                  <Text size="sm">≠ Storage</Text>
                  {hostTargets.storage_sites.map((s) => (
                    <SiteBadgeView key={s.id} site={s} />
                  ))}
                </Group>
                <Text size="xs">
                  {storageOnly
                    ? "Die Festplatten liegen an mehreren Standorten -- das behebt nur ein Storage-Move auf eine CSV am Standort des Hosts."
                    : "Schnellster Weg: Host per Live-Migration an den Standort des Storage verschieben (Sekunden, keine Datenkopie). Alternativ den Storage an den Standort des Hosts verschieben (dauert je nach VM-Größe)."}
                </Text>
              </Stack>
            </Alert>
          )}
          <SegmentedControl
            value={mode}
            onChange={(v) => setMode(v as MoveMode)}
            data={[
              {
                value: "host",
                label: "Host (Live-Migration)",
                disabled: storageOnly,
              },
              { value: "storage", label: "Storage (andere CSV)" },
            ]}
          />
          {mode === "host" ? (
            <HostMoveForm vm={vm} opened={opened} preselect={fixSiteMismatch} onStarted={setRunId} onClose={onClose} />
          ) : (
            <StorageMoveForm vm={vm} opened={opened} preselect={fixSiteMismatch} onStarted={setRunId} onClose={onClose} />
          )}
        </Stack>
      )}

      {runId && (
        <Stack gap="sm">
          {!run && <Loader size="sm" />}
          {run?.steps.map((s) => (
            <Group key={s.step} gap="xs" wrap="nowrap" align="flex-start">
              {STEP_STATUS_ICON[s.status]}
              <Stack gap={0} style={{ flex: 1 }}>
                <Text size="sm" fw={600}>
                  {s.label}
                </Text>
                {s.message && s.message !== "OK" && (
                  <Text size="xs" c={s.status === "error" ? "red" : "dimmed"} style={{ wordBreak: "break-all" }}>
                    {s.message}
                  </Text>
                )}
              </Stack>
            </Group>
          ))}
          {run?.move_type === "storage" && running && (
            <Stack gap={4}>
              <Progress value={run.progress_percent ?? 0} animated striped />
              <Group justify="space-between">
                <Text size="xs" c="dimmed">
                  {run.progress_percent != null ? `${run.progress_percent} %` : "Fortschritt wird ermittelt…"} -- die VM läuft
                  währenddessen normal weiter.
                </Text>
                <Button
                  size="xs"
                  variant="light"
                  color="red"
                  onClick={handleCancel}
                  loading={cancelMove.isPending}
                  disabled={!!run.cancel_requested_at}
                >
                  {run.cancel_requested_at ? "Abbruch angefordert" : "Abbrechen"}
                </Button>
              </Group>
            </Stack>
          )}
          {run?.status === "succeeded" && (
            <Alert icon={<IconCheck size={16} />} color="green" variant="light">
              {run.move_type === "storage"
                ? `Alle Dateien der VM liegen jetzt auf ${run.destination_csv_name}.`
                : `VM läuft jetzt auf ${run.target_node}.`}
            </Alert>
          )}
          {run?.status === "failed" && (
            <Alert icon={<IconX size={16} />} color="red" variant="light">
              {run.error_message}
            </Alert>
          )}
          {run && !running && (
            <Group justify="flex-end">
              <Button onClick={onClose}>Schließen</Button>
            </Group>
          )}
        </Stack>
      )}
    </Modal>
  );
}

interface FormProps {
  vm: MoveVmRef | null;
  opened: boolean;
  // Empfohlenes Ziel automatisch auswaehlen (Beheben-Modus).
  preselect: boolean;
  onStarted: (runId: string) => void;
  onClose: () => void;
}

function HostMoveForm({ vm, opened, preselect, onStarted, onClose }: FormProps) {
  const [targetNode, setTargetNode] = useState<string | null>(null);
  const { data: targets, isLoading, error } = useVmMoveTargets(vm?.cluster_id, vm?.name, opened);
  const startMove = useStartVmMove();
  const storageSiteIds = new Set((targets?.storage_sites ?? []).map((s) => s.id));

  useEffect(() => {
    if (preselect && targets?.recommended_node && !targets.blocked_reason) setTargetNode(targets.recommended_node);
  }, [preselect, targets]);

  function handleStart() {
    if (!vm?.cluster_id || !targetNode) return;
    startMove
      .mutateAsync({
        cluster_id: vm.cluster_id,
        vm_name: vm.name,
        target_node: targetNode,
      })
      .then((started) => onStarted(started.id))
      .catch((err) =>
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Verschieben konnte nicht gestartet werden."),
          color: "red",
        }),
      );
  }

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Live-Migration auf einen anderen Knoten desselben Clusters. Eine laufende VM bleibt dabei erreichbar, der Speicherort
        (CSV) ändert sich nicht.
      </Text>
      {isLoading && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm">Cluster-Knoten und freier Arbeitsspeicher werden abgefragt…</Text>
        </Group>
      )}
      {error && (
        <Alert color="red" icon={<IconX size={16} />}>
          {apiErrorMessage(error, "Cluster-Knoten konnten nicht abgefragt werden.")}
        </Alert>
      )}
      {targets && (
        <>
          <Group gap="xs">
            <Text size="sm">
              Aktuell auf <b>{targets.current_node ?? "?"}</b>
            </Text>
            {targets.host_site && <SiteBadgeView site={targets.host_site} />}
            {targets.storage_sites.length > 0 && (
              <>
                <Text size="sm" c="dimmed">
                  · Storage in
                </Text>
                {targets.storage_sites.map((s) => (
                  <SiteBadgeView key={s.id} site={s} />
                ))}
              </>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            RAM-Bedarf der VM:{" "}
            {targets.vm_memory_bytes != null
              ? `${formatBytes(targets.vm_memory_bytes)} (${targets.vm_state === "Running" ? "aktuell zugewiesen" : "Start-RAM"})`
              : "unbekannt"}{" "}
            · Reserve auf dem Zielknoten: {targets.memory_reserve_bytes_hint}
          </Text>
          {targets.recommended_node ? (
            <Text size="xs">
              Empfohlen: <b>{targets.recommended_node}</b>
              {targets.recommended_reason ? ` -- ${targets.recommended_reason}` : ""}
            </Text>
          ) : (
            targets.recommended_reason && (
              <Text size="xs" c="orange">
                Keine Empfehlung: {targets.recommended_reason}
              </Text>
            )
          )}
          {targets.blocked_reason && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Verschieben derzeit nicht möglich">
              {targets.blocked_reason}
            </Alert>
          )}
          <Radio.Group label="Zielknoten" value={targetNode} onChange={setTargetNode}>
            <Stack gap={6} mt={6}>
              {targets.nodes.map((node) => {
                const up = node.state === "Up";
                const matchesStorage = !!node.site && storageSiteIds.has(node.site.id);
                const mismatchesStorage = !!node.site && storageSiteIds.size > 0 && !matchesStorage;
                return (
                  <Radio
                    key={node.name}
                    value={node.name}
                    disabled={!up || node.is_current || node.fits_memory === false || !!targets.blocked_reason}
                    styles={{ labelWrapper: { flex: 1 } }}
                    label={
                      <Stack gap={2}>
                        <Group gap={6} wrap="nowrap">
                          <Text size="sm">{node.name}</Text>
                          {node.recommended && (
                            <Badge size="sm" variant="filled" color="green">
                              Empfohlen
                            </Badge>
                          )}
                          {node.site && <SiteBadgeView site={node.site} />}
                          {node.is_current && (
                            <Badge size="sm" variant="outline" color="gray">
                              aktuell
                            </Badge>
                          )}
                          {!up && (
                            <Badge size="sm" variant="light" color="red">
                              {node.state}
                            </Badge>
                          )}
                          {up && !node.is_current && matchesStorage && (
                            <Badge size="sm" variant="light" color="green">
                              gleicher Standort wie Storage
                            </Badge>
                          )}
                          {up && !node.is_current && mismatchesStorage && (
                            <Badge size="sm" variant="light" color="orange">
                              anderer Standort als Storage
                            </Badge>
                          )}
                          {up && !node.is_current && node.fits_memory === false && (
                            <Badge size="sm" variant="light" color="red">
                              zu wenig RAM
                            </Badge>
                          )}
                          <Text size="xs" c="dimmed">
                            {node.vm_count} VMs
                          </Text>
                        </Group>
                        {up && <NodeMemoryBar node={node} />}
                      </Stack>
                    }
                  />
                );
              })}
            </Stack>
          </Radio.Group>
        </>
      )}
      <Group justify="flex-end" mt="sm">
        <Button variant="default" onClick={onClose}>
          Abbrechen
        </Button>
        <Button
          onClick={handleStart}
          loading={startMove.isPending}
          disabled={!targetNode || !targets || !!targets.blocked_reason}
        >
          Verschieben
        </Button>
      </Group>
    </Stack>
  );
}

const PROTECTION_CHANGE_TEXT: Record<VmStorageTargetCsv["protection_change"], string> = {
  same: "",
  lost: "Die VM ist danach durch KEINE Protection Group mehr geschützt.",
  changed: "Die VM wird danach durch andere Protection Groups gesichert.",
  gained: "Die VM wird danach zusätzlich durch eine CSV-Protection-Group gesichert.",
};

function StorageMoveForm({ vm, opened, preselect, onStarted, onClose }: FormProps) {
  const [destination, setDestination] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const { data: targets, isLoading, error } = useVmStorageTargets(vm?.cluster_id, vm?.name, opened);
  const startMove = useStartStorageMove();
  const selected = targets?.csvs.find((c) => c.name === destination);
  const needsAck = !!selected && selected.protection_change !== "same";

  useEffect(() => setAcknowledged(false), [destination]);
  useEffect(() => {
    if (preselect && targets?.recommended_csv && !targets.blocked_reason) setDestination(targets.recommended_csv);
  }, [preselect, targets]);

  function handleStart() {
    if (!vm?.cluster_id || !destination) return;
    startMove
      .mutateAsync({
        cluster_id: vm.cluster_id,
        vm_name: vm.name,
        destination_csv_name: destination,
        acknowledge_protection_change: acknowledged,
      })
      .then((started) => onStarted(started.id))
      .catch((err) =>
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Verschieben konnte nicht gestartet werden."),
          color: "red",
        }),
      );
  }

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        Verschiebt Konfiguration und Festplatten der VM im laufenden Betrieb auf eine andere CSV. Die Ordnerstruktur bleibt dabei
        erhalten (z.B. ...\Volume1\VM01\… → ...\Volume2\VM01\…). Der Host ändert sich nicht.
      </Text>
      {isLoading && <Loader size="xs" />}
      {error && (
        <Alert color="red" icon={<IconX size={16} />}>
          {apiErrorMessage(error, "Ziel-CSVs konnten nicht ermittelt werden.")}
        </Alert>
      )}
      {targets && (
        <>
          <Group gap="xs">
            <Text size="sm">
              Aktuell auf <b>{targets.current_csvs.join(", ") || "?"}</b> · {formatBytes(targets.required_bytes)} belegt
            </Text>
            {targets.host_site && (
              <>
                <Text size="sm" c="dimmed">
                  · Host in
                </Text>
                <SiteBadgeView site={targets.host_site} />
              </>
            )}
          </Group>
          <Text size="xs" c="dimmed">
            Gesichert durch:{" "}
            {targets.protection_groups_now.length > 0 ? targets.protection_groups_now.join(", ") : "keine Protection Group"}
          </Text>
          {targets.recommended_csv ? (
            <Text size="xs">
              Empfohlen: <b>{targets.recommended_csv}</b>
              {targets.recommended_reason ? ` -- ${targets.recommended_reason}` : ""}
            </Text>
          ) : (
            targets.recommended_reason && (
              <Text size="xs" c="orange">
                Keine Empfehlung: {targets.recommended_reason}
              </Text>
            )
          )}
          {targets.blocked_reason && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Verschieben derzeit nicht möglich">
              {targets.blocked_reason}
            </Alert>
          )}
          <Radio.Group label="Ziel-CSV" value={destination} onChange={setDestination}>
            <Stack gap={6} mt={6}>
              {targets.csvs.map((csv) => {
                const matchesHost = !!csv.site && !!targets.host_site && csv.site.id === targets.host_site.id;
                const mismatchesHost = !!csv.site && !!targets.host_site && !matchesHost;
                return (
                  <Radio
                    key={csv.name}
                    value={csv.name}
                    disabled={csv.is_current || !csv.fits || !!targets.blocked_reason}
                    label={
                      <Group gap={6} wrap="nowrap">
                        <Text size="sm">{csv.name}</Text>
                        {csv.name === targets.recommended_csv && (
                          <Badge size="sm" variant="filled" color="green">
                            Empfohlen
                          </Badge>
                        )}
                        {csv.site && <SiteBadgeView site={csv.site} />}
                        {csv.is_current && (
                          <Badge size="sm" variant="outline" color="gray">
                            aktuell
                          </Badge>
                        )}
                        {!csv.is_current && !csv.fits && (
                          <Badge size="sm" variant="light" color="red">
                            zu wenig Platz
                          </Badge>
                        )}
                        {!csv.is_current && matchesHost && (
                          <Badge size="sm" variant="light" color="green">
                            gleicher Standort wie Host
                          </Badge>
                        )}
                        {!csv.is_current && mismatchesHost && (
                          <Badge size="sm" variant="light" color="orange">
                            anderer Standort als Host
                          </Badge>
                        )}
                        {!csv.is_current && csv.protection_change === "lost" && (
                          <Badge size="sm" variant="light" color="red">
                            danach ungeschützt
                          </Badge>
                        )}
                        {!csv.is_current && csv.protection_change === "changed" && (
                          <Badge size="sm" variant="light" color="yellow">
                            anderes Backup-Profil
                          </Badge>
                        )}
                        <Text size="xs" c="dimmed">
                          {csv.free_bytes != null ? `${formatBytes(csv.free_bytes)} frei` : ""}
                        </Text>
                      </Group>
                    }
                  />
                );
              })}
            </Stack>
          </Radio.Group>
          {selected && needsAck && (
            <Alert color={selected.protection_change === "lost" ? "red" : "yellow"} icon={<IconAlertTriangle size={16} />}>
              <Stack gap={6}>
                <Text size="sm">{PROTECTION_CHANGE_TEXT[selected.protection_change]}</Text>
                <Text size="xs">
                  Vorher: {targets.protection_groups_now.join(", ") || "keine"} · Nachher:{" "}
                  {selected.protection_groups_after.join(", ") || "keine"}
                </Text>
                <Text size="xs">Bestehende Wiederherstellungspunkte bleiben auf der bisherigen CSV und sind weiter nutzbar.</Text>
                <Checkbox
                  label="Verstanden, trotzdem verschieben"
                  checked={acknowledged}
                  onChange={(e) => setAcknowledged(e.currentTarget.checked)}
                />
              </Stack>
            </Alert>
          )}
        </>
      )}
      <Group justify="flex-end" mt="sm">
        <Button variant="default" onClick={onClose}>
          Abbrechen
        </Button>
        <Button
          onClick={handleStart}
          loading={startMove.isPending}
          disabled={!destination || !targets || !!targets.blocked_reason || (needsAck && !acknowledged)}
        >
          Verschieben
        </Button>
      </Group>
    </Stack>
  );
}

// RAM-Auslastung eines Knotens: heller Teil = aktuell belegt, dunklerer
// Aufsatz = zusaetzlich durch die VM belegt (nur bei Zielkandidaten).
function NodeMemoryBar({ node }: { node: VmMoveTargetNode }) {
  if (node.memory_total_bytes == null || node.memory_free_bytes == null) {
    return (
      <Text size="xs" c="dimmed">
        RAM nicht abfragbar{node.memory_error ? ` (${node.memory_error})` : ""}
      </Text>
    );
  }
  const total = node.memory_total_bytes;
  const usedPct = ((total - node.memory_free_bytes) / total) * 100;
  const afterFree = node.memory_free_after_bytes;
  const addPct = afterFree != null ? Math.max(0, ((node.memory_free_bytes - afterFree) / total) * 100) : 0;
  const color = node.fits_memory === false ? "red" : usedPct + addPct >= 85 ? "yellow" : "blue";
  return (
    <Stack gap={2} maw={360}>
      <Progress.Root size={6}>
        <Progress.Section value={usedPct} color="gray" />
        {addPct > 0 && <Progress.Section value={Math.min(addPct, 100 - usedPct)} color={color} />}
      </Progress.Root>
      <Text size="xs" c="dimmed">
        {formatBytes(node.memory_free_bytes)} von {formatBytes(total)} frei
        {afterFree != null ? ` → ${formatBytes(Math.max(0, afterFree))} nach dem Move` : ""}
      </Text>
    </Stack>
  );
}
