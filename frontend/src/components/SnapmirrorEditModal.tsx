import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

import { useNetAppSchedules, useSnapmirrorPolicies } from "@/api/hooks";
import { PolicyRulesEditor } from "@/components/PolicyRulesEditor";
import { ScheduleCronPicker, type CronValue } from "@/components/ScheduleCronPicker";
import type { NetAppCluster, SnapMirrorPolicyRuleWrite, SnapmirrorEditPlan, SnapMirrorRelationship, VaultType } from "@/api/types";

interface SnapmirrorEditModalProps {
  opened: boolean;
  onClose: () => void;
  relationship: SnapMirrorRelationship | null;
  clusters: NetAppCluster[] | undefined;
  onSubmitPlan: (plan: SnapmirrorEditPlan) => void;
}

const NEW_POLICY_VALUE = "__new_policy__";
const NEW_SCHEDULE_VALUE = "__new_schedule__";
const NO_SCHEDULE_VALUE = "__no_schedule__";

export function SnapmirrorEditModal({ opened, onClose, relationship, clusters, onSubmitPlan }: SnapmirrorEditModalProps) {
  const [policySelection, setPolicySelection] = useState<string | null>(null);
  const [newPolicyName, setNewPolicyName] = useState("");
  const [newPolicyVaultType, setNewPolicyVaultType] = useState<VaultType>("vault");
  const [newPolicyRules, setNewPolicyRules] = useState<SnapMirrorPolicyRuleWrite[]>([{ label: "", count: 7 }]);
  const [scheduleSelection, setScheduleSelection] = useState<string | null>(null);
  const [newScheduleName, setNewScheduleName] = useState("");
  const [newScheduleCron, setNewScheduleCron] = useState<CronValue>({ minutes: [0], hours: [], days: [], weekdays: [] });

  // Die Policy/Schedule-Auswahl haengt vom ZIELCLUSTER der Beziehung ab (dort
  // muss die Policy existieren) -- das ist nicht zwangslaeufig der Cluster,
  // ueber den wir die Beziehung entdeckt haben (relationship.cluster_id kann
  // auch der Quell-Cluster sein). Wir loesen daher ueber den echten ONTAP-
  // Cluster-Namen ('destination_cluster_name') auf, welcher unserer
  // registrierten Cluster das tatsaechlich ist.
  const destinationCluster = (clusters ?? []).find((c) => c.ontap_cluster_name === relationship?.destination_cluster_name);
  const destinationClusterId = destinationCluster?.id ?? null;
  const { data: allPolicies } = useSnapmirrorPolicies();
  const { data: allSchedules } = useNetAppSchedules();
  const policies = (allPolicies ?? []).filter((p) => p.cluster_id === destinationClusterId);
  const schedules = (allSchedules ?? []).filter((s) => s.cluster_id === destinationClusterId);

  const destinationSvmName = relationship?.destination_path?.split(":")[0] ?? "";

  useEffect(() => {
    if (!opened || !relationship) return;
    setPolicySelection(relationship.policy_name ?? null);
    setScheduleSelection(relationship.schedule_name ?? NO_SCHEDULE_VALUE);
    setNewPolicyName("");
    setNewPolicyVaultType("vault");
    setNewPolicyRules([{ label: "", count: 7 }]);
    setNewScheduleName("");
    setNewScheduleCron({ minutes: [0], hours: [], days: [], weekdays: [] });
  }, [opened, relationship]);

  if (!relationship) return null;

  const policyOptions = [
    ...policies.map((p) => ({ value: p.name, label: `${p.name} (${p.svm_name ?? p.scope})` })),
    { value: NEW_POLICY_VALUE, label: "+ Neue Policy anlegen" },
  ];
  const scheduleOptions = [
    { value: NO_SCHEDULE_VALUE, label: "Kein Schedule" },
    ...schedules.map((s) => ({ value: s.name, label: s.name })),
    { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Schedule anlegen" },
  ];

  const policyMode = policySelection === NEW_POLICY_VALUE ? "new" : "existing";
  const scheduleMode = scheduleSelection === NEW_SCHEDULE_VALUE ? "new" : scheduleSelection === NO_SCHEDULE_VALUE ? "none" : "existing";
  const validNewPolicyRules = newPolicyRules.filter((r) => r.label.trim() && r.count > 0);

  const policyUnchanged = policyMode === "existing" && policySelection === (relationship.policy_name ?? null);
  const scheduleUnchanged =
    (scheduleMode === "existing" && scheduleSelection === relationship.schedule_name) ||
    (scheduleMode === "none" && !relationship.schedule_name);
  const canSubmit =
    !!destinationClusterId &&
    ((!policyUnchanged && (policyMode === "existing" ? !!policySelection : !!newPolicyName && validNewPolicyRules.length > 0)) ||
      (!scheduleUnchanged && (scheduleMode !== "new" || (!!newScheduleName && newScheduleCron.minutes.length > 0))));

  function handleSubmit() {
    if (!relationship || !canSubmit || !destinationClusterId) return;
    onSubmitPlan({
      clusterId: destinationClusterId,
      relationshipUuid: relationship.uuid ?? "",
      sourcePath: relationship.source_path ?? "",
      destinationSvmName,
      policyMode: !policyUnchanged ? policyMode : "existing",
      policyName: !policyUnchanged && policyMode === "existing" ? (policySelection ?? undefined) : undefined,
      newPolicy:
        !policyUnchanged && policyMode === "new"
          ? { svmName: destinationSvmName, name: newPolicyName, vaultType: newPolicyVaultType, rules: validNewPolicyRules }
          : undefined,
      scheduleMode: scheduleUnchanged ? "unchanged" : scheduleMode,
      scheduleName: !scheduleUnchanged && scheduleMode === "existing" ? (scheduleSelection ?? undefined) : undefined,
      newSchedule:
        !scheduleUnchanged && scheduleMode === "new"
          ? { name: newScheduleName, svmName: destinationSvmName, ...newScheduleCron }
          : undefined,
    });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`SnapMirror-Beziehung bearbeiten: ${relationship.source_path}`} size="lg">
      <Stack>
        <Text size="xs" c="dimmed">
          Zielcluster: <strong>{relationship.destination_cluster_name ?? "unbekannt"}</strong>
        </Text>
        {!destinationCluster && (
          <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
            Der Zielcluster '{relationship.destination_cluster_name ?? "?"}' ist nicht in dieser App registriert.
            Policy und Schedule leben auf dem Zielcluster und können daher hier nicht bearbeitet werden.
          </Alert>
        )}
        <Text size="sm" fw={600}>
          SnapMirror-Policy
        </Text>
        <Select
          label="Policy"
          data={policyOptions}
          value={policySelection}
          onChange={setPolicySelection}
          disabled={!destinationCluster}
          required
          searchable
        />
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
        <Select
          label="Schedule"
          data={scheduleOptions}
          value={scheduleSelection}
          onChange={setScheduleSelection}
          disabled={!destinationCluster}
          searchable
        />
        {scheduleMode === "new" && (
          <Stack gap="xs">
            <TextInput label="Name des neuen Schedules" value={newScheduleName} onChange={(e) => setNewScheduleName(e.currentTarget.value)} required />
            <ScheduleCronPicker value={newScheduleCron} onChange={setNewScheduleCron} />
          </Stack>
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
