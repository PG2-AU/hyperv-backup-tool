import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, TextInput } from "@mantine/core";

import { PolicyRulesEditor } from "@/components/PolicyRulesEditor";
import type { NetAppCluster, NetAppSvm, PolicyCreationPlan, SnapMirrorPolicyRuleWrite } from "@/api/types";

interface SnapMirrorPolicyFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
  initialClusterId?: string | null;
  onSubmitPlan: (plan: PolicyCreationPlan) => void;
}

export function SnapMirrorPolicyFormModal({ opened, onClose, clusters, svms, initialClusterId, onSubmitPlan }: SnapMirrorPolicyFormModalProps) {
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [vaultType, setVaultType] = useState<string | null>("vault");
  const [rules, setRules] = useState<SnapMirrorPolicyRuleWrite[]>([{ label: "", count: 7 }]);

  useEffect(() => {
    if (!opened) return;
    setClusterId(initialClusterId ?? clusters?.[0]?.id ?? null);
    setSvmName(null);
    setName("");
    setVaultType("vault");
    setRules([{ label: "", count: 7 }]);
  }, [opened, clusters, initialClusterId]);

  const svmOptions = (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name }));
  const validRules = rules.filter((r) => r.label.trim() && r.count > 0);
  const canSubmit = !!clusterId && !!svmName && !!name.trim() && validRules.length > 0;

  function handleSubmit() {
    if (!canSubmit || !clusterId || !svmName) return;
    onSubmitPlan({ clusterId, svmName, name, vaultType: (vaultType as "vault" | "mirror_vault") ?? "vault", rules: validRules });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neue SnapMirror-Policy" size="lg">
      <Stack>
        <Group grow>
          <Select
            label="Cluster"
            data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
            value={clusterId}
            onChange={(v) => { setClusterId(v); setSvmName(null); }}
            disabled={!!initialClusterId}
            required
          />
          <Select label="SVM" data={svmOptions} value={svmName} onChange={setSvmName} required searchable />
        </Group>
        <Group grow>
          <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required />
          <Select
            label="Typ"
            data={[
              { value: "vault", label: "Vault (nur Retention-Regeln)" },
              { value: "mirror_vault", label: "Mirror-Vault (Spiegelung + Retention-Regeln)" },
            ]}
            value={vaultType}
            onChange={setVaultType}
            required
          />
        </Group>
        <PolicyRulesEditor rules={rules} onChange={setRules} />
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
