import { useEffect, useState } from "react";
import { Button, Checkbox, Group, Modal, Stack, Text } from "@mantine/core";

import type { ResourceGroup } from "@/api/types";

interface ResourceGroupPickerModalProps {
  opened: boolean;
  onClose: () => void;
  policyName: string;
  groups: ResourceGroup[];
  onConfirm: (groupIds: string[]) => void;
  loading?: boolean;
}

/** Bei "Jetzt ausführen" auf einer Policy, die an mehrere Protection Groups
 * verknüpft ist: Auswahl, welche Gruppe(n) jetzt gesichert werden sollen,
 * statt zwangsläufig alle auf einmal. Alle Gruppen sind standardmäßig
 * ausgewählt, damit ein Klick auf "Ausführen" ohne weitere Interaktion das
 * bisherige Verhalten (ganze Policy) reproduziert. */
export function ResourceGroupPickerModal({ opened, onClose, policyName, groups, onConfirm, loading }: ResourceGroupPickerModalProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (opened) setSelected(new Set(groups.map((g) => g.id)));
  }, [opened, groups]);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const allSelected = selected.size === groups.length;

  return (
    <Modal opened={opened} onClose={onClose} title={`"${policyName}" jetzt ausführen`}>
      <Stack>
        <Text size="sm" c="dimmed">
          Diese Policy ist mit mehreren Protection Groups verknüpft — welche sollen jetzt gesichert werden?
        </Text>
        <Checkbox
          label="Alle auswählen"
          checked={allSelected}
          indeterminate={selected.size > 0 && !allSelected}
          onChange={() => setSelected(allSelected ? new Set() : new Set(groups.map((g) => g.id)))}
        />
        <Stack gap={4} pl="md">
          {groups.map((g) => (
            <Checkbox key={g.id} label={g.name} checked={selected.has(g.id)} onChange={() => toggle(g.id)} />
          ))}
        </Stack>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={() => onConfirm([...selected])} disabled={selected.size === 0} loading={loading}>
            Ausführen ({selected.size})
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
