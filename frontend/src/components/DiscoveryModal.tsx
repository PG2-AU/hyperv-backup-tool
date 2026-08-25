import { useEffect, useState } from "react";
import { Button, Group, Loader, Modal, Progress, Stack, Text } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";

import type { DiscoveryStep } from "@/api/types";

const STEP_META: Record<string, { emoji: string; label: string }> = {
  login: { emoji: "🕵️", label: "Prüfe Cluster-Login" },
  svms: { emoji: "🏠", label: "Sammle Informationen über SVMs" },
  volumes: { emoji: "💾", label: "Sammle Informationen über Volumes" },
  luns: { emoji: "🧱", label: "Informationen über LUNs" },
  igroups: { emoji: "🎯", label: "Sammle Initiator-Gruppen" },
  lun_maps: { emoji: "🔌", label: "Ordne LUN-Mappings zu" },
  cluster_peers: { emoji: "🤝", label: "Cluster-Peer-Beziehungen" },
  svm_peers: { emoji: "🔗", label: "SVM-Beziehungen" },
  snapmirror: { emoji: "🪞", label: "SnapMirror-Beziehungen" },
  network_interfaces: { emoji: "🌐", label: "Network Interfaces" },
  platforms: { emoji: "🖥️", label: "Sammle Plattform-Informationen" },
  aggregates: { emoji: "🪨", label: "Sammle Informationen über Aggregate" },
};

const REVEAL_DELAY_MS = 550;

interface DiscoveryModalProps {
  opened: boolean;
  onClose: () => void;
  clusterName?: string;
  steps: DiscoveryStep[] | undefined;
  isLoading: boolean;
}

export function DiscoveryModal({ opened, onClose, clusterName, steps, isLoading }: DiscoveryModalProps) {
  const [revealedCount, setRevealedCount] = useState(0);

  useEffect(() => {
    if (!opened || !steps || steps.length === 0) {
      setRevealedCount(0);
      return;
    }
    setRevealedCount(0);
    let i = 0;
    const timer = setInterval(() => {
      i += 1;
      setRevealedCount(i);
      if (i >= steps.length) clearInterval(timer);
    }, REVEAL_DELAY_MS);
    return () => clearInterval(timer);
  }, [opened, steps]);

  const done = !isLoading && !!steps && revealedCount >= steps.length;

  return (
    <Modal opened={opened} onClose={onClose} title={`Cluster-Discovery${clusterName ? `: ${clusterName}` : ""}`} closeOnClickOutside={done}>
      <Stack gap="sm">
        {isLoading && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm" c="dimmed">
              Verbinde mit dem Cluster und schaue mich um...
            </Text>
          </Group>
        )}

        {steps?.slice(0, revealedCount).map((s, i) => {
          const meta = STEP_META[s.step] ?? { emoji: "🔍", label: s.step };
          return (
            <Group key={i} gap="xs" wrap="nowrap" align="flex-start">
              <Text size="lg" style={{ lineHeight: 1 }}>
                {meta.emoji}
              </Text>
              <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
                <Text size="sm" fw={600}>
                  {meta.label}
                </Text>
                <Text size="xs" c={s.success ? "dimmed" : "red"} truncate="end">
                  {s.message}
                </Text>
              </Stack>
              {s.success ? <IconCheck size={16} color="var(--mantine-color-green-6)" /> : <IconX size={16} color="var(--mantine-color-red-6)" />}
            </Group>
          );
        })}

        {!isLoading && steps && revealedCount < steps.length && (
          <>
            <Progress value={(revealedCount / steps.length) * 100} size="sm" animated />
            <Text size="xs" c="dimmed">
              Werte Ergebnisse aus...
            </Text>
          </>
        )}

        {done && (
          <Group justify="flex-end" mt="sm">
            <Button onClick={onClose}>Fertig</Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
