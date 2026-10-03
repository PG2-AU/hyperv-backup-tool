import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Group,
  List,
  Loader,
  Modal,
  ScrollArea,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconTrash, IconX } from "@tabler/icons-react";

import { useCsvDeleteInfo, useCsvDeleteRun, useStartCsvDelete } from "@/api/hooks.csvDelete";
import type { CsvDeleteInfo } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatDateTime, lunShortName } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

interface CsvDeleteModalProps {
  opened: boolean;
  onClose: () => void;
  csv: { name: string; cluster_id?: string | null } | null;
}

// CSV loeschen (Nutzer-Vorgaben 2026-09-30): CSV -> Cluster-Disk -> Protection
// Groups -> Mapping -> LUN -> Volume. Gesperrt bei VMs/VM-Dateien auf der CSV
// und bei SnapMirror-Quelle; Bestaetigung durch Eintippen des CSV-Namens.
export function CsvDeleteModal({ opened, onClose, csv }: CsvDeleteModalProps) {
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: info, isLoading, error } = useCsvDeleteInfo(csv?.cluster_id, csv?.name, opened && !runId);
  const { data: run } = useCsvDeleteRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) setRunId(undefined);
  }, [opened]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title={`CSV löschen: ${csv?.name ?? ""}`}
      size={820}
    >
      {runId ? (
        <RunView runId={runId} onClose={onClose} />
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">CSV-Inhalt, LUN, Volume, Snapshots und SnapMirror werden live geprüft…</Text>
            </Group>
          )}
          {error && <Alert color="red">{apiErrorMessage(error, "Prüfung fehlgeschlagen.")}</Alert>}
          {info && <DeleteForm info={info} onStarted={setRunId} onClose={onClose} />}
        </Stack>
      )}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

function FileList({ files, truncated }: { files: { path: string; size_bytes: number }[]; truncated?: boolean }) {
  return (
    <ScrollArea.Autosize mah={140}>
      <List size="xs" spacing={0}>
        {files.map((f) => (
          <List.Item key={f.path}>
            <Text size="xs" ff="monospace" span>
              {f.path}
            </Text>{" "}
            <Text size="xs" c="dimmed" span>
              ({formatBytes(f.size_bytes)})
            </Text>
          </List.Item>
        ))}
        {truncated && <List.Item>… und weitere</List.Item>}
      </List>
    </ScrollArea.Autosize>
  );
}

function DeleteForm({ info, onStarted, onClose }: { info: CsvDeleteInfo; onStarted: (id: string) => void; onClose: () => void }) {
  const start = useStartCsvDelete();
  const [confirm, setConfirm] = useState("");
  // Opt-out: LUN (samt Mapping) und Volume koennen auf dem Storage bleiben,
  // das Volume nur zusammen mit der LUN.
  const [deleteLun, setDeleteLun] = useState(true);
  const [deleteVolumeChoice, setDeleteVolume] = useState(info.volume_deletable);
  const deleteVolume = deleteLun && deleteVolumeChoice && info.volume_deletable;
  const blocked = info.blocked_reasons.length > 0;

  function handleStart() {
    start
      .mutateAsync({
        cluster_id: info.cluster_id,
        csv_name: info.csv_name,
        confirm_name: confirm,
        delete_lun: deleteLun,
        delete_volume: deleteVolume,
      })
      .then((r) => onStarted(r.id))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Löschen konnte nicht gestartet werden."), color: "red" }),
      );
  }

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            CSV
          </Text>
          <Text size="sm">
            {info.csv_path} · {formatBytes(info.capacity_bytes)} (belegt {formatBytes(info.used_bytes)})
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            LUN
          </Text>
          <Text size="sm">
            {lunShortName(info.lun_name)} · {formatBytes(info.lun_size_bytes)} · {info.igroups.join(", ") || "nicht gemappt"}
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Volume ({info.netapp_cluster_name})
          </Text>
          <Text size="sm">
            {info.svm_name}:{info.volume_name} · {formatBytes(info.volume_size_bytes)} · {info.snapshots.total} Snapshot(s)
          </Text>
        </Stack>
      </SimpleGrid>

      {info.blocked_reasons.map((r) => (
        <Alert key={r} color="red" icon={<IconX size={16} />} py={6}>
          {r}
        </Alert>
      ))}
      {info.vm_files.length > 0 && <FileList files={info.vm_files} />}
      {info.warnings.map((w) => (
        <Alert key={w} color="yellow" icon={<IconAlertTriangle size={16} />} py={6}>
          {w}
        </Alert>
      ))}
      {info.other_files.length > 0 && <FileList files={info.other_files} truncated={info.files_truncated} />}
      {info.snapshots.backup_count > 0 && deleteVolume && (
        <Text size="sm">
          Betroffene Backups: {info.snapshots.backup_count}, vom {formatDateTime(info.snapshots.oldest_backup)} bis{" "}
          {formatDateTime(info.snapshots.newest_backup)}.
        </Text>
      )}

      <Checkbox
        label={`LUN ${lunShortName(info.lun_name)} auf dem Storage löschen`}
        description={
          deleteLun
            ? "inkl. Mapping auf die igroups -- die Daten auf der CSV sind danach weg"
            : "LUN und Mapping bleiben unverändert, die Disk kann später wieder in den Cluster aufgenommen werden"
        }
        checked={deleteLun}
        onChange={(e) => setDeleteLun(e.currentTarget.checked)}
      />
      <Checkbox
        label={`Volume ${info.volume_name} mit löschen`}
        description={
          !deleteLun
            ? "nur zusammen mit der LUN möglich"
            : info.volume_deletable
              ? "inkl. aller Snapshots darauf"
            : `nicht möglich -- im Volume liegen weitere LUNs (${info.other_luns.map(lunShortName).join(", ")})`
        }
        checked={deleteVolume}
        disabled={!info.volume_deletable || !deleteLun}
        onChange={(e) => setDeleteVolume(e.currentTarget.checked)}
      />

      <Text size="sm">
        Ablauf: CSV entfernen → Cluster-Disk entfernen → aus Protection Groups austragen
        {deleteLun ? " → Mapping aufheben → LUN löschen" : ""}
        {deleteVolume ? " → Volume löschen" : ""} → neu einlesen.{" "}
        <b>{deleteLun ? "Das lässt sich nicht rückgängig machen." : "Die Daten bleiben auf der LUN erhalten."}</b>
      </Text>
      <TextInput
        label={`Zur Bestätigung den CSV-Namen „${info.csv_name}“ eintippen`}
        value={confirm}
        onChange={(e) => setConfirm(e.currentTarget.value)}
        disabled={blocked}
        autoComplete="off"
      />
      <Group justify="flex-end">
        <Button variant="default" onClick={onClose}>
          Abbrechen
        </Button>
        <Button
          color="red"
          leftSection={<IconTrash size={16} />}
          onClick={handleStart}
          loading={start.isPending}
          disabled={blocked || confirm !== info.csv_name}
        >
          Endgültig löschen
        </Button>
      </Group>
    </Stack>
  );
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useCsvDeleteRun(runId);
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
      {run.status === "succeeded" && (
        <Alert color="green" icon={<IconCheck size={16} />}>
          CSV {run.csv_name} gelöscht.{" "}
          {run.delete_lun
            ? `LUN ${lunShortName(run.lun_name)}${run.delete_volume ? ` und Volume ${run.volume_name}` : ""} gelöscht${run.delete_volume ? "" : ` (Volume ${run.volume_name} bleibt)`}.`
            : `LUN ${lunShortName(run.lun_name)} samt Mapping und Volume ${run.volume_name} bleiben auf dem Storage.`}
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
