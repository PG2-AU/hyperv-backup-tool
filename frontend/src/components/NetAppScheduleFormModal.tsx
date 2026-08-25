import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";

import { ScheduleCronPicker, type CronValue } from "@/components/ScheduleCronPicker";
import type { NetAppCluster, NetAppSvm, ScheduleCreationPlan } from "@/api/types";

interface NetAppScheduleFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  initialClusterId?: string | null;
  onSubmitPlan: (plan: ScheduleCreationPlan) => void;
}

const NO_SVM_VALUE = "__cluster_scoped__";

export function NetAppScheduleFormModal({ opened, onClose, clusters, svms, initialClusterId, onSubmitPlan }: NetAppScheduleFormModalProps) {
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(NO_SVM_VALUE);
  const [name, setName] = useState("");
  const [cron, setCron] = useState<CronValue>({ minutes: [], hours: [], days: [], weekdays: [] });

  useEffect(() => {
    if (!opened) return;
    setClusterId(initialClusterId ?? clusters?.[0]?.id ?? null);
    setSvmName(NO_SVM_VALUE);
    setName("");
    setCron({ minutes: [], hours: [], days: [], weekdays: [] });
  }, [opened, clusters, initialClusterId]);

  const svmOptions = [
    { value: NO_SVM_VALUE, label: "Cluster-weit (keine SVM)" },
    ...(svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name })),
  ];
  const canSubmit = !!clusterId && !!name.trim() && cron.minutes.length > 0;

  function handleSubmit() {
    if (!canSubmit || !clusterId) return;
    onSubmitPlan({
      clusterId,
      svmName: svmName && svmName !== NO_SVM_VALUE ? svmName : undefined,
      name,
      minutes: cron.minutes,
      hours: cron.hours,
      days: cron.days,
      weekdays: cron.weekdays,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neuer Schedule" size="lg">
      <Stack>
        <Group grow>
          <Select
            label="Cluster"
            data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
            value={clusterId}
            onChange={(v) => { setClusterId(v); setSvmName(NO_SVM_VALUE); }}
            disabled={!!initialClusterId}
            required
          />
          <Select label="SVM" data={svmOptions} value={svmName} onChange={setSvmName} searchable />
        </Group>
        <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
        <ScheduleCronPicker value={cron} onChange={setCron} />
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
