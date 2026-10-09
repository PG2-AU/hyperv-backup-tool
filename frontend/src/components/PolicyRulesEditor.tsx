import { ActionIcon, Autocomplete, Group, NumberInput, Select, Stack, Text } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";

import { useSnapMirrorLabels } from "@/api/hooks";
import type { SnapMirrorPolicyRuleWrite } from "@/api/types";
import { buildLockPeriod, parseLockPeriod, type LockPeriodUnit } from "@/utils/format";

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
        Regeln (SnapMirror-Label → Anzahl Snapshots, optional Sperrfrist)
      </Text>
      <Text size="xs" c="dimmed">
        Sperrfrist: so lange sind die übertragenen Snapshots am Ziel manipulationssicher gesperrt (Tamperproof Snapshot) – weder
        löschbar noch umbenennbar, auch nicht für Storage-Admins. Wirkt nur, wenn auf dem Ziel-Volume Snapshot-Locking eingeschaltet
        ist; die Frist hat Vorrang vor der Anzahl.
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
          {rule.period && !parseLockPeriod(rule.period) ? (
            // Frist in einer Form, die das Formular nicht abbildet (z.B. "infinite"): unveraendert beibehalten
            <Select
              label={i === 0 ? "Sperrfrist" : undefined}
              w={236}
              data={[
                { value: "keep", label: rule.period === "infinite" ? "unbegrenzt (beibehalten)" : `${rule.period} (beibehalten)` },
                { value: "none", label: "keine Sperre" },
              ]}
              value="keep"
              allowDeselect={false}
              onChange={(v) => v === "none" && updateRule(i, { period: null })}
            />
          ) : (
            <>
              <NumberInput
                label={i === 0 ? "Sperrfrist" : undefined}
                placeholder="keine"
                min={1}
                allowDecimal={false}
                value={parseLockPeriod(rule.period)?.value ?? ""}
                onChange={(v) =>
                  updateRule(i, {
                    period: typeof v === "number" && v > 0 ? buildLockPeriod(v, parseLockPeriod(rule.period)?.unit ?? "days") : null,
                  })
                }
                w={100}
              />
              <Select
                label={i === 0 ? "Einheit" : undefined}
                w={126}
                data={[
                  { value: "hours", label: "Stunden" },
                  { value: "days", label: "Tage" },
                  { value: "months", label: "Monate" },
                  { value: "years", label: "Jahre" },
                ]}
                value={parseLockPeriod(rule.period)?.unit ?? "days"}
                disabled={!rule.period}
                allowDeselect={false}
                onChange={(v) => {
                  const current = parseLockPeriod(rule.period);
                  if (v && current) updateRule(i, { period: buildLockPeriod(current.value, v as LockPeriodUnit) });
                }}
              />
            </>
          )}
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
