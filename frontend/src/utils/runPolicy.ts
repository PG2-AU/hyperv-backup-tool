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
  // Traegt bei einem offenen Auswahl-Dialog zusaetzlich die Resource-Group-
  // ID mit, von der aus der Dialog geoeffnet wurde (falls bekannt) --
  // BUG (live gefunden 2026-09-09): runGroupNow() rief bislang ueber
  // runOrPick() OHNE Gruppen-Kontext direkt runPolicy() auf. runPolicy()
  // leitete die betroffene Gruppe daraufhin selbst aus der Policy-
  // Verknuepfung ab -- war die Policy an MEHRERE Gruppen gehaengt (z.B.
  // 'Silver_hourly' an 'Silver_CSV01' UND 'Silver_CSV02'), war das Ergebnis
  // nicht mehr eindeutig (linkedGroupIds.length !== 1), der Lauf ging
  // dadurch faelschlich fuer ALLE verknuepften Gruppen los, obwohl der
  // Nutzer "Jetzt ausfuehren" gezielt nur bei EINER Gruppe geklickt hatte.
  const [picker, setPicker] = useState<{ policies: PolicySummary[]; resourceGroupId?: string } | null>(null);

  function runPolicy(policy: PolicySummary, explicitResourceGroupId?: string) {
    let resourceGroupId = explicitResourceGroupId;
    if (resourceGroupId === undefined) {
      // Ist die Policy an GENAU EINE Resource Group verknuepft, wird der Lauf
      // explizit dieser Gruppe zugeordnet, statt implizit "ganze Policy" mit
      // resource_group_id=NULL zu laufen -- sonst zeigte der Job-Verlauf
      // faelschlich "Alle Gruppen", obwohl die Zuordnung nie mehrdeutig war
      // (Nutzer-Meldung, Live-Screenshot: Policy 'Bronze' mit nur einer
      // verknuepften Gruppe). Nur relevant, wenn der Aufrufer selbst KEINE
      // Gruppe kennt (Policies-Tab) -- kennt er sie (Protection-Groups-Tab,
      // "Jetzt nachholen"), wird explicitResourceGroupId oben direkt
      // uebernommen, diese Ableitung also gar nicht erst versucht.
      const linkedGroupIds = (groups ?? []).filter((g) => g.policies.some((p) => p.id === policy.id)).map((g) => g.id);
      resourceGroupId = linkedGroupIds.length === 1 ? linkedGroupIds[0] : undefined;
    }

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

  /** resourceGroupId (optional): schraenkt einen eventuell noetigen
   * Policy-Auswahl-Dialog (mehrere Policies auf DERSELBEN, schon bekannten
   * Gruppe) auf genau diese Gruppe ein -- wird vom Aufrufer mitgegeben,
   * wenn er (anders als im Policies-Tab) bereits eine konkrete Resource
   * Group meint (siehe runGroupNow in JobsPage.tsx). */
  function runOrPick(policies: PolicySummary[], resourceGroupId?: string) {
    if (policies.length === 0) {
      notifications.show({
        title: "Keine Policy",
        message: "Dieses Objekt ist keiner Backup-Policy zugeordnet.",
        color: "red",
      });
      return;
    }
    if (policies.length === 1) {
      runPolicy(policies[0], resourceGroupId);
      return;
    }
    setPicker({ policies, resourceGroupId });
  }

  return {
    runOrPick,
    runPolicy,
    pickerPolicies: picker?.policies ?? null,
    closePicker: () => setPicker(null),
    // Fuer PolicyPickerModal.onPick -- reicht den beim Oeffnen des Dialogs
    // (falls vorhanden) gemerkten Gruppen-Kontext an runPolicy() weiter,
    // statt ihn zu verlieren.
    pickPolicy: (policy: PolicySummary) => runPolicy(policy, picker?.resourceGroupId),
  };
}
