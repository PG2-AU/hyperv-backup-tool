import { useEffect, useState } from "react";
import { Button, Group, Modal, NumberInput, Select, Stack, Switch, Text } from "@mantine/core";

import type { LunEditPlan, NetAppIgroup, NetAppLun } from "@/api/types";
import { formatBytes } from "@/utils/format";

interface LunEditModalProps {
  opened: boolean;
  onClose: () => void;
  lun: NetAppLun | null;
  igroups: NetAppIgroup[] | undefined;
  onSubmitPlan: (plan: LunEditPlan) => void;
}

const GB = 1024 ** 3;

function shortName(fullPath: string): string {
  const parts = fullPath.split("/");
  return parts[parts.length - 1];
}

export function LunEditModal({ opened, onClose, lun, igroups, onSubmitPlan }: LunEditModalProps) {
  const [sizeGb, setSizeGb] = useState<number | "">("");
  const [enabled, setEnabled] = useState(true);
  const [unmapIgroupName, setUnmapIgroupName] = useState<string | null>(null);
  const [mapIgroupName, setMapIgroupName] = useState<string | null>(null);

  const currentMapped = (lun?.mapped_igroups ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const availableIgroups = (igroups ?? [])
    .filter((ig) => ig.cluster_id === lun?.cluster_id && ig.svm_name === lun?.svm_name && !currentMapped.includes(ig.name))
    .map((ig) => ({ value: ig.name, label: ig.name }));

  useEffect(() => {
    if (!opened || !lun) return;
    setSizeGb(lun.size_bytes ? Math.round((lun.size_bytes / GB) * 100) / 100 : "");
    setEnabled(lun.state !== "offline");
    setUnmapIgroupName(null);
    setMapIgroupName(null);
  }, [opened, lun]);

  if (!lun) return null;

  const currentShort = shortName(lun.name);
  const sizeChanged = sizeGb !== "" && Math.round(Number(sizeGb) * GB) !== lun.size_bytes;
  const enabledChanged = enabled !== (lun.state !== "offline");
  const canSubmit = sizeChanged || enabledChanged || !!unmapIgroupName || !!mapIgroupName;

  function handleSubmit() {
    if (!lun || !canSubmit || !lun.volume_name) return;
    onSubmitPlan({
      clusterId: lun.cluster_id,
      lunUuid: lun.uuid ?? "",
      svmName: lun.svm_name ?? "",
      volumeName: lun.volume_name,
      currentShortName: currentShort,
      newSizeBytes: sizeChanged ? Math.round(Number(sizeGb) * GB) : undefined,
      setEnabled: enabledChanged ? enabled : undefined,
      unmapIgroupName: unmapIgroupName ?? undefined,
      mapIgroupName: mapIgroupName ?? undefined,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`LUN bearbeiten: ${currentShort}`}>
      <Stack>
        <Text size="sm" c="dimmed">
          Name: {currentShort} (nicht änderbar)
        </Text>
        <NumberInput
          label={`Größe (GB) — aktuell ${formatBytes(lun.size_bytes)}`}
          min={sizeGb === "" ? undefined : Number(sizeGb)}
          value={sizeGb}
          onChange={(v) => setSizeGb(v as number | "")}
        />
        <Switch label="Online" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />

        <Text size="sm" fw={600} mt="sm">
          IGroup-Mapping
        </Text>
        <Text size="xs" c="dimmed">
          Aktuell zugeordnet: {currentMapped.length ? currentMapped.join(", ") : "keine"}
        </Text>
        {currentMapped.length > 0 && (
          <Select
            label="Mapping entfernen"
            placeholder="Keine Auswahl"
            data={currentMapped.map((name) => ({ value: name, label: name }))}
            value={unmapIgroupName}
            onChange={setUnmapIgroupName}
            clearable
          />
        )}
        <Select
          label="Neues Mapping hinzufügen"
          placeholder="Keine Auswahl"
          data={availableIgroups}
          value={mapIgroupName}
          onChange={setMapIgroupName}
          clearable
          searchable
        />

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
