import { useEffect, useState } from "react";
import { Button, Group, Modal, PasswordInput, Stack, Switch, Text, TextInput } from "@mantine/core";

import type { HyperVCluster, HyperVClusterCreationPlan } from "@/api/types";

interface HyperVClusterFormModalProps {
  opened: boolean;
  onClose: () => void;
  onSubmitPlan: (plan: HyperVClusterCreationPlan) => void;
  // Gesetzt = Bearbeiten-Modus (Name/Adresse/Zugangsdaten eines bereits
  // registrierten Clusters aendern, z.B. fuer eine Passwort-Rotation) statt
  // einen neuen Cluster anzulegen.
  cluster?: HyperVCluster | null;
}

export function HyperVClusterFormModal({ opened, onClose, onSubmitPlan, cluster }: HyperVClusterFormModalProps) {
  const isEdit = !!cluster;
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [useHttps, setUseHttps] = useState(true);

  useEffect(() => {
    if (!opened) return;
    setName(cluster?.name ?? "");
    setAddress(cluster?.management_address ?? "");
    setUsername(cluster?.username ?? "");
    // Passwort bleibt beim Bearbeiten bewusst leer -- ein bestehendes
    // Passwort wird nie zum Anzeigen entschluesselt, siehe Hinweistext unten.
    setPassword("");
    setUseHttps(cluster?.use_https ?? true);
  }, [opened, cluster]);

  const canSubmit = !!name && !!address && !!username && (isEdit || !!password);

  function handleSubmit() {
    if (!canSubmit) return;
    onSubmitPlan({ name, managementAddress: address, username, password, useHttps });
  }

  return (
    <Modal opened={opened} onClose={onClose} title={isEdit ? `Hyper-V-Cluster bearbeiten: ${cluster!.name}` : "Hyper-V-Cluster hinzufügen"}>
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
        <PasswordInput
          label="Kennwort"
          placeholder={isEdit ? "Leer lassen, um das bestehende Kennwort beizubehalten" : undefined}
          required={!isEdit}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Switch
          label="WinRM über HTTPS (Port 5986)"
          checked={useHttps}
          onChange={(e) => setUseHttps(e.currentTarget.checked)}
        />
        <Text size="xs" c="dimmed">
          {isEdit
            ? "Die Verbindung wird mit den neuen Angaben sofort getestet, bevor sie gespeichert werden. Leeres Kennwort behält das bisherige bei."
            : "Beim Hinzufügen wird zuerst die Netzwerk-Erreichbarkeit geprüft, danach die WinRM-Verbindung getestet " +
              "(Get-Cluster / Get-ClusterNode). \"Enable-PSRemoting -Force\" richtet nur den HTTP-Listener (Port 5985) ein " +
              "— für HTTPS ist zusätzlich ein an WinRM gebundenes Zertifikat auf den Hosts nötig. Ohne das kann HTTPS hier " +
              "deaktiviert werden. Die IP-Adresse sollte auf das Cluster Name Object (den Failover-Cluster selbst) " +
              "zeigen, nicht auf einen einzelnen Knoten."}
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {isEdit ? "Speichern" : "Verbinden & hinzufügen"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
