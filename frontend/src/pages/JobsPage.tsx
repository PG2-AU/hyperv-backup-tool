import { useState } from "react";
import { Badge, Button, Drawer, Menu, Stack, Table, Tabs, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlayerPlay, IconPlus, IconTerminal2, IconTrash } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useDeletePolicy, useJobRuns, usePolicies, useTriggerJobRun } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { LogViewer } from "@/components/LogViewer";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import type { BackupPolicy, JobStatus } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatRetention, formatSchedule } from "@/utils/format";

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
  const activeTab = params.get("tab") === "runs" ? "runs" : "policies";
  const { data: policies } = usePolicies();
  const { data: runs } = useJobRuns();
  const triggerRun = useTriggerJobRun();
  const deletePolicy = useDeletePolicy();
  const policyMenu = useContextMenu<BackupPolicy>();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);
  const [formOpen, setFormOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<BackupPolicy | null>(null);

  function runNow(policy: BackupPolicy) {
    triggerRun.mutate(policy.id, {
      onSuccess: () => notifications.show({ title: "Job gestartet", message: policy.name, color: "blue" }),
      onError: () => notifications.show({ title: "Fehler", message: "Job konnte nicht gestartet werden", color: "red" }),
    });
  }

  function openCreate() {
    setEditingPolicy(null);
    setFormOpen(true);
  }

  function openEdit(policy: BackupPolicy) {
    setEditingPolicy(policy);
    setFormOpen(true);
  }

  function removePolicy(policy: BackupPolicy) {
    if (!window.confirm(`Backup-Policy '${policy.name}' wirklich löschen?`)) return;
    deletePolicy.mutate(policy.id, {
      onSuccess: () => notifications.show({ title: "Policy gelöscht", message: policy.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Policy konnte nicht gelöscht werden."), color: "red" }),
    });
  }

  function showLog(jobId: string) {
    setLogContext(jobId);
    openLogs();
  }

  return (
    <Stack>
      <Title order={3}>Backup-Jobs</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "policies" })}>
        <Tabs.List>
          <Tabs.Tab value="policies">Policies</Tabs.Tab>
          <Tabs.Tab value="runs">Job-Verlauf</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="policies" pt="md">
          <Stack>
            <Button leftSection={<IconPlus size={16} />} onClick={openCreate} style={{ alignSelf: "flex-end" }}>
              Neue Policy
            </Button>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Zeitplan</Table.Th>
                  <Table.Th>App-konsistent</Table.Th>
                  <Table.Th>SnapMirror</Table.Th>
                  <Table.Th>Retention</Table.Th>
                  <Table.Th>Snapshot Locking</Table.Th>
                  <Table.Th>Status</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {policies?.map((policy) => (
                  <Table.Tr key={policy.id} onContextMenu={(e) => policyMenu.open(e, policy)} style={{ cursor: "context-menu" }}>
                    <Table.Td>{policy.name}</Table.Td>
                    <Table.Td>{formatSchedule(policy.schedule)}</Table.Td>
                    <Table.Td>
                      <Badge color={policy.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light">
                        {policy.consistency === "ApplicationConsistent" ? "Ja" : "Nein"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {policy.snapmirror_update ? (
                        <Badge color="green" variant="light">
                          {policy.snapmirror_label?.name ?? "Update, kein Label"}
                        </Badge>
                      ) : (
                        <Badge color="gray" variant="light">
                          Nein
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{formatRetention(policy)}</Table.Td>
                    <Table.Td>
                      {policy.snapshot_locking_enabled ? (
                        <Badge color="indigo" variant="light">
                          {policy.snapshot_locking_days} Tage
                        </Badge>
                      ) : (
                        <Badge color="gray" variant="light">
                          Aus
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Badge color={policy.enabled ? "green" : "gray"} variant="light">
                        {policy.enabled ? "aktiv" : "pausiert"}
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

      <ContextMenuDropdown position={policyMenu.state?.position ?? null} opened={!!policyMenu.state} onClose={policyMenu.close}>
        <Menu.Label>{policyMenu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconPlayerPlay size={16} />} onClick={() => policyMenu.state && runNow(policyMenu.state.data)}>
          Jetzt ausfuehren
        </Menu.Item>
        <Menu.Item leftSection={<IconEdit size={16} />} onClick={() => policyMenu.state && openEdit(policyMenu.state.data)}>
          Bearbeiten
        </Menu.Item>
        <Menu.Item leftSection={<IconTerminal2 size={16} />} onClick={() => policyMenu.state && showLog(policyMenu.state.data.id)}>
          Log anzeigen
        </Menu.Item>
        <Menu.Divider />
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={() => policyMenu.state && removePolicy(policyMenu.state.data)}>
          Loeschen
        </Menu.Item>
      </ContextMenuDropdown>

      <Drawer opened={logsOpened} onClose={closeLogs} position="bottom" size="45%" title="Job-Log">
        <LogViewer context={logContext} />
      </Drawer>

      <PolicyFormModal opened={formOpen} onClose={() => setFormOpen(false)} policy={editingPolicy} />
    </Stack>
  );
}
