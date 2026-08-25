import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";

import { useNetAppSchedules, useSnapmirrorPolicies } from "@/api/hooks";
import type { SchedulePreset, SnapmirrorEditPlan, SnapMirrorRelationship } from "@/api/types";

interface SnapmirrorEditModalProps {
  opened: boolean;
  onClose: () => void;
  relationship: SnapMirrorRelationship | null;
  onSubmitPlan: (plan: SnapmirrorEditPlan) => void;
}

const NEW_POLICY_VALUE = "__new_policy__";
const NEW_SCHEDULE_VALUE = "__new_schedule__";
const NO_SCHEDULE_VALUE = "__no_schedule__";

const SCHEDULE_PRESETS: { value: SchedulePreset; label: string }[] = [
  { value: "every_5min", label: "Alle 5 Minuten" },
  { value: "every_15min", label: "Alle 15 Minuten" },
  { value: "every_30min", label: "Alle 30 Minuten" },
  { value: "hourly", label: "Stündlich" },
  { value: "daily", label: "Täglich" },
];

export function SnapmirrorEditModal({ opened, onClose, relationship, onSubmitPlan }: SnapmirrorEditModalProps) {
  const [policySelection, setPolicySelection] = useState<string | null>(null);
  const [newPolicyName, setNewPolicyName] = useState("");
  const [newPolicyType, setNewPolicyType] = useState<string | null>("async");
  const [scheduleSelection, setScheduleSelection] = useState<string | null>(null);
  const [newScheduleName, setNewScheduleName] = useState("");
  const [newSchedulePreset, setNewSchedulePreset] = useState<SchedulePreset | null>("hourly");

  const clusterId = relationship?.cluster_id ?? null;
  const { data: policies } = useSnapmirrorPolicies(clusterId);
  const { data: schedules } = useNetAppSchedules(clusterId);

  const destinationSvmName = relationship?.destination_path?.split(":")[0] ?? "";

  useEffect(() => {
    if (!opened || !relationship) return;
    setPolicySelection(relationship.policy_name ?? null);
    setScheduleSelection(relationship.schedule_name ?? NO_SCHEDULE_VALUE);
    setNewPolicyName("");
    setNewPolicyType("async");
    setNewScheduleName("");
    setNewSchedulePreset("hourly");
  }, [opened, relationship]);

  if (!relationship) return null;

  const policyOptions = [
    ...(policies ?? []).map((p) => ({ value: p.name, label: `${p.name} (${p.type})` })),
    { value: NEW_POLICY_VALUE, label: "+ Neue Policy anlegen" },
  ];
  const scheduleOptions = [
    { value: NO_SCHEDULE_VALUE, label: "Kein Schedule" },
    ...(schedules ?? []).map((s) => ({ value: s.name, label: s.name })),
    { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Schedule anlegen" },
  ];

  const policyMode = policySelection === NEW_POLICY_VALUE ? "new" : "existing";
  const scheduleMode = scheduleSelection === NEW_SCHEDULE_VALUE ? "new" : scheduleSelection === NO_SCHEDULE_VALUE ? "none" : "existing";

  const policyUnchanged = policyMode === "existing" && policySelection === (relationship.policy_name ?? null);
  const scheduleUnchanged =
    (scheduleMode === "existing" && scheduleSelection === relationship.schedule_name) ||
    (scheduleMode === "none" && !relationship.schedule_name);
  const canSubmit =
    (!policyUnchanged && (policyMode === "existing" ? !!policySelection : !!newPolicyName)) ||
    (!scheduleUnchanged && (scheduleMode !== "new" || !!newScheduleName));

  function handleSubmit() {
    if (!relationship || !canSubmit) return;
    onSubmitPlan({
      clusterId: relationship.cluster_id,
      relationshipUuid: relationship.uuid ?? "",
      sourcePath: relationship.source_path ?? "",
      destinationSvmName,
      policyMode: !policyUnchanged ? policyMode : "existing",
      policyName: !policyUnchanged && policyMode === "existing" ? (policySelection ?? undefined) : undefined,
      newPolicy: !policyUnchanged && policyMode === "new" ? { svmName: destinationSvmName, name: newPolicyName, type: (newPolicyType as "async" | "sync") ?? "async" } : undefined,
      scheduleMode: scheduleUnchanged ? "unchanged" : scheduleMode,
      scheduleName: !scheduleUnchanged && scheduleMode === "existing" ? (scheduleSelection ?? undefined) : undefined,
      newSchedule: !scheduleUnchanged && scheduleMode === "new" ? { name: newScheduleName, preset: newSchedulePreset ?? "hourly" } : undefined,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`SnapMirror-Beziehung bearbeiten: ${relationship.source_path}`} size="lg">
      <Stack>
        <Text size="sm" fw={600}>
          SnapMirror-Policy
        </Text>
        <Select label="Policy" data={policyOptions} value={policySelection} onChange={setPolicySelection} required searchable />
        {policyMode === "new" && (
          <Group grow>
            <TextInput label="Name der neuen Policy" value={newPolicyName} onChange={(e) => setNewPolicyName(e.currentTarget.value)} required />
            <Select label="Typ" data={["async", "sync"]} value={newPolicyType} onChange={setNewPolicyType} required />
          </Group>
        )}

        <Text size="sm" fw={600} mt="sm">
          Schedule
        </Text>
        <Select label="Schedule" data={scheduleOptions} value={scheduleSelection} onChange={setScheduleSelection} searchable />
        {scheduleMode === "new" && (
          <Group grow>
            <TextInput label="Name des neuen Schedules" value={newScheduleName} onChange={(e) => setNewScheduleName(e.currentTarget.value)} required />
            <Select
              label="Intervall"
              data={SCHEDULE_PRESETS}
              value={newSchedulePreset}
              onChange={(v) => setNewSchedulePreset(v as SchedulePreset)}
              required
            />
          </Group>
        )}

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
