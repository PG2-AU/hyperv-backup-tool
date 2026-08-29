import { useEffect, useState } from "react";
import { Badge, Button, Group, Loader, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconCheck } from "@tabler/icons-react";

import { useAggregates, useCheckSnapMirror, useNetAppClusters, useSvms, useVolumes } from "@/api/hooks";
import { ProcessModal, type ProcessPlan } from "@/components/ProcessModal";
import { SnapmirrorFormModal } from "@/components/SnapmirrorFormModal";
import type { SnapMirrorCheckGroup, SnapMirrorCheckResult, SnapmirrorCreationPlan } from "@/api/types";
import { buildSnapmirrorCreationSteps } from "@/utils/netappSteps";

interface SnapMirrorCheckPanelProps {
  /** Nur pruefen/anzeigen, wenn die zugehoerige Policy 'SnapMirror-Update
   * nach Snapshot' aktiviert hat -- sonst ist eine fehlende Beziehung kein
   * Problem und soll nicht gemeldet werden. */
  enabled: boolean;
  groups: SnapMirrorCheckGroup[];
}

/** Prueft fuer die ausgewaehlten VM-/CSV-Objekte einer Protection Group
 * (oder aller Protection Groups einer Policy, siehe PolicyFormModal), ob
 * fuer das zugrunde liegende NetApp-Volume bereits eine SnapMirror-
 * Beziehung existiert -- und zeigt entweder die darauf aktive SnapMirror-
 * Policy an, oder bietet direkt den bestehenden 'Beziehung erstellen'-
 * Wizard (SnapmirrorFormModal, sonst nur von der Storage-Seite aus
 * erreichbar) mit vorausgefuelltem Quell-Volume an. Wiederverwendet von
 * ResourceGroupFormModal.tsx (Objekte+Policy direkt beim Anlegen bekannt)
 * und PolicyFormModal.tsx (im Bearbeiten-Modus ueber die bereits
 * verknuepften Protection Groups)." */
export function SnapMirrorCheckPanel({ enabled, groups }: SnapMirrorCheckPanelProps) {
  const { data: clusters } = useNetAppClusters();
  const { data: svms } = useSvms();
  const { data: volumes } = useVolumes();
  const { data: aggregates } = useAggregates();
  const checkSnapMirror = useCheckSnapMirror();
  const [results, setResults] = useState<SnapMirrorCheckResult[] | null>(null);
  const [snapmirrorFormOpen, setSnapmirrorFormOpen] = useState(false);
  const [initialSource, setInitialSource] = useState<{ clusterId: string; svmName: string; volumeName: string; sizeBytes: number } | null>(
    null,
  );
  const [process, setProcess] = useState<ProcessPlan | null>(null);

  const hasMembers = groups.some((g) => g.members.length > 0);
  // Serialisiert, damit ein bei jedem Render neu erzeugtes (aber inhaltlich
  // gleiches) groups-Array keinen erneuten Check ausloest.
  const depsKey = JSON.stringify(groups);

  useEffect(() => {
    if (!enabled || !hasMembers) {
      setResults(null);
      return;
    }
    checkSnapMirror.mutate(groups, { onSuccess: (data) => setResults(data) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, hasMembers, depsKey]);

  if (!enabled || !hasMembers) return null;
  if (checkSnapMirror.isPending && !results) return <Loader size="sm" />;
  if (!results || results.length === 0) return null;

  function openCreateWizard(result: SnapMirrorCheckResult) {
    const volume = volumes?.find((v) => v.svm_name === result.svm_name && v.name === result.volume_name);
    if (!volume) return;
    setInitialSource({
      clusterId: volume.cluster_id, svmName: result.svm_name, volumeName: result.volume_name, sizeBytes: volume.size_bytes ?? 0,
    });
    setSnapmirrorFormOpen(true);
  }

  return (
    <>
      <Stack gap={6}>
        {results.map((r) => (
          <Group key={`${r.svm_name}:${r.volume_name}`} justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed" truncate>
              {r.members.join(", ")} → {r.svm_name}:{r.volume_name}
            </Text>
            {r.has_relationship ? (
              <Badge color="green" variant="light" leftSection={<IconCheck size={12} />}>
                SnapMirror aktiv ({r.policy_name ?? "?"})
              </Badge>
            ) : (
              <Group gap="xs" wrap="nowrap">
                <Badge color="orange" variant="light" leftSection={<IconAlertTriangle size={12} />}>
                  Keine SnapMirror-Beziehung
                </Badge>
                <Button size="xs" variant="light" onClick={() => openCreateWizard(r)}>
                  Jetzt erstellen
                </Button>
              </Group>
            )}
          </Group>
        ))}
      </Stack>

      <SnapmirrorFormModal
        opened={snapmirrorFormOpen}
        onClose={() => setSnapmirrorFormOpen(false)}
        clusters={clusters}
        svms={svms}
        volumes={volumes}
        aggregates={aggregates}
        initialSource={initialSource}
        onSubmitPlan={(plan: SnapmirrorCreationPlan) => {
          setSnapmirrorFormOpen(false);
          setProcess({ title: "SnapMirror-Beziehung erstellen", steps: buildSnapmirrorCreationSteps(plan) });
        }}
      />
      <ProcessModal opened={!!process} onClose={() => setProcess(null)} plan={process} />
    </>
  );
}
