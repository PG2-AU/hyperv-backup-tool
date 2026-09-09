import { Button, Modal, Stack, Text } from "@mantine/core";

import type { PolicySummary } from "@/api/types";

interface PolicyPickerModalProps {
  opened: boolean;
  onClose: () => void;
  policies: PolicySummary[];
  onPick: (policy: PolicySummary) => void;
  title?: string;
  description?: string;
}

export function PolicyPickerModal({
  opened,
  onClose,
  policies,
  onPick,
  title = "Policy auswählen",
  description = "Mehrere Backup-Policies zugeordnet — welche soll jetzt ausgeführt werden?",
}: PolicyPickerModalProps) {
  return (
    <Modal opened={opened} onClose={onClose} title={title}>
      <Stack>
        <Text size="sm" c="dimmed">
          {description}
        </Text>
        {policies.map((p) => (
          <Button
            key={p.id}
            variant="light"
            onClick={() => {
              onPick(p);
              onClose();
            }}
          >
            {p.name}
          </Button>
        ))}
      </Stack>
    </Modal>
  );
}
