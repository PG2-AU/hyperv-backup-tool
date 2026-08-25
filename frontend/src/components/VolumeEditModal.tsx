import { useEffect, useState } from "react";
import { Button, Group, Modal, NumberInput, Stack, Switch } from "@mantine/core";

import type { NetAppVolume, VolumeEditPlan } from "@/api/types";
import { formatBytes } from "@/utils/format";

interface VolumeEditModalProps {
  opened: boolean;
  onClose: () => void;
  volume: NetAppVolume | null;
  onSubmitPlan: (plan: VolumeEditPlan) => void;
}

const GB = 1024 ** 3;

export function VolumeEditModal({ opened, onClose, volume, onSubmitPlan }: VolumeEditModalProps) {
  const [sizeGb, setSizeGb] = useState<number | "">("");
  const [online, setOnline] = useState(true);

  useEffect(() => {
    if (!opened || !volume) return;
    setSizeGb(volume.size_bytes ? Math.round((volume.size_bytes / GB) * 100) / 100 : "");
    setOnline(volume.state !== "offline");
  }, [opened, volume]);

  if (!volume) return null;

  const sizeChanged = sizeGb !== "" && Math.round(Number(sizeGb) * GB) !== volume.size_bytes;
  const stateChanged = online !== (volume.state !== "offline");
  const canSubmit = sizeChanged || stateChanged;

  function handleSubmit() {
    if (!volume || !canSubmit) return;
    onSubmitPlan({
      clusterId: volume.cluster_id,
      volumeUuid: volume.uuid ?? "",
      volumeName: volume.name,
      newSizeBytes: sizeChanged ? Math.round(Number(sizeGb) * GB) : undefined,
      setState: stateChanged ? (online ? "online" : "offline") : undefined,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`Volume bearbeiten: ${volume.name}`}>
      <Stack>
        <NumberInput
          label={`Größe (GB) — aktuell ${formatBytes(volume.size_bytes)}`}
          min={sizeGb === "" ? undefined : Number(sizeGb)}
          value={sizeGb}
          onChange={(v) => setSizeGb(v as number | "")}
        />
        <Switch label="Online" checked={online} onChange={(e) => setOnline(e.currentTarget.checked)} />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
