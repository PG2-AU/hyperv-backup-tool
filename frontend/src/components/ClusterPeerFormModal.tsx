import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateClusterPeer } from "@/api/hooks";
import type { NetAppCluster } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface ClusterPeerFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
}

export function ClusterPeerFormModal({ opened, onClose, clusters }: ClusterPeerFormModalProps) {
  const createPeer = useCreateClusterPeer();
  const [clusterAId, setClusterAId] = useState<string | null>(null);
  const [clusterBId, setClusterBId] = useState<string | null>(null);

  useEffect(() => {
    if (!opened) return;
    setClusterAId(clusters?.[0]?.id ?? null);
    setClusterBId(null);
  }, [opened, clusters]);

  const optionsA = (clusters ?? []).map((c) => ({ value: c.id, label: c.name }));
  const optionsB = (clusters ?? []).filter((c) => c.id !== clusterAId).map((c) => ({ value: c.id, label: c.name }));

  function handleSubmit() {
    if (!clusterAId || !clusterBId) return;
    createPeer.mutate(
      { clusterId: clusterAId, payload: { peer_cluster_id: clusterBId } },
      {
        onSuccess: () => {
          notifications.show({ title: "Cluster-Peering hergestellt", message: "Beide Cluster wurden neu discovert.", color: "green" });
          onClose();
        },
        onError: (err) =>
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Cluster-Peering fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Cluster-Peer-Beziehung erstellen">
      <Stack>
        <Text size="xs" c="dimmed">
          Beide Cluster müssen bereits in dieser App registriert sein: Auf Cluster A wird eine Peering-Passphrase erzeugt, Cluster B nimmt sie
          zusammen mit den Intercluster-Adressen von A automatisch an.
        </Text>
        <Select label="Cluster A" data={optionsA} value={clusterAId} onChange={(v) => { setClusterAId(v); if (v === clusterBId) setClusterBId(null); }} required />
        <Select label="Cluster B" data={optionsB} value={clusterBId} onChange={setClusterBId} required disabled={!clusterAId} />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createPeer.isPending} disabled={!clusterAId || !clusterBId}>
            Peering herstellen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
