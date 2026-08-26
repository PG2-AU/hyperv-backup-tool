import { useState } from "react";
import { notifications } from "@mantine/notifications";

import { useTriggerJobRun } from "@/api/hooks";
import type { PolicySummary } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

/** Startet eine Backup-Policy direkt, wenn genau eine zur Auswahl steht;
 * bei mehreren wird ein Auswahl-Dialog geoeffnet (siehe PolicyPickerModal). */
export function useRunPolicy() {
  const triggerRun = useTriggerJobRun();
  const [pickerPolicies, setPickerPolicies] = useState<PolicySummary[] | null>(null);

  function runPolicy(policy: PolicySummary) {
    triggerRun.mutate(policy.id, {
      onSuccess: (run) =>
        notifications.show({
          title: run.status === "succeeded" ? "Job erfolgreich" : "Job fehlgeschlagen",
          message: run.error_message ?? policy.name,
          color: run.status === "succeeded" ? "green" : "red",
        }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Job konnte nicht gestartet werden."), color: "red" }),
    });
  }

  function runOrPick(policies: PolicySummary[]) {
    if (policies.length === 0) {
      notifications.show({
        title: "Keine Policy",
        message: "Dieses Objekt ist keiner Backup-Policy zugeordnet.",
        color: "red",
      });
      return;
    }
    if (policies.length === 1) {
      runPolicy(policies[0]);
      return;
    }
    setPickerPolicies(policies);
  }

  return {
    runOrPick,
    runPolicy,
    pickerPolicies,
    closePicker: () => setPickerPolicies(null),
  };
}
