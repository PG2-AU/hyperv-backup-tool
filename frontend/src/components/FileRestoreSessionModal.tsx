import { useState } from "react";
import { Alert, Button, Group, Modal, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck } from "@tabler/icons-react";

import { useCopyFileRestoreSelection } from "@/api/hooks";
import { FileBrowser } from "@/components/FileBrowser";
import { SelectedFileList } from "@/components/SelectedFileList";
import type { FileRestoreRun } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface FileRestoreSessionModalProps {
  opened: boolean;
  onClose: () => void;
  run: FileRestoreRun | null;
}

/** Wiedereinstieg in eine bereits offene Datei-Restore-Session (siehe
 * "Offene Datei-Restore-Sessions" in RestorePage.tsx) -- mountet NICHT neu,
 * nutzt den bestehenden browse_root_path der Session. Fuer eine neue Session
 * siehe stattdessen den "Dateien wiederherstellen"-Pfad in
 * RestoreWizardModal.tsx. Bewusst KEIN Cleanup-Button hier (und auch nicht
 * im Wizard) -- ein Nutzer hat live erlebt, dass ein missverstandener
 * "Fertig & aufräumen"-Button die Session vor dem eigentlichen Kopieren
 * zerstoert hat. Aufraeumen ist daher ausschliesslich ueber die dedizierte
 * Aktion in der Tabelle "Offene Datei-Restore-Sessions" (RestorePage.tsx,
 * mit eigener Bestaetigung) oder automatisch per Zeitlimit moeglich. */
export function FileRestoreSessionModal({ opened, onClose, run }: FileRestoreSessionModalProps) {
  const [selected, setSelected] = useState<Map<string, boolean>>(new Map());
  const [destinationPath, setDestinationPath] = useState(run?.default_destination_path ?? "");
  const copySelection = useCopyFileRestoreSelection(run?.id);

  function handleToggleSelect(path: string, isDirectory: boolean, checked: boolean) {
    setSelected((prev) => {
      const next = new Map(prev);
      if (checked) next.set(path, isDirectory);
      else next.delete(path);
      return next;
    });
  }

  function handleRemove(path: string) {
    setSelected((prev) => {
      const next = new Map(prev);
      next.delete(path);
      return next;
    });
  }

  function handleCopy() {
    if (!run || selected.size === 0 || !destinationPath) return;
    copySelection.mutate(
      { selected_paths: Array.from(selected.keys()), destination_path: destinationPath },
      {
        onSuccess: () => {
          setSelected(new Map());
          notifications.show({ title: "Restore abgeschlossen", message: `Nach ${destinationPath} wiederhergestellt.`, color: "green" });
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Restore fehlgeschlagen."), color: "red" }),
      },
    );
  }

  if (!run || !run.browse_root_path) return null;

  return (
    <Modal opened={opened} onClose={onClose} title={`Dateien durchsuchen: ${run.vm_name}`} size="xl">
      <Stack gap="md">
        <FileBrowser runId={run.id} rootPath={run.browse_root_path} selected={selected} onToggleSelect={handleToggleSelect} />
        <SelectedFileList rootPath={run.browse_root_path} selected={selected} onRemove={handleRemove} />
        <TextInput
          label="Zielpfad"
          description={'Lokal auf dem Restore-Proxy-Host, oder ein UNC-Pfad zu einem anderen Rechner (z.B. \\\\ZIELSERVER\\C$\\Ordner)'}
          value={destinationPath}
          onChange={(e) => setDestinationPath(e.currentTarget.value)}
        />
        {copySelection.isSuccess && (
          <Alert icon={<IconCheck size={16} />} color="green" variant="light">
            Zuletzt ausgewählte Elemente wurden wiederhergestellt.
          </Alert>
        )}
        <Text size="xs" c="dimmed">
          Aufräumen erfolgt bewusst nur über die Tabelle "Offene Datei-Restore-Sessions" oder automatisch per Zeitlimit.
        </Text>
        <Group justify="space-between">
          <Button variant="default" onClick={handleCopy} loading={copySelection.isPending} disabled={selected.size === 0 || !destinationPath}>
            Restore
          </Button>
          <Button onClick={onClose}>Schließen</Button>
        </Group>
      </Stack>
    </Modal>
  );
}
