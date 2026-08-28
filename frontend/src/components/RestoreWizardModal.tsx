import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Radio,
  ScrollArea,
  Stack,
  Stepper,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import {
  useBackupsForObject,
  useCleanupFileRestoreRun,
  useCopyFileRestoreSelection,
  useFileRestoreRun,
  useRestoreRun,
  useTriggerFileRestore,
  useTriggerRestore,
  useVms,
} from "@/api/hooks";
import { FileBrowser } from "@/components/FileBrowser";
import type { RestoreMode, RestoreRun, VmWithBackups } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

type RestoreKind = RestoreMode | "files";

interface RestoreWizardModalProps {
  opened: boolean;
  onClose: () => void;
  vm: VmWithBackups | null;
}

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

interface FinishedRun {
  vhdPath: string;
  run: RestoreRun;
}

export function RestoreWizardModal({ opened, onClose, vm }: RestoreWizardModalProps) {
  const [active, setActive] = useState(0);
  const [snapshotId, setSnapshotId] = useState<string | null>(null);
  const [selectedVhdPaths, setSelectedVhdPaths] = useState<string[]>([]);
  const [restoreKind, setRestoreKind] = useState<RestoreKind>("add");
  const mode: RestoreMode = restoreKind === "replace" ? "replace" : "add";

  const [queue, setQueue] = useState<string[]>([]);
  const [currentVhdPath, setCurrentVhdPath] = useState<string | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [finishedRuns, setFinishedRuns] = useState<FinishedRun[]>([]);

  // Datei-basierter Restore: eigener Ablauf ab Schritt "Start" (mounten
  // statt kopieren+anhaengen, siehe FileBrowser/file-restore-Endpunkte).
  const [fileRunId, setFileRunId] = useState<string | null>(null);
  const [selectedFsPaths, setSelectedFsPaths] = useState<Set<string>>(new Set());
  const [destinationPath, setDestinationPath] = useState("");
  const [lastCopyResult, setLastCopyResult] = useState<"success" | "error" | null>(null);

  const { data: backups, isLoading: backupsLoading } = useBackupsForObject("vm", vm?.name, opened && active === 0);
  const { data: vms } = useVms();
  const triggerRestore = useTriggerRestore();
  const { data: run } = useRestoreRun(currentRunId ?? undefined, true);

  const triggerFileRestore = useTriggerFileRestore();
  const { data: fileRun } = useFileRestoreRun(fileRunId ?? undefined, true);
  const copySelection = useCopyFileRestoreSelection(fileRunId ?? undefined);
  const cleanupFileRestoreRun = useCleanupFileRestoreRun();

  const vmFull = vms?.find((v) => v.name === vm?.name);
  const totalCount = finishedRuns.length + (currentVhdPath ? 1 : 0) + queue.length;

  const selectedSnapshot = backups?.find((b) => b.id === snapshotId);
  // Die VHD-Auswahl kommt aus dem gewaehlten Snapshot (BackupRunVmConfig,
  // zum Backup-Zeitpunkt gespeichert) statt aus dem aktuell-live VM-Zustand
  // -- sonst koennte man eine VHDX angeboten bekommen, die in diesem
  // Snapshot gar nicht enthalten war (neue Disk seitdem, oder die VM ist
  // zwischenzeitlich auf eine andere CSV/LUN umgezogen). Fallback auf die
  // Live-Liste nur fuer Backups von vor diesem Feature (kein
  // BackupRunVmConfig vorhanden, vhds ist dann leer).
  const vhdOptions = selectedSnapshot?.vhds.length
    ? selectedSnapshot.vhds
    : (vmFull?.vhds ?? []).map((v) => ({ name: v.name, path: v.full_path, size_bytes: v.size_bytes }));

  useEffect(() => {
    if (!opened) {
      setActive(0);
      setSnapshotId(null);
      setSelectedVhdPaths([]);
      setRestoreKind("add");
      setQueue([]);
      setCurrentVhdPath(null);
      setCurrentRunId(null);
      setFinishedRuns([]);
      setFileRunId(null);
      setSelectedFsPaths(new Set());
      setDestinationPath("");
      setLastCopyResult(null);
    }
  }, [opened]);

  useEffect(() => {
    setSelectedVhdPaths([]);
  }, [snapshotId]);

  // Vorschlag fuer den Kopier-Zielpfad uebernehmen, sobald die Session
  // erfolgreich gemountet ist (einmalig -- der Nutzer kann ihn danach frei
  // editieren, ohne dass ein Re-Render ihn wieder ueberschreibt).
  useEffect(() => {
    if (fileRun?.status === "succeeded" && fileRun.default_destination_path && !destinationPath) {
      setDestinationPath(fileRun.default_destination_path);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileRun?.status]);

  // Verarbeitet die Warteschlange sequenziell: startet den naechsten Restore
  // erst, wenn kein anderer mehr aktiv ist (currentVhdPath === null). Der
  // Restore-Proxy-Host/die Igroup werden pro Lauf exklusiv genutzt, parallele
  // Laeufe waeren riskant -- daher bewusst nacheinander statt gleichzeitig.
  useEffect(() => {
    if (currentVhdPath !== null || queue.length === 0 || !vm || !snapshotId) return;
    const [next, ...rest] = queue;
    setQueue(rest);
    setCurrentVhdPath(next);
    triggerRestore.mutate(
      { vm_name: vm.name, snapshot_id: snapshotId, source_vhd_path: next, mode },
      {
        onSuccess: (result) => setCurrentRunId(result.id),
        onError: (err) => {
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Restore konnte nicht gestartet werden."), color: "red" });
          setCurrentVhdPath(null);
        },
      },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentVhdPath, queue, vm, snapshotId, mode]);

  useEffect(() => {
    if (!run || (run.status !== "succeeded" && run.status !== "failed")) return;
    setFinishedRuns((prev) => {
      if (prev.some((f) => f.run.id === run.id)) return prev;
      return [...prev, { vhdPath: currentVhdPath ?? run.source_vhd_path, run }];
    });
    setCurrentRunId(null);
    setCurrentVhdPath(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status, run?.id]);

  function handleTrigger() {
    if (!vm || !snapshotId || selectedVhdPaths.length === 0) return;
    if (restoreKind === "files") {
      setActive(3);
      triggerFileRestore.mutate(
        { vm_name: vm.name, snapshot_id: snapshotId, source_vhd_path: selectedVhdPaths[0] },
        {
          onSuccess: (result) => setFileRunId(result.id),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Session konnte nicht geöffnet werden."), color: "red" }),
        },
      );
      return;
    }
    setFinishedRuns([]);
    setCurrentRunId(null);
    setCurrentVhdPath(null);
    setQueue(selectedVhdPaths);
    setActive(3);
  }

  function handleToggleFsPath(path: string, checked: boolean) {
    setSelectedFsPaths((prev) => {
      const next = new Set(prev);
      if (checked) next.add(path);
      else next.delete(path);
      return next;
    });
  }

  function handleCopySelection() {
    if (selectedFsPaths.size === 0 || !destinationPath) return;
    copySelection.mutate(
      { selected_paths: Array.from(selectedFsPaths), destination_path: destinationPath },
      {
        onSuccess: () => {
          setLastCopyResult("success");
          setSelectedFsPaths(new Set());
          notifications.show({ title: "Kopiert", message: `Nach ${destinationPath} kopiert.`, color: "green" });
        },
        onError: (err) => {
          setLastCopyResult("error");
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Kopieren fehlgeschlagen."), color: "red" });
        },
      },
    );
  }

  function handleCleanupAndClose() {
    if (!fileRunId) {
      onClose();
      return;
    }
    cleanupFileRestoreRun.mutate(fileRunId, {
      onSuccess: () => onClose(),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Aufräumen fehlgeschlagen."), color: "red" }),
    });
  }

  const batchDone = currentRunId === null && queue.length === 0 && finishedRuns.length > 0;
  const anyFailed = finishedRuns.some((f) => f.run.status === "failed");
  // Schliessen (X/Escape/Klick ausserhalb) ist bei "files" jederzeit erlaubt,
  // AUSSER waehrend der Mount-Vorgang noch laeuft -- danach bleibt die
  // Session bewusst offen (kein erzwungenes Aufraeumen), sie taucht in der
  // Restore-Uebersicht als 'Offene Session' auf und kann dort spaeter
  // wieder geoeffnet oder aufgeraeumt werden.
  const closeAllowed = restoreKind === "files" ? active < 3 || fileRun?.status !== "running" : batchDone || active < 3;

  return (
    <Modal
      opened={opened}
      onClose={closeAllowed ? onClose : () => {}}
      title={`VM wiederherstellen: ${vm?.name ?? ""}`}
      size="xl"
      closeOnClickOutside={closeAllowed}
      closeOnEscape={closeAllowed}
    >
      <Stepper active={active} size="sm">
        <Stepper.Step label="Snapshot" description="Zeitpunkt wählen">
          <Stack mt="md">
            {backupsLoading && <Loader size="sm" />}
            {!backupsLoading && backups?.length === 0 && (
              <Text c="dimmed" size="sm">
                Keine vorhandenen Backups für diese VM.
              </Text>
            )}
            <ScrollArea.Autosize mah={420} type="auto">
              <Radio.Group value={snapshotId} onChange={setSnapshotId}>
                <Stack gap="xs">
                  {backups?.map((b) => (
                    <Radio
                      key={b.id}
                      value={b.id}
                      label={
                        <Group gap="xs">
                          <Text size="sm">{new Date(b.created_at).toLocaleString("de-DE")}</Text>
                          <Badge color={b.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light" size="sm">
                            {b.consistency === "ApplicationConsistent" ? "App-konsistent" : "Crash-konsistent"}
                          </Badge>
                          <Text size="xs" c="dimmed">
                            {b.policy_name}
                          </Text>
                        </Group>
                      }
                    />
                  ))}
                </Stack>
              </Radio.Group>
            </ScrollArea.Autosize>
            <Group justify="flex-end">
              <Button onClick={() => setActive(1)} disabled={!snapshotId}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="VHDX & Modus" description="Was wiederherstellen">
          <Stack mt="md">
            <Radio.Group value={restoreKind} onChange={(v) => setRestoreKind(v as RestoreKind)} label="Was soll passieren?">
              <Stack gap="xs" mt="xs">
                <Radio value="add" label="Als zusätzliche Disk anhängen (kein Downtime, manueller Cleanup später möglich)" />
                <Radio value="replace" label="Laufende VHDX ersetzen (VM wird kurz gestoppt, alte Datei wird gelöscht)" />
                <Radio value="files" label="Nur einzelne Dateien/Ordner wiederherstellen (VHDX wird durchsuchbar gemountet)" />
              </Stack>
            </Radio.Group>

            {restoreKind === "files" ? (
              <Radio.Group
                value={selectedVhdPaths[0] ?? null}
                onChange={(v) => setSelectedVhdPaths(v ? [v] : [])}
                label="Welche VHDX soll durchsucht werden?"
              >
                <Stack gap="xs" mt="xs">
                  {vhdOptions.map((vhd) => (
                    <Radio
                      key={vhd.path}
                      value={vhd.path}
                      label={
                        <Group gap="xs">
                          <Text size="sm">{vhd.name}</Text>
                          <Text size="xs" c="dimmed">
                            ({formatBytes(vhd.size_bytes)})
                          </Text>
                        </Group>
                      }
                    />
                  ))}
                </Stack>
              </Radio.Group>
            ) : (
              <Checkbox.Group
                value={selectedVhdPaths}
                onChange={setSelectedVhdPaths}
                label="Welche VHDX sollen wiederhergestellt werden? (Mehrfachauswahl möglich)"
              >
                <Stack gap="xs" mt="xs">
                  {vhdOptions.map((vhd) => (
                    <Checkbox
                      key={vhd.path}
                      value={vhd.path}
                      label={
                        <Group gap="xs">
                          <Text size="sm">{vhd.name}</Text>
                          <Text size="xs" c="dimmed">
                            ({formatBytes(vhd.size_bytes)})
                          </Text>
                        </Group>
                      }
                    />
                  ))}
                </Stack>
              </Checkbox.Group>
            )}

            {restoreKind === "replace" && (
              <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
                Die aktuelle VHDX wird nach dem Umhängen unwiderruflich gelöscht, nicht nur umbenannt.
                {selectedVhdPaths.length > 1 && " Bei mehreren VHDX wird die VM dafür pro Datei kurz gestoppt und wieder gestartet."}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(0)}>
                Zurück
              </Button>
              <Button onClick={() => setActive(2)} disabled={selectedVhdPaths.length === 0}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Start" description="Bestätigen">
          <Stack mt="md">
            <Text size="sm">
              VM <strong>{vm?.name}</strong>, Modus{" "}
              <strong>
                {restoreKind === "files" ? "Dateien durchsuchen" : restoreKind === "add" ? "Zusatzdisk anhängen" : "Ersetzen"}
              </strong>
              .
            </Text>
            <Stack gap={4}>
              {selectedVhdPaths.map((p) => (
                <Text key={p} size="sm" ff="monospace">
                  • {p.split("\\").pop()}
                </Text>
              ))}
            </Stack>
            {restoreKind !== "files" && selectedVhdPaths.length > 1 && (
              <Text size="xs" c="dimmed">
                Die {selectedVhdPaths.length} VHDX werden nacheinander wiederhergestellt.
              </Text>
            )}
            {restoreKind === "files" && (
              <Text size="xs" c="dimmed">
                Die VHDX wird schreibgeschützt auf dem Restore-Proxy-Host gemountet und danach hier durchsuchbar.
              </Text>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(1)}>
                Zurück
              </Button>
              <Button onClick={handleTrigger} loading={triggerRestore.isPending || triggerFileRestore.isPending}>
                {restoreKind === "files" ? "Mounten & durchsuchen" : "Restore starten"}
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Fortschritt" description="Live-Status">
          {restoreKind === "files" ? (
            <Stack mt="md" gap="md">
              {(!fileRun || fileRun.status === "running") && (
                <Stack gap="sm" p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
                  {!fileRun && <Loader size="sm" />}
                  {fileRun?.steps.map((s) => (
                    <Group key={s.step} gap="xs" wrap="nowrap" align="flex-start">
                      {STEP_STATUS_ICON[s.status]}
                      <Stack gap={0} style={{ flex: 1 }}>
                        <Text size="sm" fw={600}>
                          {s.label}
                        </Text>
                        {s.status === "error" && (
                          <Text size="xs" c="red">
                            {s.message}
                          </Text>
                        )}
                      </Stack>
                    </Group>
                  ))}
                </Stack>
              )}

              {fileRun?.status === "failed" && (
                <Alert icon={<IconX size={16} />} color="red" variant="light">
                  Session konnte nicht geöffnet werden: {fileRun.error_message}
                </Alert>
              )}

              {fileRun?.status === "succeeded" && fileRun.browse_root_path && (
                <>
                  <FileBrowser
                    runId={fileRun.id}
                    rootPath={fileRun.browse_root_path}
                    selected={selectedFsPaths}
                    onToggleSelect={handleToggleFsPath}
                  />
                  <Text size="sm">{selectedFsPaths.size} Element(e) ausgewählt.</Text>
                  <TextInput
                    label="Zielpfad auf dem Restore-Proxy-Host"
                    value={destinationPath}
                    onChange={(e) => setDestinationPath(e.currentTarget.value)}
                  />
                  {lastCopyResult === "success" && (
                    <Alert icon={<IconCheck size={16} />} color="green" variant="light">
                      Zuletzt ausgewählte Elemente wurden kopiert.
                    </Alert>
                  )}
                  <Group justify="space-between">
                    <Button
                      variant="default"
                      onClick={handleCopySelection}
                      loading={copySelection.isPending}
                      disabled={selectedFsPaths.size === 0 || !destinationPath}
                    >
                      Kopieren
                    </Button>
                    <Group>
                      <Button variant="default" onClick={onClose}>
                        Später weitermachen
                      </Button>
                      <Button color="red" variant="light" onClick={handleCleanupAndClose} loading={cleanupFileRestoreRun.isPending}>
                        Fertig &amp; aufräumen
                      </Button>
                    </Group>
                  </Group>
                </>
              )}
            </Stack>
          ) : (
          <Stack mt="md" gap="md">
            {totalCount > 1 && (
              <Text size="sm" fw={600}>
                {finishedRuns.length} von {totalCount} VHDX verarbeitet
              </Text>
            )}

            {finishedRuns.map((f) => (
              <Group key={f.run.id} gap="xs" wrap="nowrap">
                {f.run.status === "succeeded" ? (
                  <IconCheck size={16} color="var(--mantine-color-green-6)" />
                ) : (
                  <IconX size={16} color="var(--mantine-color-red-6)" />
                )}
                <Text size="sm" ff="monospace">
                  {f.vhdPath.split("\\").pop()}
                </Text>
                {f.run.status === "failed" && (
                  <Text size="xs" c="red">
                    {f.run.error_message}
                  </Text>
                )}
              </Group>
            ))}

            {run && (
              <Stack gap="sm" p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
                <Text size="sm" fw={600} ff="monospace">
                  {currentVhdPath?.split("\\").pop()}
                </Text>
                {run.steps.map((s) => (
                  <Group key={s.step} gap="xs" wrap="nowrap" align="flex-start">
                    {STEP_STATUS_ICON[s.status]}
                    <Stack gap={0} style={{ flex: 1 }}>
                      <Text size="sm" fw={600}>
                        {s.label}
                      </Text>
                      {s.status === "error" && (
                        <Text size="xs" c="red">
                          {s.message}
                        </Text>
                      )}
                    </Stack>
                  </Group>
                ))}
              </Stack>
            )}

            {batchDone && !anyFailed && (
              <Alert icon={<IconCheck size={16} />} color="green" variant="light">
                {totalCount > 1 ? "Alle Restores erfolgreich abgeschlossen." : "Restore erfolgreich abgeschlossen."}
                {mode === "add" && " Denk daran, die Zusatzdisk(en) später über den Cleanup in der Restore-Übersicht zu entfernen."}
              </Alert>
            )}
            {batchDone && anyFailed && (
              <Alert icon={<IconX size={16} />} color="red" variant="light">
                Mindestens ein Restore ist fehlgeschlagen, siehe Details oben.
              </Alert>
            )}
            {batchDone && (
              <Group justify="flex-end">
                <Button onClick={onClose}>Schließen</Button>
              </Group>
            )}
          </Stack>
          )}
        </Stepper.Step>
      </Stepper>
    </Modal>
  );
}
