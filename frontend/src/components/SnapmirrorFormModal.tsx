import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, Switch, Text, TextInput } from "@mantine/core";

import { useNetAppSchedules, useSnapmirrorPolicies } from "@/api/hooks";
import { PolicyRulesEditor } from "@/components/PolicyRulesEditor";
import { ScheduleCronPicker, type CronValue } from "@/components/ScheduleCronPicker";
import type { NetAppAggregate, NetAppCluster, NetAppSvm, NetAppVolume, SnapMirrorPolicyRuleWrite, SnapmirrorCreationPlan, VaultType } from "@/api/types";

interface InitialSource {
  clusterId: string;
  svmName: string;
  volumeName: string;
  sizeBytes: number;
}

interface SnapmirrorFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  volumes: NetAppVolume[] | undefined;
  aggregates: NetAppAggregate[] | undefined;
  initialSource?: InitialSource | null;
  onSubmitPlan: (plan: SnapmirrorCreationPlan) => void;
}

const NEW_POLICY_VALUE = "__new_policy__";
const NEW_SCHEDULE_VALUE = "__new_schedule__";
const NO_SCHEDULE_VALUE = "__no_schedule__";

export function SnapmirrorFormModal({
  opened, onClose, clusters, svms, volumes, aggregates, initialSource, onSubmitPlan,
}: SnapmirrorFormModalProps) {
  const [sourceClusterId, setSourceClusterId] = useState<string | null>(null);
  const [sourceSvmName, setSourceSvmName] = useState<string | null>(null);
  const [sourceVolumeName, setSourceVolumeName] = useState<string | null>(null);
  const [destinationClusterId, setDestinationClusterId] = useState<string | null>(null);
  const [destinationSvmName, setDestinationSvmName] = useState<string | null>(null);
  const [destinationPrefix, setDestinationPrefix] = useState("dst_");
  const [destinationAggregate, setDestinationAggregate] = useState<string | null>(null);
  const [policySelection, setPolicySelection] = useState<string | null>(null);
  const [newPolicyName, setNewPolicyName] = useState("");
  const [newPolicyVaultType, setNewPolicyVaultType] = useState<VaultType>("vault");
  const [newPolicyRules, setNewPolicyRules] = useState<SnapMirrorPolicyRuleWrite[]>([{ label: "", count: 7 }]);
  const [scheduleSelection, setScheduleSelection] = useState<string | null>(NO_SCHEDULE_VALUE);
  const [newScheduleName, setNewScheduleName] = useState("");
  const [newScheduleCron, setNewScheduleCron] = useState<CronValue>({ minutes: [0], hours: [], days: [], weekdays: [] });
  const [autoInitialize, setAutoInitialize] = useState(true);

  const { data: allPolicies } = useSnapmirrorPolicies();
  const { data: allSchedules } = useNetAppSchedules();
  const policies = (allPolicies ?? []).filter((p) => p.cluster_id === destinationClusterId);
  const schedules = (allSchedules ?? []).filter((s) => s.cluster_id === destinationClusterId);

  useEffect(() => {
    if (!opened) return;
    const src = initialSource;
    setSourceClusterId(src?.clusterId ?? clusters?.[0]?.id ?? null);
    setSourceSvmName(src?.svmName ?? null);
    setSourceVolumeName(src?.volumeName ?? null);
    setDestinationClusterId(src?.clusterId ?? clusters?.[0]?.id ?? null);
    setDestinationSvmName(null);
    setDestinationPrefix("dst_");
    setDestinationAggregate(null);
    setPolicySelection(null);
    setNewPolicyName("");
    setNewPolicyVaultType("vault");
    setNewPolicyRules([{ label: "", count: 7 }]);
    setScheduleSelection(NO_SCHEDULE_VALUE);
    setNewScheduleName("");
    setNewScheduleCron({ minutes: [0], hours: [], days: [], weekdays: [] });
    setAutoInitialize(true);
  }, [opened, clusters, initialSource]);

  const sourceSvmOptions = (svms ?? []).filter((s) => s.cluster_id === sourceClusterId).map((s) => ({ value: s.name, label: s.name }));
  const sourceVolumeOptions = (volumes ?? [])
    .filter((v) => v.cluster_id === sourceClusterId && v.svm_name === sourceSvmName)
    .map((v) => ({ value: v.name, label: v.name }));
  const destinationSvmOptions = (svms ?? []).filter((s) => s.cluster_id === destinationClusterId).map((s) => ({ value: s.name, label: s.name }));
  const destinationAggregateOptions = (aggregates ?? []).filter((a) => a.cluster_id === destinationClusterId).map((a) => ({ value: a.name, label: a.name }));

  const policyOptions = [
    ...policies.map((p) => ({ value: p.name, label: `${p.name} (${p.svm_name ?? p.scope})` })),
    { value: NEW_POLICY_VALUE, label: "+ Neue Policy anlegen" },
  ];
  const scheduleOptions = [
    { value: NO_SCHEDULE_VALUE, label: "Kein Schedule" },
    ...schedules.map((s) => ({ value: s.name, label: s.name })),
    { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Schedule anlegen" },
  ];

  const sourceVolume = (volumes ?? []).find(
    (v) => v.cluster_id === sourceClusterId && v.svm_name === sourceSvmName && v.name === sourceVolumeName,
  );
  const destinationVolumeName = sourceVolumeName ? `${destinationPrefix}${sourceVolumeName}` : "";
  const policyMode = policySelection === NEW_POLICY_VALUE ? "new" : "existing";
  const scheduleMode = scheduleSelection === NEW_SCHEDULE_VALUE ? "new" : scheduleSelection === NO_SCHEDULE_VALUE ? "none" : "existing";
  const validNewPolicyRules = newPolicyRules.filter((r) => r.label.trim() && r.count > 0);

  const canSubmit =
    !!sourceClusterId && !!sourceSvmName && !!sourceVolumeName && !!destinationClusterId && !!destinationSvmName &&
    !!destinationAggregate && !!destinationVolumeName && !!sourceVolume &&
    (policyMode === "existing" ? !!policySelection : !!newPolicyName && validNewPolicyRules.length > 0) &&
    (scheduleMode !== "new" || (!!newScheduleName && newScheduleCron.minutes.length > 0));

  function handleSubmit() {
    if (!canSubmit || !sourceClusterId || !sourceSvmName || !sourceVolumeName || !destinationClusterId || !destinationSvmName || !destinationAggregate || !sourceVolume) {
      return;
    }
    onSubmitPlan({
      sourceClusterId,
      sourceSvmName,
      sourceVolumeName,
      sourceVolumeSizeBytes: sourceVolume.size_bytes ?? 1073741824,
      destinationClusterId,
      destinationSvmName,
      destinationVolumeName,
      destinationAggregate,
      policyMode,
      policyName: policyMode === "existing" ? (policySelection ?? undefined) : undefined,
      newPolicy:
        policyMode === "new"
          ? { svmName: destinationSvmName, name: newPolicyName, vaultType: newPolicyVaultType, rules: validNewPolicyRules }
          : undefined,
      scheduleMode,
      scheduleName: scheduleMode === "existing" ? (scheduleSelection ?? undefined) : undefined,
      newSchedule:
        scheduleMode === "new"
          ? { name: newScheduleName, svmName: destinationSvmName, ...newScheduleCron }
          : undefined,
      autoInitialize,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neue SnapMirror-Beziehung" size="lg">
      <Stack>
        <Text size="sm" fw={600}>
          Quelle
        </Text>
        <Select
          label="Quell-Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={sourceClusterId}
          onChange={(v) => { setSourceClusterId(v); setSourceSvmName(null); setSourceVolumeName(null); }}
          disabled={!!initialSource}
          required
        />
        <Select
          label="Quell-SVM"
          data={sourceSvmOptions}
          value={sourceSvmName}
          onChange={(v) => { setSourceSvmName(v); setSourceVolumeName(null); }}
          disabled={!!initialSource}
          required
          searchable
        />
        <Select
          label="Quell-Volume"
          data={sourceVolumeOptions}
          value={sourceVolumeName}
          onChange={setSourceVolumeName}
          disabled={!!initialSource}
          required
          searchable
        />

        <Text size="sm" fw={600} mt="sm">
          Ziel
        </Text>
        <Select
          label="Ziel-Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={destinationClusterId}
          onChange={(v) => { setDestinationClusterId(v); setDestinationSvmName(null); setDestinationAggregate(null); setPolicySelection(null); setScheduleSelection(NO_SCHEDULE_VALUE); }}
          required
        />
        <Select label="Ziel-SVM" data={destinationSvmOptions} value={destinationSvmName} onChange={setDestinationSvmName} required searchable />
        <TextInput
          label="Ziel-Volume-Präfix"
          value={destinationPrefix}
          onChange={(e) => setDestinationPrefix(e.currentTarget.value)}
        />
        {sourceVolumeName && (
          <Text size="xs" c="dimmed">
            Ziel-Volume-Name: <strong>{destinationVolumeName}</strong>
          </Text>
        )}
        <Select label="Ziel-Aggregat" data={destinationAggregateOptions} value={destinationAggregate} onChange={setDestinationAggregate} required />

        <Text size="sm" fw={600} mt="sm">
          SnapMirror-Policy
        </Text>
        <Select label="Policy" data={policyOptions} value={policySelection} onChange={setPolicySelection} required searchable />
        {policyMode === "new" && (
          <Stack gap="xs">
            <Group grow>
              <TextInput label="Name der neuen Policy" value={newPolicyName} onChange={(e) => setNewPolicyName(e.currentTarget.value)} required />
              <Select
                label="Typ"
                data={[
                  { value: "vault", label: "Vault (nur Retention-Regeln)" },
                  { value: "mirror_vault", label: "Mirror-Vault (Spiegelung + Retention-Regeln)" },
                ]}
                value={newPolicyVaultType}
                onChange={(v) => setNewPolicyVaultType((v as VaultType) ?? "vault")}
                required
              />
            </Group>
            <PolicyRulesEditor rules={newPolicyRules} onChange={setNewPolicyRules} />
          </Stack>
        )}

        <Text size="sm" fw={600} mt="sm">
          Schedule
        </Text>
        <Select label="Schedule" data={scheduleOptions} value={scheduleSelection} onChange={setScheduleSelection} searchable />
        {scheduleMode === "new" && (
          <Stack gap="xs">
            <TextInput label="Name des neuen Schedules" value={newScheduleName} onChange={(e) => setNewScheduleName(e.currentTarget.value)} required />
            <ScheduleCronPicker value={newScheduleCron} onChange={setNewScheduleCron} />
          </Stack>
        )}

        <Switch
          label="Auto Initialize (Baseline-Transfer sofort starten)"
          checked={autoInitialize}
          onChange={(e) => setAutoInitialize(e.currentTarget.checked)}
          mt="sm"
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
