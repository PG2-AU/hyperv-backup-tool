import { useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Loader, Modal, Radio, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { useStartVmMove, useVmMoveRun, useVmMoveTargets } from "@/api/hooks.vmMoves";
import type { Vm } from "@/api/types";
import { SiteBadgeView } from "@/components/SiteBadge";
import { apiErrorMessage } from "@/utils/errors";

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

interface VmMoveModalProps {
  opened: boolean;
  onClose: () => void;
  vm: Vm | null;
}

// VM verschieben, Stufe 1: Host-Move per Live-Migration innerhalb des
// Failover-Clusters. Zielknoten werden live abgefragt (Status + aktueller
// Owner), Standort-Badges helfen bei der Wahl eines Knotens am Standort
// des Storage (Settings > Standorte).
export function VmMoveModal({ opened, onClose, vm }: VmMoveModalProps) {
  const [targetNode, setTargetNode] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const { data: targets, isLoading, error } = useVmMoveTargets(vm?.cluster_id, vm?.name, opened && !runId);
  const startMove = useStartVmMove();
  const { data: run } = useVmMoveRun(runId);

  useEffect(() => {
    if (!opened) {
      setTargetNode(null);
      setRunId(undefined);
    }
  }, [opened]);

  const storageSiteIds = new Set((targets?.storage_sites ?? []).map((s) => s.id));
  const running = run?.status === "running";

  function handleStart() {
    if (!vm?.cluster_id || !targetNode) return;
    startMove
      .mutateAsync({ cluster_id: vm.cluster_id, vm_name: vm.name, target_node: targetNode })
      .then((started) => setRunId(started.id))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Verschieben konnte nicht gestartet werden."), color: "red" }),
      );
  }

  return (
    <Modal
      opened={opened}
      onClose={running ? () => undefined : onClose}
      withCloseButton={!running}
      title={`VM verschieben: ${vm?.name ?? ""}`}
      size="lg"
    >
      {!runId && (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Live-Migration auf einen anderen Knoten desselben Clusters. Eine laufende VM bleibt dabei erreichbar, der
            Speicherort (CSV) ändert sich nicht.
          </Text>
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">Cluster-Knoten werden abgefragt…</Text>
            </Group>
          )}
          {error && (
            <Alert color="red" icon={<IconX size={16} />}>
              {apiErrorMessage(error, "Cluster-Knoten konnten nicht abgefragt werden.")}
            </Alert>
          )}
          {targets && (
            <>
              <Group gap="xs">
                <Text size="sm">
                  Aktuell auf <b>{targets.current_node ?? "?"}</b>
                </Text>
                {targets.host_site && <SiteBadgeView site={targets.host_site} />}
                {targets.storage_sites.length > 0 && (
                  <>
                    <Text size="sm" c="dimmed">
                      · Storage in
                    </Text>
                    {targets.storage_sites.map((s) => (
                      <SiteBadgeView key={s.id} site={s} />
                    ))}
                  </>
                )}
              </Group>
              {targets.blocked_reason && (
                <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Verschieben derzeit nicht möglich">
                  {targets.blocked_reason}
                </Alert>
              )}
              <Radio.Group label="Zielknoten" value={targetNode} onChange={setTargetNode}>
                <Stack gap={6} mt={6}>
                  {targets.nodes.map((node) => {
                    const up = node.state === "Up";
                    const matchesStorage = !!node.site && storageSiteIds.has(node.site.id);
                    const mismatchesStorage = !!node.site && storageSiteIds.size > 0 && !matchesStorage;
                    return (
                      <Radio
                        key={node.name}
                        value={node.name}
                        disabled={!up || node.is_current || !!targets.blocked_reason}
                        label={
                          <Group gap={6} wrap="nowrap">
                            <Text size="sm">{node.name}</Text>
                            {node.site && <SiteBadgeView site={node.site} />}
                            {node.is_current && (
                              <Badge size="sm" variant="outline" color="gray">
                                aktuell
                              </Badge>
                            )}
                            {!up && (
                              <Badge size="sm" variant="light" color="red">
                                {node.state}
                              </Badge>
                            )}
                            {up && !node.is_current && matchesStorage && (
                              <Badge size="sm" variant="light" color="green">
                                gleicher Standort wie Storage
                              </Badge>
                            )}
                            {up && !node.is_current && mismatchesStorage && (
                              <Badge size="sm" variant="light" color="orange">
                                anderer Standort als Storage
                              </Badge>
                            )}
                            <Text size="xs" c="dimmed">
                              {node.vm_count} VMs
                            </Text>
                          </Group>
                        }
                      />
                    );
                  })}
                </Stack>
              </Radio.Group>
            </>
          )}
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Abbrechen
            </Button>
            <Button
              onClick={handleStart}
              loading={startMove.isPending}
              disabled={!targetNode || !targets || !!targets.blocked_reason}
            >
              Verschieben
            </Button>
          </Group>
        </Stack>
      )}

      {runId && (
        <Stack gap="sm">
          {!run && <Loader size="sm" />}
          {run?.steps.map((s) => (
            <Group key={s.step} gap="xs" wrap="nowrap" align="flex-start">
              {STEP_STATUS_ICON[s.status]}
              <Stack gap={0} style={{ flex: 1 }}>
                <Text size="sm" fw={600}>
                  {s.label}
                </Text>
                {s.message && s.message !== "OK" && (
                  <Text size="xs" c={s.status === "error" ? "red" : "dimmed"}>
                    {s.message}
                  </Text>
                )}
              </Stack>
            </Group>
          ))}
          {run?.status === "succeeded" && (
            <Alert icon={<IconCheck size={16} />} color="green" variant="light">
              VM läuft jetzt auf {run.target_node}.
            </Alert>
          )}
          {run?.status === "failed" && (
            <Alert icon={<IconX size={16} />} color="red" variant="light">
              {run.error_message}
            </Alert>
          )}
          {run && !running && (
            <Group justify="flex-end">
              <Button onClick={onClose}>Schließen</Button>
            </Group>
          )}
        </Stack>
      )}
    </Modal>
  );
}
