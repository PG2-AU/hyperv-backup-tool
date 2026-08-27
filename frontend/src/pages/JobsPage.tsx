import { useState } from "react";
import { ActionIcon, Badge, Button, Drawer, Group, Menu, Paper, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlayerPlay, IconPlus, IconStack2, IconTerminal2, IconTrash } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useDeletePolicy,
  useDeleteResourceGroup,
  useDeleteSchedule,
  useJobRuns,
  usePolicies,
  useResourceGroups,
  useSchedules,
} from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { LogViewer } from "@/components/LogViewer";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import { PolicyPickerModal } from "@/components/PolicyPickerModal";
import { ResourceGroupFormModal } from "@/components/ResourceGroupFormModal";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import type { BackupJobRun, BackupPolicy, JobStatus, ResourceGroup, Schedule } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatRetention, formatSchedule } from "@/utils/format";
import { useRunPolicy } from "@/utils/runPolicy";

const STATUS_COLOR: Record<JobStatus, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
};

const SCOPE_LABEL: Record<string, string> = { vm: "VMs", csv: "CSVs", lun: "LUNs" };

const SCHEDULE_TYPE_LABEL: Record<string, string> = {
  hourly: "Mehrmals täglich",
  daily: "Täglich",
  weekly: "Wöchentlich",
  monthly: "Monatlich",
};

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const activeTab = tabParam === "runs" || tabParam === "protection-groups" || tabParam === "schedules" ? tabParam : "policies";

  const { data: policies } = usePolicies();
  const { data: runs } = useJobRuns();
  const { runOrPick, runPolicy, pickerPolicies, closePicker } = useRunPolicy();
  const deletePolicy = useDeletePolicy();
  const policyMenu = useContextMenu<BackupPolicy>();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);
  const [formOpen, setFormOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<BackupPolicy | null>(null);
  const [snapshotsOpened, { open: openSnapshots, close: closeSnapshots }] = useDisclosure(false);
  const [selectedRun, setSelectedRun] = useState<BackupJobRun | null>(null);

  const { data: groups } = useResourceGroups();
  const deleteGroup = useDeleteResourceGroup();
  const groupMenu = useContextMenu<ResourceGroup>();
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<ResourceGroup | null>(null);

  const { data: schedules } = useSchedules();
  const deleteSchedule = useDeleteSchedule();
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);

  function openCreateSchedule() {
    setEditingSchedule(null);
    setScheduleModalOpen(true);
  }

  function openEditSchedule(schedule: Schedule) {
    setEditingSchedule(schedule);
    setScheduleModalOpen(true);
  }

  function removeSchedule(schedule: Schedule) {
    confirmAction({
      title: "Zeitplan löschen",
      message: `Zeitplan '${schedule.name}' wirklich löschen?`,
      confirmLabel: "Löschen",
      onConfirm: () =>
        deleteSchedule.mutate(schedule.id, {
          onSuccess: () => notifications.show({ title: "Zeitplan gelöscht", message: schedule.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zeitplan konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  function runNow(policy: BackupPolicy) {
    runPolicy(policy);
  }

  function runGroupNow(group: ResourceGroup) {
    runOrPick(group.policies);
  }

  function showSnapshots(run: BackupJobRun) {
    setSelectedRun(run);
    openSnapshots();
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
    confirmAction({
      title: "Backup-Policy löschen",
      message: `Backup-Policy '${policy.name}' wirklich löschen?`,
      confirmLabel: "Löschen",
      onConfirm: () =>
        deletePolicy.mutate(policy.id, {
          onSuccess: () => notifications.show({ title: "Policy gelöscht", message: policy.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Policy konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  function showLog(jobId: string | undefined) {
    setLogContext(jobId);
    openLogs();
  }

  function openCreateGroup() {
    setEditingGroup(null);
    setGroupFormOpen(true);
  }

  function openEditGroup(group: ResourceGroup) {
    setEditingGroup(group);
    setGroupFormOpen(true);
  }

  function removeGroup(group: ResourceGroup) {
    confirmAction({
      title: "Protection Group löschen",
      message: `Protection Group '${group.name}' wirklich löschen?`,
      confirmLabel: "Löschen",
      onConfirm: () =>
        deleteGroup.mutate(group.id, {
          onSuccess: () => notifications.show({ title: "Protection Group gelöscht", message: group.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Protection Group konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  return (
    <Stack>
      <Title order={3}>Backup</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "policies" })}>
        <Tabs.List>
          <Tabs.Tab value="policies">Policies</Tabs.Tab>
          <Tabs.Tab value="protection-groups">Protection Groups</Tabs.Tab>
          <Tabs.Tab value="schedules">Zeitpläne</Tabs.Tab>
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

        <Tabs.Panel value="protection-groups" pt="md">
          <Stack>
            <Button leftSection={<IconPlus size={16} />} onClick={openCreateGroup} style={{ alignSelf: "flex-end" }}>
              Protection Group anlegen
            </Button>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Typ</Table.Th>
                  <Table.Th>Anzahl</Table.Th>
                  <Table.Th>Objekte</Table.Th>
                  <Table.Th>Verknüpfte Policies</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {groups?.map((group) => (
                  <Table.Tr key={group.id} onContextMenu={(e) => groupMenu.open(e, group)} style={{ cursor: "context-menu" }}>
                    <Table.Td>{group.name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color="blue">
                        {SCOPE_LABEL[group.scope] ?? group.scope}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="filled" color="gray">
                        {group.members.length}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{group.members.length ? group.members.join(", ") : "-"}</Table.Td>
                    <Table.Td>
                      {group.policies.length ? (
                        <Group gap={4}>
                          {group.policies.map((p) => (
                            <Badge key={p.id} color="indigo" variant="light">
                              {p.name}
                            </Badge>
                          ))}
                        </Group>
                      ) : (
                        <Text c="dimmed" size="sm">
                          keine
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {groups?.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine Protection Groups angelegt.
              </Text>
            )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="schedules" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>Zeitpläne</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={openCreateSchedule}>
                Zeitplan erstellen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Typ</Table.Th>
                  <Table.Th>Details</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {schedules?.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td>{s.name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color="blue">
                        {SCHEDULE_TYPE_LABEL[s.schedule_type] ?? s.schedule_type}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{formatSchedule(s)}</Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <ActionIcon variant="light" onClick={() => openEditSchedule(s)}>
                          <IconEdit size={16} />
                        </ActionIcon>
                        <ActionIcon variant="light" color="red" onClick={() => removeSchedule(s)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {schedules?.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine Zeitpläne angelegt.
              </Text>
            )}
          </Paper>
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
                    <Group gap={4} wrap="nowrap">
                      <Button size="xs" variant="subtle" leftSection={<IconStack2 size={14} />} onClick={() => showSnapshots(run)}>
                        Snapshots
                      </Button>
                      <Button
                        size="xs"
                        variant="subtle"
                        leftSection={<IconTerminal2 size={14} />}
                        onClick={() => showLog(run.job_id ?? undefined)}
                      >
                        Log
                      </Button>
                    </Group>
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

      <ContextMenuDropdown position={groupMenu.state?.position ?? null} opened={!!groupMenu.state} onClose={groupMenu.close}>
        <Menu.Label>{groupMenu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconPlayerPlay size={16} />} onClick={() => groupMenu.state && runGroupNow(groupMenu.state.data)}>
          Jetzt ausfuehren
        </Menu.Item>
        <Menu.Item leftSection={<IconEdit size={16} />} onClick={() => groupMenu.state && openEditGroup(groupMenu.state.data)}>
          Bearbeiten
        </Menu.Item>
        <Menu.Item color="red" leftSection={<IconTrash size={16} />} onClick={() => groupMenu.state && removeGroup(groupMenu.state.data)}>
          Loeschen
        </Menu.Item>
      </ContextMenuDropdown>

      <Drawer opened={logsOpened} onClose={closeLogs} position="bottom" size="45%" title="Job-Log">
        <LogViewer context={logContext} />
      </Drawer>

      <Drawer
        opened={snapshotsOpened}
        onClose={closeSnapshots}
        position="right"
        size="lg"
        title={selectedRun ? `Snapshots: ${selectedRun.job_name}` : "Snapshots"}
      >
        <Stack>
          {selectedRun?.snapshots.length === 0 && (
            <Text c="dimmed" size="sm">
              Fuer diesen Lauf wurden keine Snapshots erstellt.
            </Text>
          )}
          {selectedRun?.snapshots.map((snap) => (
            <Stack key={snap.id} gap={4} p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
              <Group justify="space-between">
                <Text fw={600} size="sm">
                  {snap.volume_name ?? "?"}
                </Text>
                <Badge color={snap.success ? "green" : "red"} variant="light">
                  {snap.success ? "erfolgreich" : "fehlgeschlagen"}
                </Badge>
              </Group>
              <Text size="xs" c="dimmed">
                Cluster: {snap.netapp_cluster_name ?? "-"} / SVM: {snap.svm_name ?? "-"}
              </Text>
              <Text size="xs">CSVs: {snap.csv_names.length ? snap.csv_names.join(", ") : "-"}</Text>
              <Text size="xs">LUNs: {snap.lun_names.length ? snap.lun_names.join(", ") : "-"}</Text>
              <Text size="xs">VMs: {snap.vm_names.length ? snap.vm_names.join(", ") : "-"}</Text>
              {snap.snapshot_name && (
                <Text size="xs" ff="monospace">
                  Snapshot: {snap.snapshot_name}
                </Text>
              )}
              {snap.error_message && (
                <Text size="xs" c="red">
                  {snap.error_message}
                </Text>
              )}
            </Stack>
          ))}
        </Stack>
      </Drawer>

      <PolicyFormModal opened={formOpen} onClose={() => setFormOpen(false)} policy={editingPolicy} />
      <ResourceGroupFormModal opened={groupFormOpen} onClose={() => setGroupFormOpen(false)} group={editingGroup} />
      <ScheduleFormModal opened={scheduleModalOpen} onClose={() => setScheduleModalOpen(false)} schedule={editingSchedule} />
      <PolicyPickerModal opened={!!pickerPolicies} onClose={closePicker} policies={pickerPolicies ?? []} onPick={runPolicy} />
    </Stack>
  );
}
