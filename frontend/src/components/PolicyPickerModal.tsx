import { Button, Modal, Stack, Text } from "@mantine/core";

import type { PolicySummary } from "@/api/types";

interface PolicyPickerModalProps {
  opened: boolean;
  onClose: () => void;
  policies: PolicySummary[];
  onPick: (policy: PolicySummary) => void;
}

export function PolicyPickerModal({ opened, onClose, policies, onPick }: PolicyPickerModalProps) {
  return (
    <Modal opened={opened} onClose={onClose} title="Policy auswählen">
      <Stack>
        <Text size="sm" c="dimmed">
          Mehrere Backup-Policies zugeordnet — welche soll jetzt ausgeführt werden?
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
