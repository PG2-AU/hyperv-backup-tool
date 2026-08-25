import { useEffect, useState } from "react";
import { Button, Group, Modal, PasswordInput, Stack, Switch, Text, TextInput } from "@mantine/core";

import type { HyperVClusterCreationPlan } from "@/api/types";

interface HyperVClusterFormModalProps {
  opened: boolean;
  onClose: () => void;
  onSubmitPlan: (plan: HyperVClusterCreationPlan) => void;
}

export function HyperVClusterFormModal({ opened, onClose, onSubmitPlan }: HyperVClusterFormModalProps) {
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [useHttps, setUseHttps] = useState(true);

  useEffect(() => {
    if (!opened) return;
    setName("");
    setAddress("");
    setUsername("");
    setPassword("");
    setUseHttps(true);
  }, [opened]);

  const canSubmit = !!name && !!address && !!username && !!password;

  function handleSubmit() {
    if (!canSubmit) return;
    onSubmitPlan({ name, managementAddress: address, username, password, useHttps });
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
        <Switch
          label="WinRM über HTTPS (Port 5986)"
          checked={useHttps}
          onChange={(e) => setUseHttps(e.currentTarget.checked)}
        />
        <Text size="xs" c="dimmed">
          Beim Hinzufügen wird zuerst die Netzwerk-Erreichbarkeit geprüft, danach die WinRM-Verbindung getestet
          (Get-Cluster / Get-ClusterNode). "Enable-PSRemoting -Force" richtet nur den HTTP-Listener (Port 5985) ein
          — für HTTPS ist zusätzlich ein an WinRM gebundenes Zertifikat auf den Hosts nötig. Ohne das kann HTTPS hier
          deaktiviert werden. Die IP-Adresse sollte auf das Cluster Name Object (den Failover-Cluster selbst)
          zeigen, nicht auf einen einzelnen Knoten.
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
