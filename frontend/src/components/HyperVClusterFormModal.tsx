import { useState } from "react";
import { Button, Group, Modal, PasswordInput, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateHyperVCluster } from "@/api/hooks";
import type { HyperVCluster } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface HyperVClusterFormModalProps {
  opened: boolean;
  onClose: () => void;
  onCreated?: (cluster: HyperVCluster) => void;
}

export function HyperVClusterFormModal({ opened, onClose, onCreated }: HyperVClusterFormModalProps) {
  const createCluster = useCreateHyperVCluster();
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  function reset() {
    setName("");
    setAddress("");
    setUsername("");
    setPassword("");
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    createCluster.mutate(
      { name, management_address: address, username, password },
      {
        onSuccess: (cluster) => {
          notifications.show({
            title: "Hyper-V-Cluster hinzugefügt",
            message: `'${cluster.name}' verbunden (${cluster.node_count} Knoten).`,
            color: "green",
          });
          handleClose();
          onCreated?.(cluster);
        },
        onError: (err) => {
          notifications.show({
            title: "Verbindung fehlgeschlagen",
            message: apiErrorMessage(err, "Cluster konnte nicht hinzugefügt werden."),
            color: "red",
          });
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={handleClose} title="Hyper-V-Cluster hinzufügen">
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
          Beim Hinzufügen wird die Verbindung per WinRM sofort getestet (Get-Cluster / Get-ClusterNode). Die
          IP-Adresse sollte auf das Cluster Name Object (den Failover-Cluster selbst) zeigen, nicht auf einen
          einzelnen Knoten.
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createCluster.isPending} disabled={!name || !address || !username || !password}>
            Verbinden & hinzufügen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
