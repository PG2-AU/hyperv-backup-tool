import { useEffect, useState } from "react";
import { Button, Divider, Group, Modal, NumberInput, Select, SegmentedControl, Stack, TagsInput, TextInput } from "@mantine/core";

import { IGROUP_OS_TYPES, LUN_OS_TYPES } from "@/api/types";
import type { LunCreationPlan, NetAppAggregate, NetAppCluster, NetAppIgroup, NetAppSvm, NetAppVolume } from "@/api/types";

interface LunFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  volumes: NetAppVolume[] | undefined;
  aggregates: NetAppAggregate[] | undefined;
  igroups: NetAppIgroup[] | undefined;
  onSubmitPlan: (plan: LunCreationPlan) => void;
}

const GB = 1024 ** 3;
const NEW_IGROUP_VALUE = "__new_igroup__";
const NO_IGROUP_VALUE = "__no_igroup__";

export function LunFormModal({ opened, onClose, clusters, svms, volumes, aggregates, igroups, onSubmitPlan }: LunFormModalProps) {
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

  const [igroupSelection, setIgroupSelection] = useState<string | null>(NO_IGROUP_VALUE);
  const [newIgroupName, setNewIgroupName] = useState("");
  const [newIgroupOsType, setNewIgroupOsType] = useState<string | null>("linux");
  const [newIgroupProtocol, setNewIgroupProtocol] = useState<string | null>("mixed");
  const [newIgroupInitiators, setNewIgroupInitiators] = useState<string[]>([]);

  const svmOptions = (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name }));
  const volumeOptions = (volumes ?? [])
    .filter((v) => v.cluster_id === clusterId && v.svm_name === svmName)
    .map((v) => ({ value: v.name, label: v.name }));
  const aggregateOptions = (aggregates ?? []).filter((a) => a.cluster_id === clusterId).map((a) => ({ value: a.name, label: a.name }));
  const igroupOptions = [
    { value: NO_IGROUP_VALUE, label: "Keine Zuordnung" },
    ...(igroups ?? [])
      .filter((ig) => ig.cluster_id === clusterId && ig.svm_name === svmName)
      .map((ig) => ({ value: ig.name, label: ig.name })),
    { value: NEW_IGROUP_VALUE, label: "+ Neue IGroup in dieser SVM anlegen" },
  ];

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
    setIgroupSelection(NO_IGROUP_VALUE);
    setNewIgroupName("");
    setNewIgroupOsType("linux");
    setNewIgroupProtocol("mixed");
    setNewIgroupInitiators([]);
  }, [opened, clusters]);

  const targetVolumeName = volumeMode === "existing" ? volumeName : newVolumeName;
  const igroupMode: "none" | "existing" | "new" =
    igroupSelection === NO_IGROUP_VALUE ? "none" : igroupSelection === NEW_IGROUP_VALUE ? "new" : "existing";
  const newIgroupValid = igroupMode !== "new" || (!!newIgroupName && !!newIgroupOsType);

  const canSubmit =
    !!clusterId && !!svmName && !!targetVolumeName && !!lunName && !!osType && !!sizeGb &&
    (volumeMode === "existing" || (!!newVolumeAggregate && !!newVolumeSizeGb)) &&
    newIgroupValid;

  function handleSubmit() {
    if (!clusterId || !svmName || !targetVolumeName || !osType || !sizeGb || !canSubmit) return;
    onSubmitPlan({
      clusterId,
      svmName,
      volumeMode,
      volumeName: targetVolumeName,
      newVolumeAggregate: volumeMode === "new" ? (newVolumeAggregate ?? undefined) : undefined,
      newVolumeSizeBytes: volumeMode === "new" ? Math.round(Number(newVolumeSizeGb) * GB) : undefined,
      lunName,
      osType,
      lunSizeBytes: Math.round(Number(sizeGb) * GB),
      igroupMode,
      igroupName: igroupMode === "existing" ? (igroupSelection ?? undefined) : undefined,
      newIgroup:
        igroupMode === "new"
          ? {
              name: newIgroupName,
              osType: newIgroupOsType ?? "linux",
              protocol: (newIgroupProtocol as "fcp" | "iscsi" | "mixed") ?? "mixed",
              initiators: newIgroupInitiators,
            }
          : undefined,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neue LUN anlegen" size="lg">
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
            setIgroupSelection(NO_IGROUP_VALUE);
          }}
          required
        />
        <Select
          label="SVM"
          data={svmOptions}
          value={svmName}
          onChange={(v) => {
            setSvmName(v);
            setVolumeName(null);
            setIgroupSelection(NO_IGROUP_VALUE);
          }}
          required
          searchable
        />

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

        <Divider label="LUN-Mapping (IGroup)" labelPosition="left" />

        <Select
          label="IGroup"
          data={igroupOptions}
          value={igroupSelection}
          onChange={setIgroupSelection}
          disabled={!svmName}
          searchable
        />

        {igroupMode === "new" && (
          <Stack gap="xs" pl="md" style={{ borderLeft: "2px solid var(--mantine-color-default-border)" }}>
            <TextInput
              label="Name der neuen IGroup"
              placeholder="z.B. esx01_igroup"
              required
              value={newIgroupName}
              onChange={(e) => setNewIgroupName(e.currentTarget.value)}
            />
            <Select label="OS-Type" data={[...IGROUP_OS_TYPES]} value={newIgroupOsType} onChange={setNewIgroupOsType} required />
            <Select label="Protocol" data={["fcp", "iscsi", "mixed"]} value={newIgroupProtocol} onChange={setNewIgroupProtocol} />
            <TagsInput
              label="Initiatoren (optional)"
              placeholder="IQN oder WWPN eingeben und Enter drücken"
              value={newIgroupInitiators}
              onChange={setNewIgroupInitiators}
            />
          </Stack>
        )}

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            Anlegen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
