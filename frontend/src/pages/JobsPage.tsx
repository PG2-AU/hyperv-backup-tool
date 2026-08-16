import { Badge, Button, Drawer, Menu, Stack, Table, Tabs, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlayerPlay, IconTerminal2, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useJobRuns, useJobs, useTriggerJobRun } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { LogViewer } from "@/components/LogViewer";
import type { BackupJobDefinition, JobStatus } from "@/api/types";

const STATUS_COLOR: Record<JobStatus, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "runs" ? "runs" : "definitions";
  const { data: jobs } = useJobs();
  const { data: runs } = useJobRuns();
  const triggerRun = useTriggerJobRun();
  const jobMenu = useContextMenu<BackupJobDefinition>();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);

  function runNow(job: BackupJobDefinition) {
    triggerRun.mutate(job.id, {
      onSuccess: () => notifications.show({ title: "Job gestartet", message: job.name, color: "blue" }),
      onError: () => notifications.show({ title: "Fehler", message: "Job konnte nicht gestartet werden", color: "red" }),
    });
  }

  function showLog(jobId: string) {
    setLogContext(jobId);
    openLogs();
  }

  return (
    <Stack>
      <Title order={3}>Backup-Jobs</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "definitions" })}>
        <Tabs.List>
          <Tabs.Tab value="definitions">Job-Definitionen</Tabs.Tab>
          <Tabs.Tab value="runs">Job-Verlauf</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="definitions" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Scope</Table.Th>
                <Table.Th>Ziele</Table.Th>
                <Table.Th>Konsistenz</Table.Th>
                <Table.Th>Zeitplan</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {jobs?.map((job) => (
                <Table.Tr key={job.id} onContextMenu={(e) => jobMenu.open(e, job)} style={{ cursor: "context-menu" }}>
                  <Table.Td>{job.name}</Table.Td>
                  <Table.Td>{job.scope}</Table.Td>
                  <Table.Td>{job.targets.join(", ")}</Table.Td>
                  <Table.Td>{job.consistency}</Table.Td>
                  <Table.Td>{job.schedule_cron ?? "manuell"}</Table.Td>
                  <Table.Td>
                    <Badge color={job.enabled ? "green" : "gray"} variant="light">
                      {job.enabled ? "aktiv" : "pausiert"}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>

        <Tabs.Panel value="runs" pt="md">
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Job</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Gestartet</Table.Th>
                <Table.Th>Beendet</Table.Th>
                <Table.Th>Fehler</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {runs?.map((run) => (
                <Table.Tr key={run.id}>
                  <Table.Td>{run.job_name}</Table.Td>
                  <Table.Td>
                    <Badge color={STATUS_COLOR[run.status]} variant="light">
                      {run.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{new Date(run.started_at).toLocaleString("de-DE")}</Table.Td>
                  <Table.Td>{run.finished_at ? new Date(run.finished_at).toLocaleString("de-DE") : "-"}</Table.Td>
                  <Table.Td>{run.error_message ?? "-"}</Table.Td>
                  <Table.Td>
                    <Button size="xs" variant="subtle" leftSection={<IconTerminal2 size={14} />} onClick={() => showLog(run.job_id)}>
                      Log
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Tabs.Panel>
      </Tabs>

      <ContextMenuDropdown position={jobMenu.state?.position ?? null} opened={!!jobMenu.state} onClose={jobMenu.close}>
        <Menu.Label>{jobMenu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconPlayerPlay size={16} />} onClick={() => jobMenu.state && runNow(jobMenu.state.data)}>
          Jetzt ausfuehren
        </Menu.Item>
        <Menu.Item leftSection={<IconEdit size={16} />}>Bearbeiten</Menu.Item>
        <Menu.Item leftSection={<IconTerminal2 size={16} />} onClick={() => jobMenu.state && showLog(jobMenu.state.data.id)}>
          Log anzeigen
        </Menu.Item>
        <Menu.Divider />
        <Menu.Item color="red" leftSection={<IconTrash size={16} />}>
          Loeschen
        </Menu.Item>
      </ContextMenuDropdown>

      <Drawer opened={logsOpened} onClose={closeLogs} position="bottom" size="45%" title="Job-Log">
        <LogViewer context={logContext} />
      </Drawer>
    </Stack>
  );
}
