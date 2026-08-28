import { useState } from "react";
import { Alert, Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck } from "@tabler/icons-react";

import { useCleanupFileRestoreRun, useCopyFileRestoreSelection } from "@/api/hooks";
import { FileBrowser } from "@/components/FileBrowser";
import type { FileRestoreRun } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { confirmAction } from "@/utils/confirm";

interface FileRestoreSessionModalProps {
  opened: boolean;
  onClose: () => void;
  run: FileRestoreRun | null;
}

/** Wiedereinstieg in eine bereits offene Datei-Restore-Session (siehe
 * "Offene Datei-Restore-Sessions" in RestorePage.tsx) -- mountet NICHT neu,
 * nutzt den bestehenden browse_root_path der Session. Fuer eine neue Session
 * siehe stattdessen den "Dateien wiederherstellen"-Pfad in
 * RestoreWizardModal.tsx. */
export function FileRestoreSessionModal({ opened, onClose, run }: FileRestoreSessionModalProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [destinationPath, setDestinationPath] = useState(run?.default_destination_path ?? "");
  const copySelection = useCopyFileRestoreSelection(run?.id);
  const cleanupFileRestoreRun = useCleanupFileRestoreRun();

  function handleToggleSelect(path: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(path);
      else next.delete(path);
      return next;
    });
  }

  function handleCopy() {
    if (!run || selected.size === 0 || !destinationPath) return;
    copySelection.mutate(
      { selected_paths: Array.from(selected), destination_path: destinationPath },
      {
        onSuccess: () => {
          setSelected(new Set());
          notifications.show({ title: "Kopiert", message: `Nach ${destinationPath} kopiert.`, color: "green" });
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Kopieren fehlgeschlagen."), color: "red" }),
      },
    );
  }

  function handleCleanup() {
    if (!run) return;
    confirmAction({
      title: "Session aufräumen",
      message: `VHDX für '${run.vm_name}' aushängen und temporären LUN-Klon entfernen?`,
      confirmLabel: "Aufräumen",
      onConfirm: () =>
        cleanupFileRestoreRun.mutate(run.id, {
          onSuccess: () => onClose(),
          onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Aufräumen fehlgeschlagen."), color: "red" }),
        }),
    });
  }

  if (!run || !run.browse_root_path) return null;

  return (
    <Modal opened={opened} onClose={onClose} title={`Dateien durchsuchen: ${run.vm_name}`} size="xl">
      <Stack gap="md">
        <FileBrowser runId={run.id} rootPath={run.browse_root_path} selected={selected} onToggleSelect={handleToggleSelect} />
        <Text size="sm">{selected.size} Element(e) ausgewählt.</Text>
        <TextInput
          label="Zielpfad auf dem Restore-Proxy-Host"
          value={destinationPath}
          onChange={(e) => setDestinationPath(e.currentTarget.value)}
        />
        {copySelection.isSuccess && (
          <Alert icon={<IconCheck size={16} />} color="green" variant="light">
            Zuletzt ausgewählte Elemente wurden kopiert.
          </Alert>
        )}
        <Group justify="space-between">
          <Button variant="default" onClick={handleCopy} loading={copySelection.isPending} disabled={selected.size === 0 || !destinationPath}>
            Kopieren
          </Button>
          <Group>
            <Button variant="default" onClick={onClose}>
              Schließen
            </Button>
            <Button color="red" variant="light" onClick={handleCleanup} loading={cleanupFileRestoreRun.isPending}>
              Fertig &amp; aufräumen
            </Button>
          </Group>
        </Group>
      </Stack>
    </Modal>
  );
}
