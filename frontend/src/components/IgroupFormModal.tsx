import { useEffect, useState } from "react";
import { Button, Group, Modal, Select, Stack, TagsInput, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useCreateIgroup, useDiscoverNetAppCluster } from "@/api/hooks";
import { IGROUP_OS_TYPES } from "@/api/types";
import type { NetAppCluster, NetAppSvm } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface IgroupFormModalProps {
  opened: boolean;
  onClose: () => void;
  clusters: NetAppCluster[] | undefined;
  svms: NetAppSvm[] | undefined;
}

export function IgroupFormModal({ opened, onClose, clusters, svms }: IgroupFormModalProps) {
  const createIgroup = useCreateIgroup();
  const discoverCluster = useDiscoverNetAppCluster();
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [osType, setOsType] = useState<string | null>("linux");
  const [protocol, setProtocol] = useState<string | null>("mixed");
  const [initiators, setInitiators] = useState<string[]>([]);

  const svmOptions = (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name }));

  useEffect(() => {
    if (!opened) return;
    setClusterId(clusters?.[0]?.id ?? null);
    setSvmName(null);
    setName("");
    setOsType("linux");
    setProtocol("mixed");
    setInitiators([]);
  }, [opened, clusters]);

  function handleSubmit() {
    if (!clusterId || !svmName || !name || !osType) return;
    createIgroup.mutate(
      {
        clusterId,
        payload: { svm_name: svmName, name, os_type: osType, protocol: (protocol as "fcp" | "iscsi" | "mixed") ?? "mixed", initiators },
      },
      {
        onSuccess: () => {
          notifications.show({ title: "Initiator-Gruppe angelegt", message: name, color: "green" });
          onClose();
          discoverCluster.mutate(clusterId);
        },
        onError: (err) =>
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Initiator-Gruppe konnte nicht angelegt werden."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Neue Initiator-Gruppe (IGroup)">
      <Stack>
        <Select
          label="Cluster"
          data={(clusters ?? []).map((c) => ({ value: c.id, label: c.name }))}
          value={clusterId}
          onChange={(v) => {
            setClusterId(v);
            setSvmName(null);
          }}
          required
        />
        <Select label="SVM" data={svmOptions} value={svmName} onChange={setSvmName} required searchable />
        <TextInput label="Name" placeholder="z.B. esx01_igroup" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Select label="OS-Type" data={[...IGROUP_OS_TYPES]} value={osType} onChange={setOsType} required />
        <Select label="Protocol" data={["fcp", "iscsi", "mixed"]} value={protocol} onChange={setProtocol} />
        <TagsInput
          label="Initiatoren (optional)"
          placeholder="IQN oder WWPN eingeben und Enter drücken"
          value={initiators}
          onChange={setInitiators}
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createIgroup.isPending} disabled={!clusterId || !svmName || !name || !osType}>
            Anlegen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
