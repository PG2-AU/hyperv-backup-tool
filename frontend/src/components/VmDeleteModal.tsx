import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Group, List, Loader, Modal, ScrollArea, SimpleGrid, Stack, Stepper, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconTrash, IconX } from "@tabler/icons-react";

import { useStartVmDelete, useVmDeleteInfo, useVmDeleteRun } from "@/api/hooks.vmDelete";
import type { VmDeleteInfo } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

interface VmDeleteModalProps {
  vm: { name: string; cluster_id?: string | null } | null;
  onClose: () => void;
}

// VM loeschen: Cluster-Rolle + VM entfernen, aus Protection Groups austragen,
// Disk-Dateien als Opt-out loeschen. Laufende VM nur mit ausdruecklichem
// "vorher hart ausschalten"; Bestaetigung per VM-Name.
export function VmDeleteModal({ vm, onClose }: VmDeleteModalProps) {
  const opened = vm !== null;
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: info, isLoading, error } = useVmDeleteInfo(vm?.cluster_id, vm?.name, opened && !runId);
  const { data: run } = useVmDeleteRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) setRunId(undefined);
  }, [opened]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title={`VM löschen: ${vm?.name ?? ""}`}
      size={820}
    >
      {runId ? (
        <RunView runId={runId} onClose={onClose} />
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">Status, Festplatten und Checkpoints werden live abgefragt…</Text>
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

function DeleteForm({ info, onStarted, onClose }: { info: VmDeleteInfo; onStarted: (id: string) => void; onClose: () => void }) {
  const start = useStartVmDelete();
  const [confirm, setConfirm] = useState("");
  const [deleteFiles, setDeleteFiles] = useState(true);
  const [turnOff, setTurnOff] = useState(false);
  const blocked = info.blocked_reasons.length > 0;
  const isOff = info.state === "Off";
  const deletable = info.files.filter((f) => !f.shared_with);

  function handleStart() {
    start
      .mutateAsync({
        cluster_id: info.cluster_id,
        vm_name: info.vm_name,
        confirm_name: confirm,
        delete_files: deleteFiles,
        turn_off: !isOff && turnOff,
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
            Knoten / Status
          </Text>
          <Text size="sm">
            {info.node} · {info.state}
            {info.checkpoint_count ? ` · ${info.checkpoint_count} Checkpoint(s)` : ""}
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Konfiguration
          </Text>
          <Text size="sm" style={{ overflowWrap: "anywhere" }}>
            {info.configuration_location ?? "–"}
          </Text>
        </Stack>
        <Stack gap={0}>
          <Text size="xs" c="dimmed">
            Festplatten
          </Text>
          <Text size="sm">
            {deletable.length} Datei(en), {formatBytes(info.total_bytes)}
          </Text>
        </Stack>
      </SimpleGrid>

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
      {info.backup_count > 0 && (
        <Alert color="blue" variant="light" icon={<IconInfoCircle size={16} />} py={6}>
          {info.backup_count} Backup(s) dieser VM bleiben erhalten -- sie lässt sich daraus über Restore wieder neu erstellen.
        </Alert>
      )}

      {!isOff && (
        <Checkbox
          color="red"
          label="Laufende VM vorher hart ausschalten"
          description="entspricht dem Ziehen des Netzsteckers -- sauberer ist, die VM vorher über das Power-Menü herunterzufahren"
          checked={turnOff}
          onChange={(e) => setTurnOff(e.currentTarget.checked)}
        />
      )}
      <Checkbox
        label="Festplatten-Dateien mit löschen"
        description={
          deleteFiles
            ? "danach werden nur leer gewordene Ordner der VM entfernt"
            : "die VM wird nur aus Hyper-V entfernt, die VHDX-Dateien bleiben auf dem Storage liegen"
        }
        checked={deleteFiles}
        onChange={(e) => setDeleteFiles(e.currentTarget.checked)}
      />
      {deleteFiles && info.files.length > 0 && (
        <ScrollArea.Autosize mah={150}>
          <List size="xs" spacing={0}>
            {info.files.map((f) => (
              <List.Item key={f.path}>
                <Text size="xs" ff="monospace" span td={f.shared_with ? "line-through" : undefined}>
                  {f.path}
                </Text>{" "}
                <Text size="xs" c="dimmed" span>
                  ({f.size_bytes != null ? formatBytes(f.size_bytes) : "nicht lesbar"}
                  {f.shared_with ? `, bleibt -- auch an ${f.shared_with}` : ""})
                </Text>
              </List.Item>
            ))}
          </List>
        </ScrollArea.Autosize>
      )}

      <Text size="sm">
        Ablauf: Cluster-Rolle entfernen → VM entfernen → aus Protection Groups austragen
        {deleteFiles ? " → Dateien löschen" : ""} → Inventory.{" "}
        <b>{deleteFiles ? "Das lässt sich nicht rückgängig machen." : "Die Festplatten bleiben erhalten."}</b>
      </Text>
      <TextInput
        label={`Zur Bestätigung den VM-Namen „${info.vm_name}“ eintippen`}
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
          disabled={blocked || confirm !== info.vm_name || (!isOff && !turnOff)}
        >
          Endgültig löschen
        </Button>
      </Group>
    </Stack>
  );
}

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useVmDeleteRun(runId);
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
          VM {run.vm_name} gelöscht{run.delete_files ? " (inkl. Festplatten-Dateien)" : " -- die Festplatten-Dateien bleiben erhalten"}.
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
