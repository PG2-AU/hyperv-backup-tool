import { useEffect, useState } from "react";
import { Button, Group, Modal, PasswordInput, Stack, Text, TextInput } from "@mantine/core";

import { usePublicSettings } from "@/api/hooks.settings";
import type { HyperVClusterCreationPlan } from "@/api/types";

interface HyperVClusterFormModalProps {
  opened: boolean;
  onClose: () => void;
  onSubmitPlan: (plan: HyperVClusterCreationPlan) => void;
}

export function HyperVClusterFormModal({ opened, onClose, onSubmitPlan }: HyperVClusterFormModalProps) {
  const { data: settings } = usePublicSettings();
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (!opened) return;
    setName("");
    setAddress("");
    setUsername("");
    setPassword("");
  }, [opened]);

  const canSubmit = !!name && !!address && !!username && !!password;

  function handleSubmit() {
    if (!canSubmit) return;
    onSubmitPlan({ name, managementAddress: address, username, password, winrmPort: settings?.winrm_port ?? 5986 });
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Hyper-V-Cluster hinzufügen">
      <Stack>
        <TextInput label="Name" placeholder="z.B. HVCLUSTER01" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput
          label="IP-Adresse"
          placeholder="z.B. 10.0.0.20"
          required
          value={address}
          onChange={(e) => setAddress(e.currentTarget.value)}
        />
        <TextInput label="Benutzername" required value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
        <PasswordInput label="Kennwort" required value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
        <Text size="xs" c="dimmed">
          Beim Hinzufügen wird zuerst die Netzwerk-Erreichbarkeit geprüft, danach die WinRM-Verbindung getestet
          (Get-Cluster / Get-ClusterNode). Die IP-Adresse sollte auf das Cluster Name Object (den Failover-Cluster
          selbst) zeigen, nicht auf einen einzelnen Knoten.
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            Verbinden & hinzufügen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
