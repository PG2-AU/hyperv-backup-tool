import { useEffect, useState } from "react";
import { Button, Group, Modal, Stack } from "@mantine/core";

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
    setRules(policy.rules.map((r) => ({ label: r.label, count: Number(r.count) || 1 })));
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
