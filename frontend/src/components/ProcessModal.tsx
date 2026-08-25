import { useEffect, useState } from "react";
import { Button, Group, Loader, Modal, Stack, Text } from "@mantine/core";
import { IconCheck, IconMinus, IconX } from "@tabler/icons-react";

import { apiErrorMessage } from "@/utils/errors";

export type StepStatus = "pending" | "running" | "success" | "error" | "skipped";

export interface ProcessStepDef {
  id: string;
  emoji: string;
  label: string;
  run: () => Promise<void>;
  /** Runs even if an earlier step failed (e.g. a final "refresh" step). */
  alwaysRun?: boolean;
}

interface ProcessStepState {
  id: string;
  emoji: string;
  label: string;
  status: StepStatus;
  message?: string;
}

const STATUS_ICON: Record<StepStatus, React.ReactNode> = {
  pending: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
  running: <Loader size="xs" />,
  success: <IconCheck size={16} color="var(--mantine-color-green-6)" />,
  error: <IconX size={16} color="var(--mantine-color-red-6)" />,
  skipped: <IconMinus size={16} color="var(--mantine-color-gray-5)" />,
};

export interface ProcessPlan {
  title: string;
  steps: ProcessStepDef[];
}

export function ProcessModal({ opened, onClose, plan }: { opened: boolean; onClose: () => void; plan: ProcessPlan | null }) {
  const [stepStates, setStepStates] = useState<ProcessStepState[]>([]);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!opened || !plan) return;
    const stepDefs = plan.steps;
    setStepStates(stepDefs.map(({ id, emoji, label }) => ({ id, emoji, label, status: "pending" as StepStatus })));
    setRunning(true);
    let cancelled = false;

    function setStatus(id: string, status: StepStatus, message?: string) {
      if (cancelled) return;
      setStepStates((prev) => prev.map((s) => (s.id === id ? { ...s, status, message } : s)));
    }

    (async () => {
      let failed = false;
      for (const step of stepDefs) {
        if (cancelled) return;
        if (failed && !step.alwaysRun) {
          setStatus(step.id, "skipped");
          continue;
        }
        setStatus(step.id, "running");
        try {
          await step.run();
          setStatus(step.id, "success");
        } catch (err) {
          setStatus(step.id, "error", apiErrorMessage(err, "Unbekannter Fehler."));
          failed = true;
        }
      }
      if (!cancelled) setRunning(false);
    })();

    return () => {
      cancelled = true;
    };
  }, [opened, plan]);

  const done = !running && stepStates.length > 0;
  const hasError = stepStates.some((s) => s.status === "error");

  return (
    <Modal
      opened={opened}
      onClose={done ? onClose : () => {}}
      closeOnClickOutside={done}
      closeOnEscape={done}
      withCloseButton={done}
      title={plan?.title ?? ""}
    >
      <Stack gap="sm">
        {stepStates.map((step) => (
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
