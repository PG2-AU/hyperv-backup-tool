import { useState } from "react";
import { notifications } from "@mantine/notifications";

import { useResourceGroups, useTriggerJobRun } from "@/api/hooks";
import type { PolicySummary } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

/** Startet eine Backup-Policy direkt, wenn genau eine zur Auswahl steht;
 * bei mehreren wird ein Auswahl-Dialog geoeffnet (siehe PolicyPickerModal). */
export function useRunPolicy() {
  const triggerRun = useTriggerJobRun();
  const { data: groups } = useResourceGroups();
  const [pickerPolicies, setPickerPolicies] = useState<PolicySummary[] | null>(null);

  function runPolicy(policy: PolicySummary) {
    // Ist die Policy an GENAU EINE Resource Group verknuepft, wird der Lauf
    // explizit dieser Gruppe zugeordnet, statt implizit "ganze Policy" mit
    // resource_group_id=NULL zu laufen -- sonst zeigte der Job-Verlauf
    // faelschlich "Alle Gruppen", obwohl die Zuordnung nie mehrdeutig war
    // (Nutzer-Meldung, Live-Screenshot: Policy 'Bronze' mit nur einer
    // verknuepften Gruppe). Bei 0 verknuepften Gruppen (laesst das Backend
    // mit "keine Ziele" ablehnen) oder mehreren (kommt hier nur aus
    // Kontexten an, die die Gruppe nicht mehr eindeutig kennen -- der
    // Mehrfachauswahl-Dialog in JobsPage.tsx faengt den Policies-Tab-Fall
    // bereits VORHER ab) bleibt es beim bisherigen Verhalten.
    const linkedGroupIds = (groups ?? []).filter((g) => g.policies.some((p) => p.id === policy.id)).map((g) => g.id);
    const resourceGroupId = linkedGroupIds.length === 1 ? linkedGroupIds[0] : undefined;

    triggerRun.mutate({ jobId: policy.id, resourceGroupId }, {
      // Der Job laeuft jetzt im Hintergrund weiter (siehe RunningJobsIndicator
      // in der Kopfzeile fuer den Live-Fortschritt) -- die Antwort hier
      // bedeutet nur "gestartet", nicht mehr "fertig" wie frueher, als der
      // Request bis zum kompletten Abschluss blockierte.
      onSuccess: () =>
        notifications.show({
          title: "Job gestartet",
          message: `${policy.name} läuft – Fortschritt siehe Kopfzeile.`,
          color: "blue",
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
