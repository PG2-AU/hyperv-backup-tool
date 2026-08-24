import { useState } from "react";
import { Badge, Button, Drawer, Group, Menu, Modal, Select, Stack, Switch, Table, Tabs, TextInput, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlayerPlay, IconPlus, IconTerminal2, IconTrash } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useCreateJob, useDeleteJob, useJobRuns, useJobs, useSchedules, useTriggerJobRun } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { LogViewer } from "@/components/LogViewer";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import type { BackupJobDefinition, JobStatus } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatSchedule } from "@/utils/format";

const STATUS_COLOR: Record<JobStatus, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

const NEW_SCHEDULE_VALUE = "__new__";

function CreateJobModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const createJob = useCreateJob();
  const { data: schedules } = useSchedules();
  const [name, setName] = useState("");
  const [scheduleId, setScheduleId] = useState<string | null>(null);
  const [appConsistent, setAppConsistent] = useState(true);
  const [snapmirrorUpdate, setSnapmirrorUpdate] = useState(true);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);

  function reset() {
    setName("");
    setScheduleId(null);
    setAppConsistent(true);
    setSnapmirrorUpdate(true);
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    createJob.mutate(
      { name, schedule_id: scheduleId, app_consistent: appConsistent, snapmirror_update: snapmirrorUpdate },
      {
        onSuccess: (job) => {
          notifications.show({ title: "Job erstellt", message: job.name, color: "green" });
          handleClose();
        },
        onError: (err) => {
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Job konnte nicht erstellt werden."), color: "red" });
        },
      },
    );
  }

  return (
    <>
      <Modal opened={opened} onClose={handleClose} title="Neuen Backup-Job erstellen">
        <Stack>
          <TextInput label="Job-Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <Select
            label="Zeitplan"
            placeholder="Kein Zeitplan (nur manuell)"
            data={[
              { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Zeitplan erstellen..." },
              ...(schedules?.map((s) => ({ value: s.id, label: `${s.name} (${formatSchedule(s)})` })) ?? []),
            ]}
            value={scheduleId}
            onChange={(v) => (v === NEW_SCHEDULE_VALUE ? setScheduleModalOpen(true) : setScheduleId(v))}
            clearable
          />
          <Switch
            label="Applikationskonsistent (VSS-Checkpoint)"
            description="Nein = crash-konsistent (Standard-Checkpoint)"
            checked={appConsistent}
            onChange={(e) => setAppConsistent(e.currentTarget.checked)}
          />
          <Switch
            label="SnapMirror-Update nach Snapshot"
            checked={snapmirrorUpdate}
            onChange={(e) => setSnapmirrorUpdate(e.currentTarget.checked)}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={handleClose}>
              Abbrechen
            </Button>
            <Button onClick={handleSubmit} loading={createJob.isPending} disabled={!name}>
              Erstellen
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ScheduleFormModal opened={scheduleModalOpen} onClose={() => setScheduleModalOpen(false)} onSaved={(s) => setScheduleId(s.id)} />
    </>
  );
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") === "runs" ? "runs" : "definitions";
  const { data: jobs } = useJobs();
  const { data: runs } = useJobRuns();
  const triggerRun = useTriggerJobRun();
  const deleteJob = useDeleteJob();
  const jobMenu = useContextMenu<BackupJobDefinition>();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);
  const [createOpen, setCreateOpen] = useState(false);

  function runNow(job: BackupJobDefinition) {
    triggerRun.mutate(job.id, {
      onSuccess: () => notifications.show({ title: "Job gestartet", message: job.name, color: "blue" }),
      onError: () => notifications.show({ title: "Fehler", message: "Job konnte nicht gestartet werden", color: "red" }),
    });
  }

  function removeJob(job: BackupJobDefinition) {
    if (!window.confirm(`Job '${job.name}' wirklich löschen?`)) return;
    deleteJob.mutate(job.id, {
      onSuccess: () => notifications.show({ title: "Job gelöscht", message: job.name, color: "blue" }),
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Job konnte nicht gelöscht werden."), color: "red" }),
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
          <Stack>
            <Button leftSection={<IconPlus size={16} />} onClick={() => setCreateOpen(true)} style={{ alignSelf: "flex-end" }}>
              Neuer Job
            </Button>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Zeitplan</Table.Th>
                  <Table.Th>App-konsistent</Table.Th>
                  <Table.Th>SnapMirror-Update</Table.Th>
                  <Table.Th>Ziele</Table.Th>
                  <Table.Th>Status</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {jobs?.map((job) => (
                  <Table.Tr key={job.id} onContextMenu={(e) => jobMenu.open(e, job)} style={{ cursor: "context-menu" }}>
                    <Table.Td>{job.name}</Table.Td>
                    <Table.Td>{formatSchedule(job.schedule)}</Table.Td>
                    <Table.Td>
                      <Badge color={job.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light">
                        {job.consistency === "ApplicationConsistent" ? "Ja" : "Nein"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge color={job.snapmirror_update ? "green" : "gray"} variant="light">
                        {job.snapmirror_update ? "Ja" : "Nein"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{job.targets.length ? job.targets.join(", ") : "-"}</Table.Td>
                    <Table.Td>
                      <Badge color={job.enabled ? "green" : "gray"} variant="light">
                        {job.enabled ? "aktiv" : "pausiert"}
                      </Badge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
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
        <Menu.Item leftSection={<IconEdit size={16} />} disabled>
          Bearbeiten
        </Menu.Item>
        <Menu.Item leftSection={<IconTerminal2 size={16} />} onClick={() => jobMenu.state && showLog(jobMenu.state.data.id)}>
          Log anzeigen
        </Menu.Item>
        <Menu.Divider />
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={() => jobMenu.state && removeJob(jobMenu.state.data)}>
          Loeschen
        </Menu.Item>
      </ContextMenuDropdown>

      <Drawer opened={logsOpened} onClose={closeLogs} position="bottom" size="45%" title="Job-Log">
        <LogViewer context={logContext} />
      </Drawer>

      <CreateJobModal opened={createOpen} onClose={() => setCreateOpen(false)} />
    </Stack>
  );
}
