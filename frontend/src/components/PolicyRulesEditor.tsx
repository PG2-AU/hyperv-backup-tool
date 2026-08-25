import { ActionIcon, Autocomplete, Group, NumberInput, Stack, Text } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";

import { useSnapMirrorLabels } from "@/api/hooks";
import type { SnapMirrorPolicyRuleWrite } from "@/api/types";

interface PolicyRulesEditorProps {
  rules: SnapMirrorPolicyRuleWrite[];
  onChange: (rules: SnapMirrorPolicyRuleWrite[]) => void;
}

export function PolicyRulesEditor({ rules, onChange }: PolicyRulesEditorProps) {
  const { data: labels } = useSnapMirrorLabels();
  const labelOptions = (labels ?? []).map((l) => l.name);

  function updateRule(index: number, patch: Partial<SnapMirrorPolicyRuleWrite>) {
    onChange(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  }

  function removeRule(index: number) {
    onChange(rules.filter((_, i) => i !== index));
  }

  function addRule() {
    onChange([...rules, { label: "", count: 7 }]);
  }

  return (
    <Stack gap="xs">
      <Text size="sm" fw={600}>
        Regeln (SnapMirror-Label → Anzahl Snapshots)
      </Text>
      {rules.map((rule, i) => (
        <Group key={i} wrap="nowrap" align="flex-end">
          <Autocomplete
            label={i === 0 ? "SnapMirror-Label" : undefined}
            placeholder="z.B. daily"
            data={labelOptions}
            value={rule.label}
            onChange={(v) => updateRule(i, { label: v })}
            style={{ flex: 1 }}
          />
          <NumberInput
            label={i === 0 ? "Anzahl" : undefined}
            min={1}
            value={rule.count}
            onChange={(v) => updateRule(i, { count: Number(v) || 1 })}
            w={100}
          />
          <ActionIcon color="red" variant="subtle" onClick={() => removeRule(i)} mb={2}>
            <IconTrash size={16} />
          </ActionIcon>
        </Group>
      ))}
      <Group>
        <ActionIcon variant="light" onClick={addRule}>
          <IconPlus size={16} />
        </ActionIcon>
        <Text size="xs" c="dimmed">
          Regel hinzufügen
        </Text>
      </Group>
    </Stack>
  );
}
