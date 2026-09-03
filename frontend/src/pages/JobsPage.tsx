import { useState } from "react";
import { ActionIcon, Badge, Button, Drawer, Group, Paper, Stack, Table, Tabs, Text, Title, Tooltip } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconCopy, IconEdit, IconPlayerPlay, IconPlus, IconStack2, IconTerminal2, IconTrash, IconX } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useDeletePolicy,
  useDeleteResourceGroup,
  useDeleteSchedule,
  useJobRuns,
  usePolicies,
  useCancelJobRun,
  useResourceGroups,
  useSchedules,
  useTriggerJobRun,
} from "@/api/hooks";
import { BackupCalendarTab } from "@/components/BackupCalendarTab";
import { LogViewer } from "@/components/LogViewer";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import { PolicyPickerModal } from "@/components/PolicyPickerModal";
import { ResourceGroupFormModal } from "@/components/ResourceGroupFormModal";
import { ResourceGroupPickerModal } from "@/components/ResourceGroupPickerModal";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import type { BackupJobRun, BackupPolicy, JobStatus, ResourceGroup, Schedule } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatRetention, formatSchedule } from "@/utils/format";
import { memberDisplayName } from "@/utils/resourceGroupMember";
import { useRunPolicy } from "@/utils/runPolicy";

const STATUS_COLOR: Record<JobStatus, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  pending: "gray",
  cleaning_up: "yellow",
  cleaned_up_after_failure: "orange",
  cancelled: "gray",
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
  const activeTab =
    tabParam === "runs" || tabParam === "protection-groups" || tabParam === "schedules" || tabParam === "calendar"
      ? tabParam
      : "policies";

  const { data: policies } = usePolicies();
  const { data: runs } = useJobRuns();
  const { runOrPick, runPolicy, pickerPolicies, closePicker } = useRunPolicy();
  const triggerRun = useTriggerJobRun();
  const cancelRun = useCancelJobRun();
  const [groupPickerPolicy, setGroupPickerPolicy] = useState<BackupPolicy | null>(null);
  const deletePolicy = useDeletePolicy();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);
  const [logTitle, setLogTitle] = useState<string | undefined>(undefined);
  const [formOpen, setFormOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<BackupPolicy | null>(null);
  const [duplicateFromPolicy, setDuplicateFromPolicy] = useState<BackupPolicy | null>(null);
  const [snapshotsOpened, { open: openSnapshots, close: closeSnapshots }] = useDisclosure(false);
  const [selectedRun, setSelectedRun] = useState<BackupJobRun | null>(null);

  const { data: groups } = useResourceGroups();
  const deleteGroup = useDeleteResourceGroup();
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<ResourceGroup | null>(null);
  const [duplicateFromGroup, setDuplicateFromGroup] = useState<ResourceGroup | null>(null);

  const { data: schedules } = useSchedules();
  const deleteSchedule = useDeleteSchedule();
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [duplicateFromSchedule, setDuplicateFromSchedule] = useState<Schedule | null>(null);

  function openCreateSchedule() {
    setEditingSchedule(null);
    setDuplicateFromSchedule(null);
    setScheduleModalOpen(true);
  }

  function openEditSchedule(schedule: Schedule) {
    setDuplicateFromSchedule(null);
    setEditingSchedule(schedule);
    setScheduleModalOpen(true);
  }

  function openDuplicateSchedule(schedule: Schedule) {
    setEditingSchedule(null);
    setDuplicateFromSchedule(schedule);
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

  // Verknuepfte Protection Groups dieser Policy -- aus der bereits
  // geladenen Gruppenliste abgeleitet (jede Gruppe traegt ihre
  // verknuepften Policies), statt eines eigenen Backend-Aufrufs.
  function linkedGroupsOf(policy: BackupPolicy): ResourceGroup[] {
    return (groups ?? []).filter((g) => g.policies.some((p) => p.id === policy.id));
  }

  function runNow(policy: BackupPolicy) {
    const linkedGroups = linkedGroupsOf(policy);
    if (linkedGroups.length > 1) {
      setGroupPickerPolicy(policy);
      return;
    }
    runPolicy(policy);
  }

  function runNowForGroups(policy: BackupPolicy, groupIds: string[]) {
    triggerRun.mutate(
      { jobId: policy.id, resourceGroupId: groupIds },
      {
        onSuccess: (runs) => {
          setGroupPickerPolicy(null);
          // runs.length kann kleiner als groupIds.length sein, wenn fuer
          // einzelne Gruppen bereits ein Lauf dieser Policy aktiv war
          // (das Backend ueberspringt dann nur diese, siehe trigger_job_run) --
          // die tatsaechlich gestartete Anzahl ist daher aussagekraeftiger.
          const skipped = groupIds.length - runs.length;
          notifications.show({
            title: "Job gestartet",
            message:
              `${policy.name} läuft für ${runs.length} Protection Group(s) – Fortschritt siehe Kopfzeile.` +
              (skipped > 0 ? ` (${skipped} bereits laufend, übersprungen)` : ""),
            color: "blue",
          });
        },
        onError: (err) =>
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Job konnte nicht gestartet werden."), color: "red" }),
      },
    );
  }

  function runGroupNow(group: ResourceGroup) {
    runOrPick(group.policies);
  }

  function showSnapshots(run: BackupJobRun) {
    setSelectedRun(run);
    openSnapshots();
  }

  function cancelJob(run: BackupJobRun) {
    confirmAction({
      title: "Backup-Lauf abbrechen",
      message:
        `'${run.job_name}' wirklich abbrechen? Wird nach dem aktuellen Schritt gestoppt (kann je nach Schritt bis zu ` +
        "ca. 1 Minute dauern), bereits erstellte Checkpoints werden aufgeräumt. Bereits erstellte Snapshots bleiben gültig.",
      confirmLabel: "Abbrechen",
      color: "red",
      onConfirm: () =>
        cancelRun.mutate(run.id, {
          onSuccess: () => notifications.show({ title: "Abbruch angefordert", message: "", color: "orange" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Abbruch konnte nicht angefordert werden."), color: "red" }),
        }),
    });
  }

  function openCreate() {
    setEditingPolicy(null);
    setDuplicateFromPolicy(null);
    setFormOpen(true);
  }

  function openEdit(policy: BackupPolicy) {
    setDuplicateFromPolicy(null);
    setEditingPolicy(policy);
    setFormOpen(true);
  }

  function openDuplicatePolicy(policy: BackupPolicy) {
    setEditingPolicy(null);
    setDuplicateFromPolicy(policy);
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

  function showLog(jobId: string | undefined, title?: string) {
    setLogContext(jobId);
    setLogTitle(title);
    openLogs();
  }

  function openCreateGroup() {
    setEditingGroup(null);
    setDuplicateFromGroup(null);
    setGroupFormOpen(true);
  }

  function openEditGroup(group: ResourceGroup) {
    setDuplicateFromGroup(null);
    setEditingGroup(group);
    setGroupFormOpen(true);
  }

  function openDuplicateGroup(group: ResourceGroup) {
    setEditingGroup(null);
    setDuplicateFromGroup(group);
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
          <Tabs.Tab value="calendar">Kalender</Tabs.Tab>
          <Tabs.Tab value="runs">Job-Verlauf</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="policies" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>Policies</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={openCreate}>
                Neue Policy
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>App-konsistent</Table.Th>
                  <Table.Th>SnapMirror</Table.Th>
                  <Table.Th>Retention</Table.Th>
                  <Table.Th>Snapshot Locking</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {policies?.map((policy) => (
                  <Table.Tr key={policy.id}>
                    <Table.Td>{policy.name}</Table.Td>
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
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Tooltip label="Jetzt ausführen">
                          <ActionIcon variant="light" onClick={() => runNow(policy)}>
                            <IconPlayerPlay size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Bearbeiten">
                          <ActionIcon variant="light" onClick={() => openEdit(policy)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Duplizieren">
                          <ActionIcon variant="light" onClick={() => openDuplicatePolicy(policy)}>
                            <IconCopy size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Log anzeigen">
                          <ActionIcon variant="light" onClick={() => showLog(policy.id, policy.name)}>
                            <IconTerminal2 size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Löschen">
                          <ActionIcon variant="light" color="red" onClick={() => removePolicy(policy)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="protection-groups" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>Protection Groups</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={openCreateGroup}>
                Protection Group anlegen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Typ</Table.Th>
                  <Table.Th>Anzahl</Table.Th>
                  <Table.Th>Objekte</Table.Th>
                  <Table.Th>Verknüpfte Policies (Zeitplan)</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {groups?.map((group) => (
                  <Table.Tr key={group.id}>
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
                    <Table.Td>{group.members.length ? group.members.map(memberDisplayName).join(", ") : "-"}</Table.Td>
                    <Table.Td>
                      {group.policy_links.length ? (
                        <Group gap={4}>
                          {group.policy_links.map((link) => (
                            <Badge key={link.policy_id} color="indigo" variant="light">
                              {link.policy_name} ({formatSchedule(link.schedule)})
                            </Badge>
                          ))}
                        </Group>
                      ) : (
                        <Text c="dimmed" size="sm">
                          keine
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Tooltip label="Jetzt ausführen">
                          <ActionIcon variant="light" onClick={() => runGroupNow(group)}>
                            <IconPlayerPlay size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Bearbeiten">
                          <ActionIcon variant="light" onClick={() => openEditGroup(group)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Duplizieren">
                          <ActionIcon variant="light" onClick={() => openDuplicateGroup(group)}>
                            <IconCopy size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Löschen">
                          <ActionIcon variant="light" color="red" onClick={() => removeGroup(group)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
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
          </Paper>
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
                        <Tooltip label="Bearbeiten">
                          <ActionIcon variant="light" onClick={() => openEditSchedule(s)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Duplizieren">
                          <ActionIcon variant="light" onClick={() => openDuplicateSchedule(s)}>
                            <IconCopy size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Löschen">
                          <ActionIcon variant="light" color="red" onClick={() => removeSchedule(s)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
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

        <Tabs.Panel value="calendar" pt="md">
          <BackupCalendarTab />
        </Tabs.Panel>

        <Tabs.Panel value="runs" pt="md">
          <Paper p="md">
            <Title order={5} mb="sm">
              Job-Verlauf
            </Title>
            <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Protection Group</Table.Th>
                <Table.Th>Policy</Table.Th>
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
                  <Table.Td>
                    {/* Nur bei einem geplanten Lauf gesetzt -- ein manuelles
                    "Jetzt ausfuehren" auf der ganzen Policy betrifft
                    potenziell mehrere Resource Groups auf einmal, siehe
                    BackupJobRun.resource_group_name. */}
                    {run.resource_group_name ?? (
                      <Text span c="dimmed" size="sm">
                        Alle Gruppen
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{run.job_name}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Badge color={STATUS_COLOR[run.status]} variant="light">
                        {run.status}
                      </Badge>
                      {run.cancel_requested_at && run.status === "running" && (
                        <Tooltip label="Wird nach dem aktuellen Schritt gestoppt">
                          <Badge color="orange" variant="light">
                            Abbruch angefordert
                          </Badge>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>{new Date(run.started_at).toLocaleString("de-DE")}</Table.Td>
                  <Table.Td>{run.finished_at ? new Date(run.finished_at).toLocaleString("de-DE") : "-"}</Table.Td>
                  <Table.Td>{run.error_message ?? "-"}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Button size="xs" variant="subtle" leftSection={<IconStack2 size={14} />} onClick={() => showSnapshots(run)}>
                        Snapshots
                      </Button>
                      <Button size="xs" variant="subtle" leftSection={<IconTerminal2 size={14} />} onClick={() => showLog(run.id, run.job_name)}>
                        Log
                      </Button>
                      {run.status === "running" && !run.cancel_requested_at && (
                        <Button
                          size="xs"
                          variant="subtle"
                          color="red"
                          leftSection={<IconX size={14} />}
                          onClick={() => cancelJob(run)}
                        >
                          Abbrechen
                        </Button>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <Drawer
        opened={logsOpened}
        onClose={closeLogs}
        position="right"
        size="lg"
        title={logTitle ? `Log: ${logTitle}` : "Job-Log"}
      >
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

      <PolicyFormModal opened={formOpen} onClose={() => setFormOpen(false)} policy={editingPolicy} duplicateFrom={duplicateFromPolicy} />
      <ResourceGroupFormModal
        opened={groupFormOpen}
        onClose={() => setGroupFormOpen(false)}
        group={editingGroup}
        duplicateFrom={duplicateFromGroup}
      />
      <ScheduleFormModal
        opened={scheduleModalOpen}
        onClose={() => setScheduleModalOpen(false)}
        schedule={editingSchedule}
        duplicateFrom={duplicateFromSchedule}
      />
      <PolicyPickerModal opened={!!pickerPolicies} onClose={closePicker} policies={pickerPolicies ?? []} onPick={runPolicy} />
      <ResourceGroupPickerModal
        opened={!!groupPickerPolicy}
        onClose={() => setGroupPickerPolicy(null)}
        policyName={groupPickerPolicy?.name ?? ""}
        groups={groupPickerPolicy ? linkedGroupsOf(groupPickerPolicy) : []}
        onConfirm={(groupIds) => groupPickerPolicy && runNowForGroups(groupPickerPolicy, groupIds)}
        loading={triggerRun.isPending}
      />
    </Stack>
  );
}
