import { useEffect, useState } from "react";
import { Button, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { IconCheck, IconMinus, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import { DISCOVERY_QUERY_KEYS } from "@/api/hooks";
import type { LunCreationPlan } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

type StepStatus = "pending" | "running" | "success" | "error" | "skipped";

interface StepDef {
  id: string;
  emoji: string;
  label: string;
}

interface StepState extends StepDef {
  status: StepStatus;
  message?: string;
}

function buildSteps(plan: LunCreationPlan): StepDef[] {
  const steps: StepDef[] = [];
  if (plan.volumeMode === "new") {
    steps.push({ id: "volume", emoji: "🗂️", label: `Lege Volume '${plan.volumeName}' an` });
  }
  steps.push({ id: "lun", emoji: "🧱", label: `Lege LUN '${plan.lunName}' an` });
  if (plan.igroupMode === "new" && plan.newIgroup) {
    steps.push({ id: "igroup", emoji: "🎯", label: `Lege Initiator-Gruppe '${plan.newIgroup.name}' an` });
  }
  if (plan.igroupMode !== "none") {
    steps.push({ id: "mapping", emoji: "🔌", label: "Verknüpfe LUN mit IGroup" });
  }
  steps.push({ id: "discover", emoji: "🔄", label: "Aktualisiere Übersicht" });
  return steps;
}

async function runStep(step: StepDef, plan: LunCreationPlan): Promise<void> {
  const base = `/netapp/clusters/${plan.clusterId}`;
  if (step.id === "volume") {
    await apiClient.post(`${base}/volumes`, {
      svm_name: plan.svmName,
      name: plan.volumeName,
      aggregate_name: plan.newVolumeAggregate,
      size_bytes: plan.newVolumeSizeBytes,
    });
  } else if (step.id === "lun") {
    await apiClient.post(`${base}/luns`, {
      svm_name: plan.svmName,
      lun_name: plan.lunName,
      os_type: plan.osType,
      size_bytes: plan.lunSizeBytes,
      volume_name: plan.volumeName,
    });
  } else if (step.id === "igroup" && plan.newIgroup) {
    await apiClient.post(`${base}/igroups`, {
      svm_name: plan.svmName,
      name: plan.newIgroup.name,
      os_type: plan.newIgroup.osType,
      protocol: plan.newIgroup.protocol,
      initiators: plan.newIgroup.initiators,
    });
  } else if (step.id === "mapping") {
    const igroupName = plan.igroupMode === "new" ? plan.newIgroup?.name : plan.igroupName;
    await apiClient.post(`${base}/lun-maps`, {
      svm_name: plan.svmName,
      lun_name: `/vol/${plan.volumeName}/${plan.lunName}`,
      igroup_name: igroupName,
    });
  } else if (step.id === "discover") {
    await apiClient.post(`${base}/discover`);
  }
}

const STATUS_ICON: Record<StepStatus, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

export function LunCreationModal({ opened, onClose, plan }: { opened: boolean; onClose: () => void; plan: LunCreationPlan | null }) {
  const queryClient = useQueryClient();
  const [steps, setSteps] = useState<StepState[]>([]);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!opened || !plan) return;
    const stepDefs = buildSteps(plan);
    setSteps(stepDefs.map((s) => ({ ...s, status: "pending" })));
    setRunning(true);
    let cancelled = false;

    function setStatus(id: string, status: StepStatus, message?: string) {
      if (cancelled) return;
      setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, status, message } : s)));
    }

    (async () => {
      let failed = false;
      for (const step of stepDefs) {
        if (cancelled) return;
        if (failed && step.id !== "discover") {
          setStatus(step.id, "skipped");
          continue;
        }
        setStatus(step.id, "running");
        try {
          await runStep(step, plan);
          setStatus(step.id, "success");
        } catch (err) {
          setStatus(step.id, "error", apiErrorMessage(err, "Unbekannter Fehler."));
          failed = true;
        }
      }
      if (!cancelled) {
        setRunning(false);
        DISCOVERY_QUERY_KEYS.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] }));
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [opened, plan, queryClient]);

  const done = !running && steps.length > 0;
  const hasError = steps.some((s) => s.status === "error");

  return (
    <Modal opened={opened} onClose={done ? onClose : () => {}} closeOnClickOutside={done} closeOnEscape={done} withCloseButton={done} title="LUN anlegen">
      <Stack gap="sm">
        {steps.map((step) => (
          <Group key={step.id} gap="xs" wrap="nowrap" align="flex-start">
            <Text size="lg" style={{ lineHeight: 1 }}>
              {step.emoji}
            </Text>
            <Stack gap={0} style={{ flex: 1, minWidth: 0 }}>
              <Text size="sm" fw={600}>
                {step.label}
              </Text>
              {step.status === "error" && (
                <Text size="xs" c="red" truncate="end">
                  {step.message}
                </Text>
              )}
              {step.status === "skipped" && (
                <Text size="xs" c="dimmed">
                  Übersprungen (vorheriger Schritt fehlgeschlagen)
                </Text>
              )}
            </Stack>
            {STATUS_ICON[step.status]}
          </Group>
        ))}

        {done && (
          <Group justify="flex-end" mt="sm">
            <Button onClick={onClose} color={hasError ? "red" : undefined}>
              {hasError ? "Schließen" : "Fertig"}
            </Button>
          </Group>
        )}
      </Stack>
    </Modal>
  );
}
