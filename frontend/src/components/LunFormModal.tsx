import { useEffect, useState } from "react";
import { Button, Group, Modal, NumberInput, Select, SegmentedControl, Stack, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateLun } from "@/api/hooks";
import { LUN_OS_TYPES } from "@/api/types";
import type { NetAppAggregate, NetAppCluster, NetAppSvm, NetAppVolume } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface LunFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  volumes: NetAppVolume[] | undefined;
  aggregates: NetAppAggregate[] | undefined;
}

const GB = 1024 ** 3;

export function LunFormModal({ opened, onClose, clusters, svms, volumes, aggregates }: LunFormModalProps) {
  const createLun = useCreateLun();
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(null);
  const [volumeMode, setVolumeMode] = useState<"existing" | "new">("existing");
  const [volumeName, setVolumeName] = useState<string | null>(null);
  const [newVolumeName, setNewVolumeName] = useState("");
  const [newVolumeAggregate, setNewVolumeAggregate] = useState<string | null>(null);
  const [newVolumeSizeGb, setNewVolumeSizeGb] = useState<number | "">(50);
  const [lunName, setLunName] = useState("");
  const [osType, setOsType] = useState<string | null>("linux");
  const [sizeGb, setSizeGb] = useState<number | "">(10);

  const svmOptions = (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name }));
  const volumeOptions = (volumes ?? [])
    .filter((v) => v.cluster_id === clusterId && v.svm_name === svmName)
    .map((v) => ({ value: v.name, label: v.name }));
  const aggregateOptions = (aggregates ?? []).filter((a) => a.cluster_id === clusterId).map((a) => ({ value: a.name, label: a.name }));

  useEffect(() => {
    if (!opened) return;
    setClusterId(clusters?.[0]?.id ?? null);
    setSvmName(null);
    setVolumeMode("existing");
    setVolumeName(null);
    setNewVolumeName("");
    setNewVolumeAggregate(null);
    setNewVolumeSizeGb(50);
    setLunName("");
    setOsType("linux");
    setSizeGb(10);
  }, [opened, clusters]);

  const targetVolumeName = volumeMode === "existing" ? volumeName : newVolumeName;
  const canSubmit =
    !!clusterId && !!svmName && !!targetVolumeName && !!lunName && !!osType && !!sizeGb &&
    (volumeMode === "existing" || (!!newVolumeAggregate && !!newVolumeSizeGb));

  function handleSubmit() {
    if (!clusterId || !svmName || !targetVolumeName || !osType || !sizeGb) return;
    createLun.mutate(
      {
        clusterId,
        payload: {
          svm_name: svmName,
          lun_name: lunName,
          os_type: osType,
          size_bytes: Math.round(Number(sizeGb) * GB),
          volume_mode: volumeMode,
          volume_name: targetVolumeName,
          new_volume_aggregate: volumeMode === "new" ? newVolumeAggregate : undefined,
          new_volume_size_bytes: volumeMode === "new" ? Math.round(Number(newVolumeSizeGb) * GB) : undefined,
        },
      },
      {
        onSuccess: () => {
          notifications.show({ title: "LUN angelegt", message: `${targetVolumeName}/${lunName}`, color: "green" });
          onClose();
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "LUN konnte nicht angelegt werden."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neue LUN anlegen">
      <Stack>
        <Select
          label="Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={clusterId}
          onChange={(v) => {
            setClusterId(v);
            setSvmName(null);
            setVolumeName(null);
            setNewVolumeAggregate(null);
          }}
          required
        />
        <Select label="SVM" data={svmOptions} value={svmName} onChange={(v) => { setSvmName(v); setVolumeName(null); }} required searchable />

        <SegmentedControl
          data={[
            { value: "existing", label: "Vorhandenes Volume" },
            { value: "new", label: "Neues Volume anlegen" },
          ]}
          value={volumeMode}
          onChange={(v) => setVolumeMode(v as "existing" | "new")}
        />

        {volumeMode === "existing" ? (
          <Select label="Volume" data={volumeOptions} value={volumeName} onChange={setVolumeName} required searchable disabled={!svmName} />
        ) : (
          <>
            <TextInput
              label="Name des neuen Volumes"
              placeholder="z.B. lun_vol01"
              required
              value={newVolumeName}
              onChange={(e) => setNewVolumeName(e.currentTarget.value)}
            />
            <Select label="Aggregat" data={aggregateOptions} value={newVolumeAggregate} onChange={setNewVolumeAggregate} required />
            <NumberInput label="Volume-Größe (GB)" min={1} value={newVolumeSizeGb} onChange={(v) => setNewVolumeSizeGb(v as number | "")} required />
          </>
        )}

        <TextInput label="LUN-Name" placeholder="z.B. datastore01" required value={lunName} onChange={(e) => setLunName(e.currentTarget.value)} />
        <Select label="OS-Type" data={[...LUN_OS_TYPES]} value={osType} onChange={setOsType} required />
        <NumberInput label="LUN-Größe (GB)" min={1} value={sizeGb} onChange={(v) => setSizeGb(v as number | "")} required />

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createLun.isPending} disabled={!canSubmit}>
            Anlegen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
