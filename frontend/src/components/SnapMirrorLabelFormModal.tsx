import { useEffect, useState } from "react";
import { Button, Group, Modal, Stack, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateSnapMirrorLabel, useUpdateSnapMirrorLabel } from "@/api/hooks";
import type { SnapMirrorLabel } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface SnapMirrorLabelFormModalProps {
  opened: boolean;
  onClose: () => void;
  label?: SnapMirrorLabel | null;
  onSaved?: (label: SnapMirrorLabel) => void;
}

export function SnapMirrorLabelFormModal({ opened, onClose, label, onSaved }: SnapMirrorLabelFormModalProps) {
  const createLabel = useCreateSnapMirrorLabel();
  const updateLabel = useUpdateSnapMirrorLabel();
  const isEdit = !!label;
  const [name, setName] = useState("");

  useEffect(() => {
    if (!opened) return;
    setName(label?.name ?? "");
  }, [opened, label]);

  function handleSubmit() {
    const mutation = isEdit ? updateLabel.mutateAsync({ id: label!.id, name }) : createLabel.mutateAsync(name);

    mutation
      .then((saved) => {
        notifications.show({ title: isEdit ? "Label aktualisiert" : "Label angelegt", message: saved.name, color: "green" });
        onSaved?.(saved);
        onClose();
      })
      .catch((err) => {
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Label konnte nicht gespeichert werden."), color: "red" });
      });
  }

  const isPending = createLabel.isPending || updateLabel.isPending;

  return (
    <Modal opened={opened} onClose={onClose} title={isEdit ? "SnapMirror-Label bearbeiten" : "SnapMirror-Label erstellen"}>
      <Stack>
        <TextInput label="Name" placeholder="z.B. hyperv_hourly" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={isPending} disabled={!name}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
