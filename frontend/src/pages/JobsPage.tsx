import { useState } from "react";
import { Badge, Button, Drawer, Group, Menu, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconPlayerPlay, IconPlus, IconTerminal2, IconTrash } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useDeletePolicy, useDeleteResourceGroup, useJobRuns, usePolicies, useResourceGroups, useTriggerJobRun } from "@/api/hooks";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { LogViewer } from "@/components/LogViewer";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import { ResourceGroupFormModal } from "@/components/ResourceGroupFormModal";
import type { BackupPolicy, JobStatus, ResourceGroup } from "@/api/types";
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

const SCOPE_LABEL: Record<string, string> = { vm: "VMs", csv: "CSVs", lun: "LUNs" };

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const activeTab = tabParam === "runs" || tabParam === "protection-groups" ? tabParam : "policies";

  const { data: policies } = usePolicies();
  const { data: runs } = useJobRuns();
  const triggerRun = useTriggerJobRun();
  const deletePolicy = useDeletePolicy();
  const policyMenu = useContextMenu<BackupPolicy>();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const [logContext, setLogContext] = useState<string | undefined>(undefined);
  const [formOpen, setFormOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<BackupPolicy | null>(null);

  const { data: groups } = useResourceGroups();
  const deleteGroup = useDeleteResourceGroup();
  const groupMenu = useContextMenu<ResourceGroup>();
  const [groupFormOpen, setGroupFormOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<ResourceGroup | null>(null);

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

  function openCreateGroup() {
    setEditingGroup(null);
    setGroupFormOpen(true);
  }

  function openEditGroup(group: ResourceGroup) {
    setEditingGroup(group);
    setGroupFormOpen(true);
  }

  function removeGroup(group: ResourceGroup) {
    if (!window.confirm(`Protection Group '${group.name}' wirklich löschen?`)) return;
    deleteGroup.mutate(group.id, {
      onSuccess: () => notifications.show({ title: "Protection Group gelöscht", message: group.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Protection Group konnte nicht gelöscht werden."), color: "red" }),
    });
  }

  return (
    <Stack>
      <Title order={3}>Backup</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "policies" })}>
        <Tabs.List>
          <Tabs.Tab value="policies">Policies</Tabs.Tab>
          <Tabs.Tab value="protection-groups">Protection Groups</Tabs.Tab>
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

      <ContextMenuDropdown position={groupMenu.state?.position ?? null} opened={!!groupMenu.state} onClose={groupMenu.close}>
        <Menu.Label>{groupMenu.state?.data.name}</Menu.Label>
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

      <PolicyFormModal opened={formOpen} onClose={() => setFormOpen(false)} policy={editingPolicy} />
      <ResourceGroupFormModal opened={groupFormOpen} onClose={() => setGroupFormOpen(false)} group={editingGroup} />
    </Stack>
  );
}
