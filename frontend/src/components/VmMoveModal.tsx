import { useEffect, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Progress,
  Paper,
  Radio,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCheck,
  IconDatabase,
  IconInfoCircle,
  IconMinus,
  IconRefresh,
  IconServer,
  IconX,
} from "@tabler/icons-react";

import { useQueryClient } from "@tanstack/react-query";

import { useVms } from "@/api/hooks";
import {
  resetVmMoveQueries,
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
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

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
  // null = noch keine Art gewaehlt. Die teuren Live-Abfragen (RAM aller
  // Knoten bzw. CSV-Belegung) starten erst, wenn das jeweilige Formular
  // nach der Auswahl gerendert wird (Nutzer-Vorgabe 2026-09-27).
  const [mode, setMode] = useState<MoveMode | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: run } = useVmMoveRun(runId);
  const cancelMove = useCancelVmMove();
  // Standort-Info fuer den Beheben-Hinweis aus dem bereits geladenen
  // Inventory (reine DB-Abfrage, kein WinRM) -- funktioniert auch beim
  // Oeffnen von der Alarme-Seite, die nur Name + Cluster kennt.
  const { data: vms } = useVms();
  const inventoryVm = vms?.find((v) => v.cluster_id === vm?.cluster_id && v.name === vm?.name);
  // Disks an mehreren Standorten: nur ein Storage-Move behebt das.
  const storageOnly = fixSiteMismatch && (inventoryVm?.storage_sites.length ?? 0) > 1;

  const queryClient = useQueryClient();
  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      setMode(null);
      // Ergebnisse gelten nur fuer diesen einen geoeffneten Dialog.
      resetVmMoveQueries(queryClient);
    }
  }, [opened, queryClient]);

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
      onClose={onClose}
      closeOnClickOutside={!running}
      title={`${fixSiteMismatch ? "Standort-Abweichung beheben" : "VM verschieben"}: ${vm?.name ?? ""}`}
      size={960}
    >
      {!runId && (
        <Stack gap="sm">
          {fixSiteMismatch && inventoryVm && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
              <Stack gap={4}>
                <Group gap={6}>
                  <Text size="sm">Host {inventoryVm.host || "?"}</Text>
                  {inventoryVm.host_site && <SiteBadgeView site={inventoryVm.host_site} />}
                  <Text size="sm">≠ Storage</Text>
                  {inventoryVm.storage_sites.map((s) => (
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
          {mode === null ? (
            <MoveTypeChoice
              storageOnly={storageOnly}
              recommend={fixSiteMismatch ? (storageOnly ? "storage" : "host") : null}
              onChoose={setMode}
              onClose={onClose}
            />
          ) : (
            <>
              <Group>
                <Button size="xs" variant="subtle" leftSection={<IconArrowLeft size={14} />} onClick={() => setMode(null)} px={0}>
                  Andere Art wählen
                </Button>
              </Group>
              {mode === "host" ? (
                <HostMoveForm vm={vm} opened={opened} preselect={fixSiteMismatch} onStarted={setRunId} onClose={onClose} />
              ) : (
                <StorageMoveForm vm={vm} opened={opened} preselect={fixSiteMismatch} onStarted={setRunId} onClose={onClose} />
              )}
            </>
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
                  <Text size="xs" c={s.status === "error" ? "red" : "dimmed"} style={{ wordBreak: "break-all", whiteSpace: "pre-line" }}>
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
                ? `Alle Dateien der VM liegen jetzt auf ${run.destination_label ?? run.destination_csv_name}.`
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
      {running && <BackgroundRunHint onClose={onClose} />}
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
  const {
    data: targets,
    isLoading,
    error,
    dataUpdatedAt,
    isFetching,
    refetch,
  } = useVmMoveTargets(vm?.cluster_id, vm?.name, opened);
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
          <FetchedAtLine updatedAt={dataUpdatedAt} fetching={isFetching} onRefresh={() => refetch()} />
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
                        <Group gap={6} wrap="wrap">
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
  gained: "Die VM wird danach zusätzlich durch eine CSV- bzw. SMB3-Protection-Group gesichert.",
};

function StorageMoveForm({ vm, opened, preselect, onStarted, onClose }: FormProps) {
  const [destination, setDestination] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const {
    data: targets,
    isLoading,
    error,
    dataUpdatedAt,
    isFetching,
    refetch,
  } = useVmStorageTargets(vm?.cluster_id, vm?.name, opened);
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
        destination_name: destination,
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
        Verschiebt Konfiguration und Festplatten der VM im laufenden Betrieb auf eine andere CSV oder SMB3-Freigabe. Die
        Ordnerstruktur bleibt dabei erhalten (z.B. ...\Volume1\VM01\… → \\server\share\VM01\…). Der Host ändert sich nicht.
      </Text>
      {isLoading && <Loader size="xs" />}
      {error && (
        <Alert color="red" icon={<IconX size={16} />}>
          {apiErrorMessage(error, "Mögliche Ziele konnten nicht ermittelt werden.")}
        </Alert>
      )}
      {targets && (
        <>
          <FetchedAtLine updatedAt={dataUpdatedAt} fetching={isFetching} onRefresh={() => refetch()} />
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
          <Text size="xs" c={targets.usage_note ? "orange" : "dimmed"}>
            Platzbedarf der VM: {formatBytes(targets.required_bytes)} · Reserve am Ziel: {targets.reserve_hint} ·{" "}
            {targets.usage_note ?? (targets.usage_live ? "CSV-Belegung live abgefragt" : "Belegung laut letzter Discovery")}
            {targets.csvs.some((c) => c.kind === "smb") ? " · SMB3-Belegung laut letzter Discovery" : ""}
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
          <Radio.Group label="Ziel (CSV oder SMB3-Freigabe)" value={destination} onChange={setDestination}>
            <Stack gap={6} mt={6}>
              {targets.csvs.map((csv) => {
                const matchesHost = !!csv.site && !!targets.host_site && csv.site.id === targets.host_site.id;
                const mismatchesHost = !!csv.site && !!targets.host_site && !matchesHost;
                return (
                  <Radio
                    key={csv.name}
                    value={csv.name}
                    disabled={csv.is_current || !csv.fits || !!targets.blocked_reason}
                    styles={{ labelWrapper: { flex: 1 } }}
                    label={
                      <Stack gap={2}>
                        <Group gap={6} wrap="wrap">
                          <Text size="sm">{csv.name}</Text>
                          {csv.kind === "smb" && (
                            <Badge size="sm" variant="light" color="grape">
                              SMB3
                            </Badge>
                          )}
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
                        </Group>
                        <CsvUsageBar csv={csv} />
                      </Stack>
                    }
                  />
                );
              })}
            </Stack>
          </Radio.Group>
          {selected && (selected.kind === "smb" || targets.current_csvs.some((c) => c.startsWith("\\\\"))) && (
            <Alert color="blue" variant="light" icon={<IconInfoCircle size={16} />}>
              <Text size="xs">
                SMB3 im Spiel: Hyper-V greift beim Verschieben im Namen des App-Kontos auf die Freigabe zu, die App nutzt dafür
                eine CredSSP-Verbindung zum Host. Die Computerkonten aller Hyper-V-Knoten und das Cluster-Konto brauchen
                Vollzugriff auf die Freigabe (Freigabe- und NTFS-Rechte), sonst scheitert der Move mit „Zugriff verweigert“.
              </Text>
            </Alert>
          )}
          {selected && needsAck && (
            <Alert color={selected.protection_change === "lost" ? "red" : "yellow"} icon={<IconAlertTriangle size={16} />}>
              <Stack gap={6}>
                <Text size="sm">{PROTECTION_CHANGE_TEXT[selected.protection_change]}</Text>
                <Text size="xs">
                  Vorher: {targets.protection_groups_now.join(", ") || "keine"} · Nachher:{" "}
                  {selected.protection_groups_after.join(", ") || "keine"}
                </Text>
                <Text size="xs">Bestehende Wiederherstellungspunkte bleiben am bisherigen Speicherort und sind weiter nutzbar.</Text>
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

// Belegung einer CSV, analog zu NodeMemoryBar: grau = heute belegt, farbiger
// Aufsatz = zusaetzlich durch die VM belegt, wenn diese CSV das Ziel ist.
// Bei der aktuellen CSV der VM zeigt der gruene Anteil stattdessen den
// Platz, der durch das Wegverschieben frei wird.
function CsvUsageBar({ csv }: { csv: VmStorageTargetCsv }) {
  if (csv.capacity_bytes == null || csv.free_bytes == null || csv.capacity_bytes <= 0) {
    return (
      <Text size="xs" c="dimmed">
        Belegung unbekannt
      </Text>
    );
  }
  const total = csv.capacity_bytes;
  const usedPct = ((total - csv.free_bytes) / total) * 100;
  if (csv.is_current) {
    const freedPct = Math.min(usedPct, (csv.freed_bytes / total) * 100);
    return (
      <Stack gap={2} maw={360}>
        <Progress.Root size={6}>
          <Progress.Section value={usedPct - freedPct} color="gray" />
          {freedPct > 0 && <Progress.Section value={freedPct} color="green" />}
        </Progress.Root>
        <Text size="xs" c="dimmed">
          {formatBytes(csv.free_bytes)} von {formatBytes(total)} frei → {formatBytes(csv.free_bytes + csv.freed_bytes)} nach dem
          Move (wird frei)
        </Text>
      </Stack>
    );
  }
  const afterFree = csv.free_after_bytes;
  const addPct = afterFree != null ? Math.max(0, Math.min(100 - usedPct, (csv.needed_bytes / total) * 100)) : 0;
  const color = !csv.fits ? "red" : usedPct + addPct >= 85 ? "yellow" : "blue";
  return (
    <Stack gap={2} maw={360}>
      <Progress.Root size={6}>
        <Progress.Section value={usedPct} color="gray" />
        {addPct > 0 && <Progress.Section value={addPct} color={color} />}
      </Progress.Root>
      <Text size="xs" c="dimmed">
        {formatBytes(csv.free_bytes)} von {formatBytes(total)} frei
        {afterFree != null ? ` → ${formatBytes(Math.max(0, afterFree))} nach dem Move` : ""}
        {csv.freed_bytes > 0 ? ` (bereits ${formatBytes(csv.freed_bytes)} der VM hier)` : ""}
      </Text>
    </Stack>
  );
}

// Erster Schritt des Dialogs: Art der Verschiebung waehlen. Loest selbst
// keine Abfrage aus.
function MoveTypeChoice({
  storageOnly,
  recommend,
  onChoose,
  onClose,
}: {
  storageOnly: boolean;
  recommend: MoveMode | null;
  onChoose: (mode: MoveMode) => void;
  onClose: () => void;
}) {
  const options: { mode: MoveMode; icon: React.ReactNode; title: string; text: string; query: string }[] = [
    {
      mode: "host",
      icon: <IconServer size={22} />,
      title: "Host (Live-Migration)",
      text: "Auf einen anderen Knoten des Clusters. Dauert Sekunden, keine Datenkopie, der Speicherort bleibt gleich.",
      query: "Fragt als Nächstes den freien Arbeitsspeicher aller Knoten ab.",
    },
    {
      mode: "storage",
      icon: <IconDatabase size={22} />,
      title: "Storage (CSV oder SMB3)",
      text: "Dateien der VM auf eine andere CSV oder SMB3-Freigabe, Ordnerstruktur bleibt erhalten. Dauert je nach VM-Größe, der Host bleibt gleich.",
      query: "Fragt als Nächstes die Belegung aller CSVs und SMB3-Freigaben ab.",
    },
  ];
  return (
    <Stack gap="sm">
      <Text size="sm">Was soll verschoben werden?</Text>
      {options.map((o) => {
        const disabled = o.mode === "host" && storageOnly;
        return (
          <UnstyledButton key={o.mode} onClick={() => !disabled && onChoose(o.mode)} disabled={disabled}>
            <Paper withBorder p="sm" style={{ opacity: disabled ? 0.5 : 1, cursor: disabled ? "not-allowed" : "pointer" }}>
              <Group align="flex-start" wrap="nowrap" gap="sm">
                {o.icon}
                <Stack gap={2} style={{ flex: 1 }}>
                  <Group gap={6}>
                    <Text size="sm" fw={600}>
                      {o.title}
                    </Text>
                    {recommend === o.mode && (
                      <Badge size="sm" variant="filled" color="green">
                        Empfohlen
                      </Badge>
                    )}
                  </Group>
                  <Text size="xs" c="dimmed">
                    {disabled ? "Behebt die Abweichung nicht, da die Festplatten an mehreren Standorten liegen." : o.text}
                  </Text>
                  {!disabled && (
                    <Text size="xs" c="dimmed" fs="italic">
                      {o.query}
                    </Text>
                  )}
                </Stack>
              </Group>
            </Paper>
          </UnstyledButton>
        );
      })}
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Abbrechen
        </Button>
      </Group>
    </Stack>
  );
}

// "Stand: hh:mm:ss" der Live-Abfrage + bewusstes Neu-Abfragen -- die Werte
// werden innerhalb des geoeffneten Dialogs wiederverwendet (siehe
// hooks.vmMoves.ts), koennen also einige Minuten alt sein.
function FetchedAtLine({ updatedAt, fetching, onRefresh }: { updatedAt: number; fetching: boolean; onRefresh: () => void }) {
  return (
    <Group gap={6}>
      <Text size="xs" c="dimmed">
        {fetching ? "Wird aktualisiert…" : `Stand: ${new Date(updatedAt).toLocaleTimeString("de-DE")}`}
      </Text>
      <Tooltip label="Neu abfragen">
        <ActionIcon size="sm" variant="subtle" onClick={onRefresh} loading={fetching} aria-label="Neu abfragen">
          <IconRefresh size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}
