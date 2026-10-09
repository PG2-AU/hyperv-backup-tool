import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, Stack } from "@mantine/core";

import { PolicyRulesEditor } from "@/components/PolicyRulesEditor";
import type { NetAppSnapMirrorPolicy, PolicyEditPlan, SnapMirrorPolicyRuleWrite } from "@/api/types";

interface SnapMirrorPolicyEditModalProps {
  opened: boolean;
  onClose: () => void;
  policy: NetAppSnapMirrorPolicy | null;
  onSubmitPlan: (plan: PolicyEditPlan) => void;
}

export function SnapMirrorPolicyEditModal({ opened, onClose, policy, onSubmitPlan }: SnapMirrorPolicyEditModalProps) {
  const [rules, setRules] = useState<SnapMirrorPolicyRuleWrite[]>([]);

  useEffect(() => {
    if (!opened || !policy) return;
    setRules(policy.rules.map((r) => ({ label: r.label, count: Number(r.count) || 1, period: r.period ?? null })));
  }, [opened, policy]);

  if (!policy) return null;

  const validRules = rules.filter((r) => r.label.trim() && r.count > 0);
  const canSubmit = validRules.length > 0;

  function handleSubmit() {
    if (!policy || !canSubmit) return;
    onSubmitPlan({ clusterId: policy.cluster_id, policyUuid: policy.uuid ?? "", policyName: policy.name, rules: validRules });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={`SnapMirror-Policy bearbeiten: ${policy.name}`} size="lg">
      <Stack>
        <PolicyRulesEditor rules={rules} onChange={setRules} />
        {rules.some((r) => r.period) && (policy.lock_ineffective_on ?? []).length > 0 && (
          <Alert color="yellow" variant="light">
            Auf diesen Ziel-Volumes ist Snapshot-Locking aus, die Sperrfrist wirkt dort nicht:{" "}
            {(policy.lock_ineffective_on ?? []).join(", ")}. Einschalten auf dem Zielsystem mit{" "}
            <code>volume modify -snapshot-locking-enabled true</code>.
          </Alert>
        )}
        {rules.some((r) => r.period) && (
          <Alert color="gray" variant="light">
            Die Frist gilt für Snapshots, die ab jetzt übertragen werden. Bereits gesperrte Snapshots bleiben bis zu ihrem Ablauf
            gesperrt, auch wenn die Frist hier verkürzt oder entfernt wird; ein Ziel-Volume mit gesperrten Snapshots lässt sich so
            lange nicht löschen.
          </Alert>
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
