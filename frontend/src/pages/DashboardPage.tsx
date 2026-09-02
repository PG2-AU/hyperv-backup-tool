import { useState } from "react";
import { Anchor, Badge, Grid, Group, Paper, ScrollArea, SegmentedControl, SimpleGrid, Stack, Table, Text, ThemeIcon, Title } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconClockHour4,
  IconDatabase,
  IconServer2,
  IconShieldCheck,
} from "@tabler/icons-react";
import { Link } from "react-router-dom";

import {
  useAlerts,
  useCsvs,
  useHyperVClusters,
  useJobRuns,
  useJobsCalendar,
  useNetAppClusters,
  useSnapMirrorRelationships,
  useSvms,
  useVms,
  useVolumes,
} from "@/api/hooks";
import { DayJobStrip } from "@/components/DayJobStrip";
import type { BackupJobRun, BackupRunSnapshot } from "@/api/types";

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

// Jobs-Tabelle: bis zu 8 Zeilen ohne Scrollen sichtbar, danach vertikal
// scrollbar (Kopfzeile bleibt beim Scrollen sichtbar, siehe sticky-Style
// unten) -- reine Schaetzwerte fuer die Standard-Zeilenhoehe, da die
// tatsaechliche Hoehe von der unter Settings > Anzeige waehlbaren
// Content-Schriftgroesse mit abhaengt. Bewusst niedriger als frueher (15),
// da geplante Jobs nicht mehr Teil dieser Tabelle sind (siehe
// DashboardDayStrip weiter oben) und das Fenster insgesamt kompakter sein
// soll.
const JOBS_TABLE_VISIBLE_ROWS = 8;
const ROW_HEIGHT_PX = 44;
const HEADER_HEIGHT_PX = 44;

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
  to,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  color?: string;
  to?: string;
}) {
  return (
    <Paper p="sm" component={to ? Link : undefined} to={to as string} style={to ? { cursor: "pointer" } : undefined}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <div style={{ overflow: "hidden" }}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700} truncate>
            {label}
          </Text>
          <Text size="lg" fw={700} c={color} truncate>
            {value}
          </Text>
          {sub ? (
            <Text size="xs" c="dimmed" truncate>
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
  const [jobsRangeDays, setJobsRangeDays] = useState("1");
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const { data: runs } = useJobRuns();
  // Lokales Datum (nicht toISOString(), das UTC ist und rund um Mitternacht
  // auf den falschen Kalendertag zeigen wuerde) -- deckungsgleich mit der
  // dayKey()-Logik in BackupCalendarTab.
  const now0 = new Date();
  const todayStr = `${now0.getFullYear()}-${String(now0.getMonth() + 1).padStart(2, "0")}-${String(now0.getDate()).padStart(2, "0")}`;
  const { data: todayJobs } = useJobsCalendar(todayStr, todayStr);
  const { data: hyperVClusters } = useHyperVClusters();
  const { data: netAppClusters } = useNetAppClusters();
  const { data: svms } = useSvms();
  const { data: volumes } = useVolumes();
  const { data: relationships } = useSnapMirrorRelationships();
  const { data: alerts } = useAlerts();

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

  const unhealthyRelationships = referencedRelationships.filter((r) => !r.healthy).length;
  // Ersetzt die vorherige rein clientseitige Zusammenzaehlung (fehlgeschlagene
  // Laeufe + ungesunde Cluster + ungesunde Beziehungen) durch die zentrale,
  // auch fuer die Alarme-Seite genutzte Quelle (siehe app.core.scheduler.
  // run_alert_check) -- deckt zusaetzlich Kapazitaets-Warnungen ab.
  const activeAlertsCount = (alerts ?? []).filter((a) => a.status === "active").length;

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


  // Jobs-Tabelle zeigt ausschliesslich VERGANGENE Laeufe -- geplante Jobs
  // stehen stattdessen im kompakten Tages-Zeitstrahl weiter oben
  // (DashboardDayStrip). Zeitraum ist ueber jobsRangeDays waehlbar (1/2/3
  // Tage zurueck).
  const jobsRangeCutoff = Date.now() - Number(jobsRangeDays) * 24 * 60 * 60 * 1000;
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

      <SimpleGrid cols={{ base: 1, xs: 2, sm: 3, lg: 5 }}>
        <StatCard
          icon={<IconServer2 size={24} />}
          label="Hyper-V Cluster"
          value={String(hyperVClusters?.length ?? "-")}
          sub="Gesamt"
        />
        <StatCard
          icon={<IconShieldCheck size={24} />}
          label="Geschuetzte VMs"
          value={String(protectedVms)}
          sub={vms ? `von ${vms.length} gesamt` : undefined}
        />
        <StatCard
          icon={<IconClockHour4 size={24} />}
          label="Letztes Backup"
          value={lastSuccessfulRun ? formatDateTime(lastSuccessfulRun.started_at) : "-"}
          sub={lastSuccessfulRun ? "Erfolgreich" : undefined}
        />
        <StatCard
          icon={<IconDatabase size={24} />}
          label="SnapMirror Status"
          value={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "OK" : `${unhealthyRelationships} Fehler`) : "-"}
          sub={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "Alle Replikationen gesund" : "Pruefung noetig") : undefined}
          color={referencedRelationships.length > 0 ? (snapMirrorAllHealthy ? "green" : "red") : undefined}
        />
        <StatCard
          icon={<IconAlertTriangle size={24} />}
          label="Warnungen"
          value={String(activeAlertsCount)}
          sub={activeAlertsCount > 0 ? "Benötigen Aufmerksamkeit -- Details ansehen" : undefined}
          color={activeAlertsCount > 0 ? "red" : undefined}
          to="/alerts"
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

      <Grid>
        <Grid.Col span={12}>
          <DayJobStrip runs={runs ?? []} scheduled={todayJobs ?? []} />
        </Grid.Col>
      </Grid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" h="100%">
            <Title order={5} mb="sm">
              SnapMirror Status
            </Title>
            <Table.ScrollContainer minWidth={500}>
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
            </Table.ScrollContainer>
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 6 }}>
          <Paper p="md" h="100%">
            <Group justify="space-between" mb="sm" align="flex-end">
              <Title order={5}>Jobs</Title>
              <SegmentedControl
                size="xs"
                value={jobsRangeDays}
                onChange={setJobsRangeDays}
                data={[
                  { label: "1 Tag", value: "1" },
                  { label: "2 Tage", value: "2" },
                  { label: "3 Tage", value: "3" },
                ]}
              />
            </Group>
            <ScrollArea.Autosize mah={JOBS_TABLE_VISIBLE_ROWS * ROW_HEIGHT_PX + HEADER_HEIGHT_PX} type="auto">
            <Table.ScrollContainer minWidth={500}>
              <Table striped highlightOnHover>
                <Table.Thead style={{ position: "sticky", top: 0, zIndex: 1, backgroundColor: "var(--mantine-color-body)" }}>
                  <Table.Tr>
                    <Table.Th>Job</Table.Th>
                    <Table.Th>Scope</Table.Th>
                    <Table.Th>Ziele</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Zeitpunkt</Table.Th>
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
            </Table.ScrollContainer>
            </ScrollArea.Autosize>
            <Anchor component={Link} to="/jobs?tab=runs" size="sm" mt="sm" style={{ display: "inline-block" }}>
              Alle Job-Laeufe anzeigen
            </Anchor>
          </Paper>
        </Grid.Col>
      </Grid>

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
