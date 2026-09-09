import { useState } from "react";
import { notifications } from "@mantine/notifications";

import { useResourceGroups, useTriggerJobRun } from "@/api/hooks";
import type { PolicySummary, ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

interface RunCandidate {
  // Fuer runOrPick (Gruppe bereits bekannt) = policy.id; fuer
  // runOrPickForGroups (Gruppe selbst Teil der Auswahl, siehe unten) =
  // zusammengesetzter Schluessel, da dieselbe Policy fuer mehrere Gruppen
  // auftauchen kann.
  key: string;
  policy: PolicySummary;
  resourceGroupId?: string;
  label: string;
}

/** Startet eine Backup-Policy direkt, wenn genau eine (Policy, Gruppe)-
 * Kombination zur Auswahl steht; bei mehreren wird ein Auswahl-Dialog
 * geoeffnet (siehe PolicyPickerModal). */
export function useRunPolicy() {
  const triggerRun = useTriggerJobRun();
  const { data: groups } = useResourceGroups();
  const [candidates, setCandidates] = useState<RunCandidate[] | null>(null);

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
      // Inventory), wird explicitResourceGroupId oben direkt uebernommen,
      // diese Ableitung also gar nicht erst versucht.
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
   * Gruppe) auf genau diese Gruppe ein -- vom Aufrufer mitgegeben, wenn er
   * (anders als im Policies-Tab) bereits eine konkrete Resource Group
   * meint (siehe runGroupNow in JobsPage.tsx). BUG, live gefunden
   * 2026-09-09: fehlte dieser Parameter, leitete runPolicy() die Gruppe
   * selbst aus der Policy-Verknuepfung ab -- war die Policy an MEHRERE
   * Gruppen gehaengt, lief der Job faelschlich fuer ALLE davon, nicht nur
   * die angeklickte. */
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
    setCandidates(policies.map((p) => ({ key: p.id, policy: p, resourceGroupId, label: p.name })));
  }

  /** Fuer Objekte (VM/CSV), die gleichzeitig ueber MEHRERE Protection
   * Groups geschuetzt sein koennen (Nutzer-Vorgabe 2026-09-09) -- anders
   * als runOrPick ist hier auch die Gruppe selbst Teil der Auswahl, nicht
   * nur die Policy: dieselbe Policy kann fuer zwei verschiedene, dieses
   * Objekt schuetzende Gruppen auftauchen. `protectingGroups` sind die
   * bereits aufgeloesten ResourceGroup-Objekte (siehe VmsPage.tsx), nicht
   * nur deren Namen -- liefert dadurch group.id UND group.policies direkt,
   * ohne erneute Ableitung wie in runPolicy(). */
  function runOrPickForGroups(protectingGroups: ResourceGroup[]) {
    const list: RunCandidate[] = [];
    for (const group of protectingGroups) {
      for (const policy of group.policies) {
        list.push({ key: `${policy.id}::${group.id}`, policy, resourceGroupId: group.id, label: `${policy.name} (${group.name})` });
      }
    }
    if (list.length === 0) {
      notifications.show({
        title: "Keine Policy",
        message: "Dieses Objekt ist keiner Backup-Policy zugeordnet.",
        color: "red",
      });
      return;
    }
    if (list.length === 1) {
      runPolicy(list[0].policy, list[0].resourceGroupId);
      return;
    }
    setCandidates(list);
  }

  return {
    runOrPick,
    runOrPickForGroups,
    runPolicy,
    // Fuer PolicyPickerModal: key statt der rohen policy.id, damit auch
    // Kandidaten mit identischer Policy, aber unterschiedlicher Gruppe
    // (siehe runOrPickForGroups) eindeutig bleiben; label statt name, da
    // bei runOrPickForGroups der Gruppenname mit im Text steht.
    pickerPolicies: candidates?.map((c) => ({ id: c.key, name: c.label })) ?? null,
    closePicker: () => setCandidates(null),
    // Fuer PolicyPickerModal.onPick -- loest den zusammengesetzten
    // Schluessel wieder zum echten (Policy, Gruppe)-Paar auf, statt den
    // Gruppen-Kontext beim Auswaehlen zu verlieren.
    pickPolicy: (picked: PolicySummary) => {
      const candidate = candidates?.find((c) => c.key === picked.id);
      if (candidate) runPolicy(candidate.policy, candidate.resourceGroupId);
    },
  };
}
