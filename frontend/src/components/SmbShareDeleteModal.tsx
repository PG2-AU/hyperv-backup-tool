import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Group, List, Loader, Modal, ScrollArea, SimpleGrid, Stack, Stepper, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconTrash, IconX } from "@tabler/icons-react";

import { useSmbDeleteInfo, useSmbDeleteRun, useStartSmbDelete } from "@/api/hooks.smbShares";
import type { SmbDeleteInfo } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatDateTime } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

interface SmbShareDeleteModalProps {
  onClose: () => void;
  share: { server: string; share: string; cluster_id?: string | null } | null;
}

// SMB3-Freigabe loeschen -- gleiche Regeln wie CSV loeschen: Sperre bei VMs/
// VM-Dateien und SnapMirror-Quelle, Volume als Opt-out, Bestaetigung per Name.
export function SmbShareDeleteModal({ onClose, share }: SmbShareDeleteModalProps) {
  const opened = share !== null;
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: info, isLoading, error } = useSmbDeleteInfo(share?.cluster_id, share?.server, share?.share, opened && !runId);
  const { data: run } = useSmbDeleteRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) setRunId(undefined);
  }, [opened]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title={`SMB3-Freigabe löschen: ${share ? `\\\\${share.server}\\${share.share}` : ""}`}
      size={820}
    >
      {runId ? (
        <RunView runId={runId} onClose={onClose} />
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">Inhalt, Volume, Snapshots und SnapMirror werden live geprüft…</Text>
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

function DeleteForm({ info, onStarted, onClose }: { info: SmbDeleteInfo; onStarted: (id: string) => void; onClose: () => void }) {
  const start = useStartSmbDelete();
  const [confirm, setConfirm] = useState("");
  const [deleteVolumeChoice, setDeleteVolume] = useState(info.volume_deletable);
  const deleteVolume = deleteVolumeChoice && info.volume_deletable;
  const blocked = info.blocked_reasons.length > 0;
  const unc = `\\\\${info.server}\\${info.share}`;

  function handleStart() {
    start
      .mutateAsync({
        cluster_id: info.cluster_id,
        server: info.server,
        share: info.share,
        confirm_name: confirm,
        delete_volume: deleteVolume,
      })
      .then((r) => onStarted(r.id))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Löschen konnte nicht gestartet werden."), color: "red" }),
      );
  }

  return (
    <Stack gap="md">
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Freigabe
          </Text>
          <Text size="sm">
            {unc} ({info.share_path})
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Volume ({info.netapp_cluster_name})
          </Text>
          <Text size="sm">
            {info.svm_name}:{info.volume_name} · {formatBytes(info.volume_size_bytes)} (belegt {formatBytes(info.used_bytes)}) ·{" "}
            {info.snapshots.total} Snapshot(s)
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
        label={`Volume ${info.volume_name} mit löschen`}
        description={
          info.volume_deletable
            ? "inkl. aller Dateien und Snapshots darauf"
            : "nicht möglich -- auf dem Volume liegen weitere Freigaben oder LUNs"
        }
        checked={deleteVolume}
        disabled={!info.volume_deletable}
        onChange={(e) => setDeleteVolume(e.currentTarget.checked)}
      />
      <Text size="sm">
        Ablauf: Freigabe löschen → aus Protection Groups austragen{deleteVolume ? " → Volume löschen" : ""} → Inventory.{" "}
        <b>{deleteVolume ? "Das lässt sich nicht rückgängig machen." : "Die Daten bleiben im Volume erhalten."}</b>
      </Text>
      <TextInput
        label={`Zur Bestätigung den Freigabenamen „${info.share}“ eintippen`}
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
          disabled={blocked || confirm !== info.share}
        >
          Endgültig löschen
        </Button>
      </Group>
    </Stack>
  );
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useSmbDeleteRun(runId);
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
          Freigabe \\{run.server}\{run.share} gelöscht
          {run.delete_volume ? `, Volume ${run.volume_name} gelöscht.` : ` (Volume ${run.volume_name} bleibt).`}
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
