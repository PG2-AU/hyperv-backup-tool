import { useEffect, useState } from "react";
import { Button, Group, Modal, NumberInput, Select, Stack, TextInput } from "@mantine/core";

import type { NetAppAggregate, NetAppCluster, NetAppSvm, VolumeCreationPlan } from "@/api/types";

interface VolumeFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  aggregates: NetAppAggregate[] | undefined;
  onSubmitPlan: (plan: VolumeCreationPlan) => void;
}

const GB = 1024 ** 3;

export function VolumeFormModal({ opened, onClose, clusters, svms, aggregates, onSubmitPlan }: VolumeFormModalProps) {
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [aggregateName, setAggregateName] = useState<string | null>(null);
  const [sizeGb, setSizeGb] = useState<number | "">(50);
  const [securityStyle, setSecurityStyle] = useState<string | null>("unix");
  const [guaranteeType, setGuaranteeType] = useState<string | null>("none");

  const svmOptions = (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name }));
  const aggregateOptions = (aggregates ?? []).filter((a) => a.cluster_id === clusterId).map((a) => ({ value: a.name, label: a.name }));

  useEffect(() => {
    if (!opened) return;
    setClusterId(clusters?.[0]?.id ?? null);
    setSvmName(null);
    setName("");
    setAggregateName(null);
    setSizeGb(50);
    setSecurityStyle("unix");
    setGuaranteeType("none");
  }, [opened, clusters]);

  const canSubmit = !!clusterId && !!svmName && !!name && !!aggregateName && !!sizeGb && !!securityStyle && !!guaranteeType;

  function handleSubmit() {
    if (!canSubmit || !clusterId || !svmName || !aggregateName || !sizeGb || !securityStyle || !guaranteeType) return;
    onSubmitPlan({
      clusterId,
      svmName,
      name,
      aggregateName,
      sizeBytes: Math.round(Number(sizeGb) * GB),
      securityStyle: securityStyle as "unix" | "ntfs" | "mixed",
      guaranteeType: guaranteeType as "volume" | "none",
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neues Volume anlegen">
      <Stack>
        <Select
          label="Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={clusterId}
          onChange={(v) => {
            setClusterId(v);
            setSvmName(null);
            setAggregateName(null);
          }}
          required
        />
        <Select label="SVM" data={svmOptions} value={svmName} onChange={setSvmName} required searchable />
        <TextInput label="Name" placeholder="z.B. app_data01" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Select label="Aggregat" data={aggregateOptions} value={aggregateName} onChange={setAggregateName} required />
        <NumberInput label="Größe (GB)" min={1} value={sizeGb} onChange={(v) => setSizeGb(v as number | "")} required />
        <Select
          label="Security Style"
          data={[
            { value: "unix", label: "UNIX" },
            { value: "ntfs", label: "NTFS" },
            { value: "mixed", label: "Mixed" },
          ]}
          value={securityStyle}
          onChange={setSecurityStyle}
          required
        />
        <Select
          label="Space Guarantee"
          data={[
            { value: "none", label: "Deaktiviert (thin)" },
            { value: "volume", label: "Volume (thick)" },
          ]}
          value={guaranteeType}
          onChange={setGuaranteeType}
          required
        />
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
