import { Badge, Group, Paper, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { IconAlertTriangle, IconCircleCheck, IconServer2, IconStack2 } from "@tabler/icons-react";

import { useJobRuns, useJobs, useMetroClusterStatus, useVms } from "@/api/hooks";

const STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: string; color?: string }) {
  return (
    <Paper p="md">
      <Group justify="space-between">
        <div>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            {label}
          </Text>
          <Text size="xl" fw={700} c={color}>
            {value}
          </Text>
        </div>
        {icon}
      </Group>
    </Paper>
  );
}

export function DashboardPage() {
  const { data: vms } = useVms();
  const { data: jobs } = useJobs();
  const { data: runs } = useJobRuns();
  const { data: mcc } = useMetroClusterStatus();

  const failedRuns = runs?.filter((r) => r.status === "failed" || r.status === "cleaned_up_after_failure").length ?? 0;

  return (
    <Stack>
      <Title order={3}>Dashboard</Title>

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
        <StatCard icon={<IconServer2 size={28} />} label="Virtuelle Maschinen" value={String(vms?.length ?? "-")} />
        <StatCard icon={<IconStack2 size={28} />} label="Aktive Backup-Jobs" value={String(jobs?.filter((j) => j.enabled).length ?? "-")} />
        <StatCard
          icon={<IconAlertTriangle size={28} />}
          label="Fehlgeschlagene Laeufe (letzte)"
          value={String(failedRuns)}
          color={failedRuns > 0 ? "red" : undefined}
        />
        <StatCard
          icon={<IconCircleCheck size={28} />}
          label="MetroCluster"
          value={mcc ? (mcc.switchover_in_progress ? "Switchover aktiv" : mcc.mode) : "-"}
          color={mcc?.switchover_in_progress ? "orange" : "green"}
        />
      </SimpleGrid>

      <Paper p="md">
        <Title order={5} mb="sm">
          Letzte Job-Laeufe
        </Title>
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
            {runs?.map((run) => (
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
            ))}
          </Table.Tbody>
        </Table>
      </Paper>
    </Stack>
  );
}
