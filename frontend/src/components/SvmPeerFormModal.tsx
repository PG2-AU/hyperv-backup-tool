import { useEffect, useState } from "react";
import { Button, Group, Modal, MultiSelect, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateSvmPeer } from "@/api/hooks";
import type { NetAppCluster, NetAppSvm } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface SvmPeerFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
}

const APPLICATION_OPTIONS = ["snapmirror", "lun_copy", "file_copy", "sync_migration"];

export function SvmPeerFormModal({ opened, onClose, clusters, svms }: SvmPeerFormModalProps) {
  const createSvmPeer = useCreateSvmPeer();
  const [clusterAId, setClusterAId] = useState<string | null>(null);
  const [svmAName, setSvmAName] = useState<string | null>(null);
  const [clusterBId, setClusterBId] = useState<string | null>(null);
  const [svmBName, setSvmBName] = useState<string | null>(null);
  const [applications, setApplications] = useState<string[]>(["snapmirror"]);

  useEffect(() => {
    if (!opened) return;
    setClusterAId(clusters?.[0]?.id ?? null);
    setSvmAName(null);
    setClusterBId(null);
    setSvmBName(null);
    setApplications(["snapmirror"]);
  }, [opened, clusters]);

  const optionsA = (clusters ?? []).map((c) => ({ value: c.id, label: c.name }));
  const optionsB = (clusters ?? []).filter((c) => c.id !== clusterAId).map((c) => ({ value: c.id, label: c.name }));
  const svmAOptions = (svms ?? []).filter((s) => s.cluster_id === clusterAId).map((s) => ({ value: s.name, label: s.name }));
  const svmBOptions = (svms ?? []).filter((s) => s.cluster_id === clusterBId).map((s) => ({ value: s.name, label: s.name }));

  function handleSubmit() {
    if (!clusterAId || !svmAName || !clusterBId || !svmBName) return;
    createSvmPeer.mutate(
      { clusterId: clusterAId, payload: { local_svm_name: svmAName, peer_cluster_id: clusterBId, peer_svm_name: svmBName, applications } },
      {
        onSuccess: () => {
          notifications.show({ title: "SVM-Peering hergestellt", message: `${svmAName} <-> ${svmBName}`, color: "green" });
          onClose();
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "SVM-Peering fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="SVM-Peer-Beziehung erstellen">
      <Stack>
        <Text size="xs" c="dimmed">
          Voraussetzung: Cluster A und Cluster B sind bereits per Cluster-Peering verbunden. Die Anfrage wird auf Cluster B automatisch
          angenommen.
        </Text>
        <Select
          label="Cluster A"
          data={optionsA}
          value={clusterAId}
          onChange={(v) => { setClusterAId(v); setSvmAName(null); if (v === clusterBId) setClusterBId(null); }}
          required
        />
        <Select label="SVM auf Cluster A" data={svmAOptions} value={svmAName} onChange={setSvmAName} required disabled={!clusterAId} searchable />
        <Select
          label="Cluster B (Peer)"
          data={optionsB}
          value={clusterBId}
          onChange={(v) => { setClusterBId(v); setSvmBName(null); }}
          required
          disabled={!clusterAId}
        />
        <Select label="SVM auf Cluster B" data={svmBOptions} value={svmBName} onChange={setSvmBName} required disabled={!clusterBId} searchable />
        <MultiSelect label="Applications" data={APPLICATION_OPTIONS} value={applications} onChange={setApplications} />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createSvmPeer.isPending} disabled={!clusterAId || !svmAName || !clusterBId || !svmBName}>
            Peering herstellen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
