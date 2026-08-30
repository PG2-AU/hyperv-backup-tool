import { useState } from "react";
import { Anchor, Badge, Grid, Group, Paper, SegmentedControl, SimpleGrid, Stack, Table, Text, ThemeIcon, Title } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconClockHour4,
  IconDatabase,
  IconServer2,
  IconShieldCheck,
  IconStack2,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";

import {
  useCsvs,
  useHyperVClusters,
  useJobRuns,
  useNetAppClusters,
  usePolicies,
  useSnapMirrorRelationships,
  useSvms,
  useVms,
  useVolumes,
} from "@/api/hooks";
import type { BackupJobRun, BackupRunSnapshot } from "@/api/types";

const RANGE_HOURS: Record<string, number> = { "24h": 24, "7d": 24 * 7 };

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

const HEALTH_COLOR: Record<string, string> = {
  healthy: "green",
  degraded: "orange",
  unreachable: "red",
  unknown: "gray",
};

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// SnapMirror-Pfade haben das Format "svm_name:volume_name".
function parseVolumePath(path: string | null | undefined): { svm: string; volume: string } | null {
  if (!path) return null;
  const [svm, volume] = path.split(":");
  if (!svm || !volume) return null;
  return { svm, volume };
}

function StatCard({
  icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  color?: string;
}) {
  return (
    <Paper p="md">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            {label}
          </Text>
          <Text size="xl" fw={700} c={color}>
            {value}
          </Text>
          {sub ? (
            <Text size="xs" c="dimmed">
              {sub}
            </Text>
          ) : null}
        </div>
        {icon}
      </Group>
    </Paper>
  );
}

export function DashboardPage() {
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const { data: policies } = usePolicies();
  const { data: runs } = useJobRuns();
  const { data: hyperVClusters } = useHyperVClusters();
  const { data: netAppClusters } = useNetAppClusters();
  const { data: svms } = useSvms();
  const { data: volumes } = useVolumes();
  const { data: relationships } = useSnapMirrorRelationships();
  const [jobsRange, setJobsRange] = useState<string>("24h");

  const failedRuns = runs?.filter((r) => r.status === "failed" || r.status === "cleaned_up_after_failure").length ?? 0;
  const protectedVms = vms?.filter((v) => v.protected).length ?? 0;

  const lastSuccessfulRun = runs
    ?.filter((r) => r.status === "succeeded")
    .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())[0];

  // Nur die SnapMirror-Beziehungen beruecksichtigen, deren Quell- oder
  // Ziel-Volume tatsaechlich einer vom Backup-Tool erfassten CSV gehoert
  // -- alle anderen im NetApp-Cluster vorhandenen Beziehungen sind fuer
  // dieses Tool nicht relevant und werden ausgeblendet.
  const referencedVolumeKeys = new Set(
    (csvs ?? []).filter((c) => c.svm_name && c.volume_name).map((c) => `${c.svm_name}|${c.volume_name}`),
  );
  const referencedRelationships = (relationships ?? []).filter((rel) => {
    const source = parseVolumePath(rel.source_path);
    const destination = parseVolumePath(rel.destination_path);
    return (
      (source && referencedVolumeKeys.has(`${source.svm}|${source.volume}`)) ||
      (destination && referencedVolumeKeys.has(`${destination.svm}|${destination.volume}`))
    );
  });

  const unhealthyClusters = hyperVClusters?.filter((c) => c.health !== "healthy").length ?? 0;
  const unhealthyRelationships = referencedRelationships.filter((r) => !r.healthy).length;
  const warningsCount = failedRuns + unhealthyClusters + unhealthyRelationships;

  const snapMirrorAllHealthy = referencedRelationships.length > 0 && unhealthyRelationships === 0;

  // "Letztes Backup" je Hyper-V-Cluster: ueber VM-Namen der Cluster-VMs
  // mit den Ziel-Namen erfolgreicher Job-Laeufe abgleichen (deckt aktuell
  // nur VM-Scope-Laeufe ab, nicht CSV-Scope).
  const lastRunPerCluster = new Map<string, BackupJobRun>();
  if (runs && vms) {
    for (const cluster of hyperVClusters ?? []) {
      const clusterVmNames = new Set(vms.filter((v) => v.cluster === cluster.name).map((v) => v.name));
      const clusterRuns = runs
        .filter((r) => r.status === "succeeded" && r.targets.some((t) => clusterVmNames.has(t)))
        .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime());
      if (clusterRuns[0]) lastRunPerCluster.set(cluster.id, clusterRuns[0]);
    }
  }

  const jobsRangeCutoff = Date.now() - RANGE_HOURS[jobsRange] * 60 * 60 * 1000;
  const jobsInRange = (runs ?? [])
    .filter((r) => new Date(r.started_at).getTime() >= jobsRangeCutoff)
    .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime());

  const recentSnapshots: (BackupRunSnapshot & { started_at: string })[] = (runs ?? [])
    .flatMap((r) => r.snapshots.filter((s) => s.success).map((s) => ({ ...s, started_at: r.started_at })))
    .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
    .slice(0, 6);

  return (
    <Stack>
      <Title order={3}>Dashboard</Title>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
        <StatCard
          icon={<IconServer2 size={28} />}
          label="Hyper-V Cluster"
          value={String(hyperVClusters?.length ?? "-")}
          sub="Gesamt"
        />
        <StatCard
          icon={<IconShieldCheck size={28} />}
          label="Geschuetzte VMs"
          value={String(protectedVms)}
          sub={vms ? `von ${vms.length} gesamt` : undefined}
        />
        <StatCard icon={<IconStack2 size={28} />} label="Aktive Policies" value={String(policies?.filter((p) => p.enabled).length ?? "-")} />
        <StatCard
          icon={<IconClockHour4 size={28} />}
          label="Letztes Backup"
          value={lastSuccessfulRun ? formatDateTime(lastSuccessfulRun.started_at) : "-"}
          sub={lastSuccessfulRun ? "Erfolgreich" : undefined}
        />
        <StatCard
          icon={<IconDatabase size={28} />}
          label="SnapMirror Status"
          value={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "OK" : `${unhealthyRelationships} Fehler`) : "-"}
          sub={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "Alle Replikationen gesund" : "Pruefung noetig") : undefined}
          color={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "green" : "red") : undefined}
        />
        <StatCard
          icon={<IconAlertTriangle size={28} />}
          label="Warnungen"
          value={String(warningsCount)}
          sub={warningsCount > 0 ? "Benoetigen Aufmerksamkeit" : undefined}
          color={warningsCount > 0 ? "red" : undefined}
        />
      </SimpleGrid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" h="100%">
            <Title order={5} mb="sm">
              Cluster-Uebersicht
            </Title>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Cluster</Table.Th>
                  <Table.Th>Geschuetzte VMs</Table.Th>
                  <Table.Th>Letztes Backup</Table.Th>
                  <Table.Th>Status</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {hyperVClusters?.map((cluster) => {
                  const clusterVms = vms?.filter((v) => v.cluster === cluster.name) ?? [];
                  const lastRun = lastRunPerCluster.get(cluster.id);
                  return (
                    <Table.Tr key={cluster.id}>
                      <Table.Td>{cluster.name}</Table.Td>
                      <Table.Td>{clusterVms.filter((v) => v.protected).length}</Table.Td>
                      <Table.Td>{lastRun ? formatDateTime(lastRun.started_at) : "-"}</Table.Td>
                      <Table.Td>
                        <Badge color={HEALTH_COLOR[cluster.health]} variant="light">
                          {cluster.health}
                        </Badge>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" h="100%">
            <Title order={5} mb="sm">
              NetApp Cluster
            </Title>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Cluster</Table.Th>
                  <Table.Th>SVMs</Table.Th>
                  <Table.Th>Volumes</Table.Th>
                  <Table.Th>Status</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {netAppClusters?.map((cluster) => (
                  <Table.Tr key={cluster.id}>
                    <Table.Td>{cluster.name}</Table.Td>
                    <Table.Td>{svms?.filter((s) => s.cluster_id === cluster.id).length ?? 0}</Table.Td>
                    <Table.Td>{volumes?.filter((v) => v.cluster_id === cluster.id).length ?? 0}</Table.Td>
                    <Table.Td>
                      <Badge color={HEALTH_COLOR[cluster.health]} variant="light">
                        {cluster.health}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
        </Grid.Col>
      </Grid>

      <Paper p="md">
        <Title order={5} mb="sm">
          SnapMirror Status
        </Title>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Quelle</Table.Th>
              <Table.Th>Ziel</Table.Th>
              <Table.Th>Ziel-Cluster</Table.Th>
              <Table.Th>Lag</Table.Th>
              <Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {referencedRelationships.length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={5}>
                  <Text size="sm" c="dimmed">
                    Keine vom Backup-Tool referenzierten SnapMirror-Beziehungen vorhanden.
                  </Text>
                </Table.Td>
              </Table.Tr>
            ) : (
              referencedRelationships.map((rel) => (
                <Table.Tr key={rel.id}>
                  <Table.Td>{rel.source_path ?? "-"}</Table.Td>
                  <Table.Td>{rel.destination_path ?? "-"}</Table.Td>
                  <Table.Td>{rel.destination_cluster_name ?? "-"}</Table.Td>
                  <Table.Td>{rel.lag_time ?? "-"}</Table.Td>
                  <Table.Td>
                    <Badge color={rel.healthy ? "green" : "red"} variant="light">
                      {rel.healthy ? "Gesund" : "Warnung"}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))
            )}
          </Table.Tbody>
        </Table>
      </Paper>

      <Paper p="md">
        <Group justify="space-between" mb="sm">
          <Title order={5}>Job-Laeufe</Title>
          <SegmentedControl
            size="xs"
            value={jobsRange}
            onChange={setJobsRange}
            data={[
              { label: "Letzte 24h", value: "24h" },
              { label: "Letzte 7 Tage", value: "7d" },
            ]}
          />
        </Group>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Job</Table.Th>
              <Table.Th>Scope</Table.Th>
              <Table.Th>Ziele</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Gestartet</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {jobsInRange.length === 0 ? (
              <Table.Tr>
                <Table.Td colSpan={5}>
                  <Text size="sm" c="dimmed">
                    Keine Job-Laeufe in diesem Zeitraum.
                  </Text>
                </Table.Td>
              </Table.Tr>
            ) : (
              jobsInRange.map((run) => (
                <Table.Tr key={run.id}>
                  <Table.Td>{run.job_name}</Table.Td>
                  <Table.Td>{run.scope}</Table.Td>
                  <Table.Td>{run.targets.join(", ")}</Table.Td>
                  <Table.Td>
                    <Badge color={STATUS_COLOR[run.status]} variant="light">
                      {run.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{new Date(run.started_at).toLocaleString("de-DE")}</Table.Td>
                </Table.Tr>
              ))
            )}
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to="/jobs?tab=runs" size="sm" mt="sm" style={{ display: "inline-block" }}>
          Alle Job-Laeufe anzeigen
        </Anchor>
      </Paper>

      <Paper p="md">
        <Title order={5} mb="sm">
          Letzte Snapshots
        </Title>
        {recentSnapshots.length === 0 ? (
          <Text size="sm" c="dimmed">
            Noch keine Snapshots vorhanden.
          </Text>
        ) : (
          <SimpleGrid cols={{ base: 1, sm: 2, lg: 3, xl: 6 }}>
            {recentSnapshots.map((snap) => (
              <Paper key={snap.id} withBorder p="sm">
                <Group gap="xs" wrap="nowrap">
                  <ThemeIcon color="green" variant="light" radius="xl">
                    <IconCircleCheck size={16} />
                  </ThemeIcon>
                  <div style={{ overflow: "hidden" }}>
                    <Text size="sm" fw={600} truncate>
                      {snap.volume_name ?? "-"}
                    </Text>
                    <Text size="xs" c="dimmed" truncate>
                      {snap.snapshot_name ?? "-"}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {formatDateTime(snap.started_at)}
                    </Text>
                  </div>
                </Group>
              </Paper>
            ))}
          </SimpleGrid>
        )}
      </Paper>
    </Stack>
  );
}
