import { useState } from "react";
import { notifications } from "@mantine/notifications";

import { useResourceGroups, useTriggerJobRun } from "@/api/hooks";
import type { PolicySummary, ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatSchedule } from "@/utils/format";

/** Startet eine Backup-Policy direkt, wenn genau eine (Gruppe, Policy)-
 * Kombination zur Auswahl steht; bei mehreren wird ein Auswahl-Dialog
 * geoeffnet (siehe PolicyPickerModal). */
export function useRunPolicy() {
  const triggerRun = useTriggerJobRun();
  const { data: groups } = useResourceGroups();

  // Einstufiger Dialog (Gruppe bereits bekannt, siehe runOrPick) --
  // mehrere Policies auf DERSELBEN Gruppe, reine Policy-Auswahl reicht.
  const [candidates, setCandidates] = useState<{ key: string; policy: PolicySummary; resourceGroupId?: string; label: string }[] | null>(
    null,
  );

  // Zweistufiger Dialog (Gruppe selbst Teil der Auswahl, siehe
  // runOrPickForGroups) -- Nutzer-Vorgabe 2026-09-09: ZUERST die Protection
  // Group waehlen, DANACH (nur falls diese Gruppe mehrere Policies
  // verknuepft hat) die Policy dahinter.
  const [groupChoices, setGroupChoices] = useState<ResourceGroup[] | null>(null);
  const [chosenGroup, setChosenGroup] = useState<ResourceGroup | null>(null);

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

  function runPolicyForGroup(group: ResourceGroup) {
    if (group.policy_links.length === 1) {
      const link = group.policy_links[0];
      runPolicy({ id: link.policy_id, name: link.policy_name }, group.id);
      return;
    }
    setChosenGroup(group);
  }

  /** Fuer Objekte (VM/CSV), die gleichzeitig ueber MEHRERE Protection
   * Groups geschuetzt sein koennen (Nutzer-Vorgabe 2026-09-09).
   * `protectingGroups` sind die bereits aufgeloesten ResourceGroup-Objekte
   * (siehe VmsPage.tsx). Zweistufig: ZUERST die Protection Group waehlen,
   * DANACH (nur falls diese mehrere Policies verknuepft hat) die Policy. */
  function runOrPickForGroups(protectingGroups: ResourceGroup[]) {
    const withPolicies = protectingGroups.filter((g) => g.policy_links.length > 0);
    if (withPolicies.length === 0) {
      notifications.show({
        title: "Keine Policy",
        message: "Dieses Objekt ist keiner Backup-Policy zugeordnet.",
        color: "red",
      });
      return;
    }
    if (withPolicies.length === 1) {
      runPolicyForGroup(withPolicies[0]);
      return;
    }
    setGroupChoices(withPolicies);
  }

  function pickStep1Group(groupId: string) {
    const group = groupChoices?.find((g) => g.id === groupId);
    setGroupChoices(null);
    if (group) runPolicyForGroup(group);
  }

  function pickStep2Policy(policyId: string) {
    const link = chosenGroup?.policy_links.find((l) => l.policy_id === policyId);
    if (link && chosenGroup) runPolicy({ id: link.policy_id, name: link.policy_name }, chosenGroup.id);
    setChosenGroup(null);
  }

  // ZWEI getrennte Close-Handler statt einem gemeinsamen: PolicyPickerModal
  // ruft bei jeder Auswahl intern IMMER onPick gefolgt von onClose auf
  // (siehe PolicyPickerModal.tsx). Ein gemeinsamer Handler, der bei JEDEM
  // onClose beide States loescht, hat live beobachtet den gerade erst von
  // pickStep1Group() gesetzten chosenGroup (Uebergang zu Schritt 2) im
  // selben Klick sofort wieder auf null gesetzt, bevor Schritt 2 ueberhaupt
  // sichtbar wurde -- Schritt 1 schloss sich, Schritt 2 oeffnete sich nie.
  function closeStep1() {
    setGroupChoices(null);
  }

  function closeStep2() {
    setChosenGroup(null);
  }

  return {
    runOrPick,
    runOrPickForGroups,
    runPolicy,
    // Einstufiger Dialog (runOrPick, Gruppe schon bekannt).
    pickerPolicies: candidates?.map((c) => ({ id: c.key, name: c.label })) ?? null,
    closePicker: () => setCandidates(null),
    pickPolicy: (picked: PolicySummary) => {
      const candidate = candidates?.find((c) => c.key === picked.id);
      if (candidate) runPolicy(candidate.policy, candidate.resourceGroupId);
    },
    // Zweistufiger Dialog (runOrPickForGroups): Schritt 1 = Protection
    // Group, Schritt 2 = Policy dieser Gruppe (Zeitplan mit im Label).
    step1GroupChoices: groupChoices?.map((g) => ({ id: g.id, name: g.name })) ?? null,
    step2PolicyChoices:
      chosenGroup?.policy_links.map((l) => ({ id: l.policy_id, name: `${l.policy_name} (${formatSchedule(l.schedule)})` })) ?? null,
    pickStep1Group: (picked: PolicySummary) => pickStep1Group(picked.id),
    pickStep2Policy: (picked: PolicySummary) => pickStep2Policy(picked.id),
    closeStep1,
    closeStep2,
  };
}
