import { useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Paper,
  PasswordInput,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconEdit, IconInfoCircle, IconKey, IconPlus, IconRefresh, IconTrash, IconUserPlus } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useDeleteHyperVCluster,
  useDeleteSchedule,
  useDeleteSnapMirrorLabel,
  useHyperVClusters,
  useNetAppClusters,
  useNetAppSchedules,
  useSchedules,
  useSnapmirrorPolicies,
  useSnapMirrorLabels,
  useSvms,
  useVerifyHyperVCluster,
} from "@/api/hooks";
import { useCreateUser, usePublicSettings, useRoles, useUpdateUserPassword, useUsers, type UserRead } from "@/api/hooks.settings";
import { HyperVClusterFormModal } from "@/components/HyperVClusterFormModal";
import { NetAppScheduleFormModal } from "@/components/NetAppScheduleFormModal";
import { ProcessModal, type ProcessPlan } from "@/components/ProcessModal";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import { SnapMirrorLabelFormModal } from "@/components/SnapMirrorLabelFormModal";
import { SnapMirrorPolicyEditModal } from "@/components/SnapMirrorPolicyEditModal";
import { SnapMirrorPolicyFormModal } from "@/components/SnapMirrorPolicyFormModal";
import type { HyperVCluster, NetAppSnapMirrorPolicy, Schedule, SnapMirrorLabel } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatSchedule } from "@/utils/format";
import { buildHyperVClusterCreationSteps } from "@/utils/hypervSteps";
import { buildPolicyCreationSteps, buildPolicyEditSteps, buildScheduleCreationSteps } from "@/utils/netappSteps";

const SCHEDULE_TYPE_LABEL: Record<string, string> = {
  hourly: "Mehrmals täglich",
  daily: "Täglich",
  weekly: "Wöchentlich",
  monthly: "Monatlich",
};

const HYPERV_HEALTH_COLOR: Record<string, string> = { healthy: "green", degraded: "yellow", unreachable: "red", unknown: "gray" };
const HYPERV_HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Eingeschränkt",
  unreachable: "Nicht erreichbar",
  unknown: "Unbekannt",
};

function ConfigRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Group justify="space-between">
      <Text c="dimmed" size="sm">
        {label}
      </Text>
      <Text size="sm" fw={600}>
        {value}
      </Text>
    </Group>
  );
}

function CreateUserModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const { data: roles } = useRoles();
  const createUser = useCreateUser();
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [roleId, setRoleId] = useState<string | null>(null);

  function reset() {
    setUsername("");
    setDisplayName("");
    setEmail("");
    setPassword("");
    setRoleId(null);
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    createUser.mutate(
      { username, display_name: displayName, email, password, role_id: roleId },
      {
        onSuccess: () => {
          notifications.show({ title: "Benutzer angelegt", message: `'${username}' wurde erstellt.`, color: "green" });
          handleClose();
        },
        onError: (err) => {
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Benutzer konnte nicht angelegt werden."), color: "red" });
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={handleClose} title="Benutzer hinzufügen">
      <Stack>
        <TextInput label="Benutzername" required value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
        <TextInput label="Anzeigename" value={displayName} onChange={(e) => setDisplayName(e.currentTarget.value)} />
        <TextInput label="E-Mail" type="email" value={email} onChange={(e) => setEmail(e.currentTarget.value)} />
        <PasswordInput
          label="Kennwort"
          required
          description="Mindestens 8 Zeichen"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Select
          label="Rolle"
          placeholder="Keine Rolle zuweisen"
          data={roles?.map((r) => ({ value: r.id, label: r.name })) ?? []}
          value={roleId}
          onChange={setRoleId}
          clearable
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createUser.isPending} disabled={!username || password.length < 8}>
            Anlegen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ChangePasswordModal({ user, onClose }: { user: UserRead | null; onClose: () => void }) {
  const updatePassword = useUpdateUserPassword();
  const [password, setPassword] = useState("");

  function handleClose() {
    setPassword("");
    onClose();
  }

  function handleSubmit() {
    if (!user) return;
    updatePassword.mutate(
      { userId: user.id, password },
      {
        onSuccess: () => {
          notifications.show({ title: "Kennwort geändert", message: `Kennwort für '${user.username}' wurde aktualisiert.`, color: "green" });
          handleClose();
        },
        onError: (err) => {
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Kennwort konnte nicht geändert werden."), color: "red" });
        },
      },
    );
  }

  return (
    <Modal opened={!!user} onClose={handleClose} title={`Kennwort ändern: ${user?.username ?? ""}`}>
      <Stack>
        <PasswordInput
          label="Neues Kennwort"
          required
          description="Mindestens 8 Zeichen"
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={updatePassword.isPending} disabled={password.length < 8}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") ?? "users";
  const { data: users } = useUsers();
  const { data: roles } = useRoles();
  const { data: settings } = usePublicSettings();
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const [passwordModalUser, setPasswordModalUser] = useState<UserRead | null>(null);
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
    if (!window.confirm(`Zeitplan '${schedule.name}' wirklich löschen?`)) return;
    deleteSchedule.mutate(schedule.id, {
      onSuccess: () => notifications.show({ title: "Zeitplan gelöscht", message: schedule.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zeitplan konnte nicht gelöscht werden."), color: "red" }),
    });
  }

  const { data: labels } = useSnapMirrorLabels();
  const deleteLabel = useDeleteSnapMirrorLabel();
  const [labelModalOpen, setLabelModalOpen] = useState(false);
  const [editingLabel, setEditingLabel] = useState<SnapMirrorLabel | null>(null);

  function openCreateLabel() {
    setEditingLabel(null);
    setLabelModalOpen(true);
  }

  function openEditLabel(label: SnapMirrorLabel) {
    setEditingLabel(label);
    setLabelModalOpen(true);
  }

  function removeLabel(label: SnapMirrorLabel) {
    if (!window.confirm(`Label '${label.name}' wirklich löschen?`)) return;
    deleteLabel.mutate(label.id, {
      onSuccess: () => notifications.show({ title: "Label gelöscht", message: label.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Label konnte nicht gelöscht werden."), color: "red" }),
    });
  }

  const { data: hyperVClusters } = useHyperVClusters();
  const verifyHyperVCluster = useVerifyHyperVCluster();
  const deleteHyperVCluster = useDeleteHyperVCluster();
  const [hyperVFormOpen, setHyperVFormOpen] = useState(false);

  function handleVerifyHyperVCluster(cluster: HyperVCluster) {
    verifyHyperVCluster.mutate(cluster.id, {
      onSuccess: (c) =>
        notifications.show({
          title: "Verbindung geprüft",
          message: `${c.name}: ${HYPERV_HEALTH_LABEL[c.health]}`,
          color: c.health === "healthy" ? "green" : "orange",
        }),
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Prüfung fehlgeschlagen."), color: "red" }),
    });
  }

  function handleDeleteHyperVCluster(cluster: HyperVCluster) {
    if (!window.confirm(`Hyper-V-Cluster '${cluster.name}' wirklich entfernen? Die gespeicherten Zugangsdaten werden gelöscht.`)) return;
    deleteHyperVCluster.mutate(cluster.id, {
      onSuccess: () => notifications.show({ title: "Cluster entfernt", message: cluster.name, color: "blue" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Cluster konnte nicht entfernt werden."), color: "red" }),
    });
  }

  const { data: netappClusters } = useNetAppClusters();
  const { data: netappSvms } = useSvms();
  const { data: netappPolicies } = useSnapmirrorPolicies();
  const { data: netappSchedules } = useNetAppSchedules();
  const [policyFormOpen, setPolicyFormOpen] = useState(false);
  const [policyEditOpen, setPolicyEditOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<NetAppSnapMirrorPolicy | null>(null);
  const [netappScheduleFormOpen, setNetappScheduleFormOpen] = useState(false);
  const [process, setProcess] = useState<ProcessPlan | null>(null);

  return (
    <Stack>
      <Title order={3}>Einstellungen</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "users" })}>
        <Tabs.List>
          <Tabs.Tab value="users">Benutzer & Rollen</Tabs.Tab>
          <Tabs.Tab value="schedules">Zeitpläne</Tabs.Tab>
          <Tabs.Tab value="snapmirror-labels">SnapMirror-Labels</Tabs.Tab>
          <Tabs.Tab value="netapp-snapmirror-policies">SnapMirror-Policies</Tabs.Tab>
          <Tabs.Tab value="netapp-schedules">Schedules</Tabs.Tab>
          <Tabs.Tab value="ad">Active Directory</Tabs.Tab>
          <Tabs.Tab value="netapp">NetApp-Verbindung</Tabs.Tab>
          <Tabs.Tab value="hyperv">Hyper-V-Hosts</Tabs.Tab>
          <Tabs.Tab value="updates">Updates (Git)</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="users" pt="md">
          <Stack>
            <Paper p="md">
              <Group justify="space-between" mb="sm">
                <Title order={5}>Benutzer</Title>
                <Button leftSection={<IconUserPlus size={16} />} onClick={() => setCreateModalOpen(true)}>
                  Benutzer hinzufügen
                </Button>
              </Group>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Benutzername</Table.Th>
                    <Table.Th>Anzeigename</Table.Th>
                    <Table.Th>Quelle</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Letzte Anmeldung</Table.Th>
                    <Table.Th>Aktionen</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {users?.map((u) => (
                    <Table.Tr key={u.id}>
                      <Table.Td>{u.username}</Table.Td>
                      <Table.Td>{u.display_name || "-"}</Table.Td>
                      <Table.Td>
                        <Badge variant="light" color={u.source === "active_directory" ? "grape" : "blue"}>
                          {u.source === "active_directory" ? "Active Directory" : "Lokal"}
                        </Badge>
                      </Table.Td>
                      <Table.Td>
                        <Badge color={u.is_active ? "green" : "gray"} variant="light">
                          {u.is_active ? "aktiv" : "deaktiviert"}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{u.last_login_at ? new Date(u.last_login_at).toLocaleString("de-DE") : "nie"}</Table.Td>
                      <Table.Td>
                        <Tooltip label={u.source === "active_directory" ? "AD-Benutzer verwalten ihr Kennwort selbst" : "Kennwort ändern"}>
                          <ActionIcon
                            variant="light"
                            disabled={u.source === "active_directory"}
                            onClick={() => setPasswordModalUser(u)}
                          >
                            <IconKey size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>

            <CreateUserModal opened={createModalOpen} onClose={() => setCreateModalOpen(false)} />
            <ChangePasswordModal user={passwordModalUser} onClose={() => setPasswordModalUser(null)} />

            <Paper p="md">
              <Title order={5} mb="sm">
                Rollen
              </Title>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Rolle</Table.Th>
                    <Table.Th>Beschreibung</Table.Th>
                    <Table.Th>Berechtigungen</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {roles?.map((r) => (
                    <Table.Tr key={r.id}>
                      <Table.Td>{r.name}</Table.Td>
                      <Table.Td>{r.description}</Table.Td>
                      <Table.Td>{r.permissions.length}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>
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

          <ScheduleFormModal
            opened={scheduleModalOpen}
            onClose={() => setScheduleModalOpen(false)}
            schedule={editingSchedule}
          />
        </Tabs.Panel>

        <Tabs.Panel value="snapmirror-labels" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>SnapMirror-Labels</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={openCreateLabel}>
                Label erstellen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {labels?.map((l) => (
                  <Table.Tr key={l.id}>
                    <Table.Td>{l.name}</Table.Td>
                    <Table.Td>
                      <Group gap="xs">
                        <ActionIcon variant="light" onClick={() => openEditLabel(l)}>
                          <IconEdit size={16} />
                        </ActionIcon>
                        <ActionIcon variant="light" color="red" onClick={() => removeLabel(l)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {labels?.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine Labels angelegt.
              </Text>
            )}
          </Paper>

          <SnapMirrorLabelFormModal opened={labelModalOpen} onClose={() => setLabelModalOpen(false)} label={editingLabel} />
        </Tabs.Panel>

        <Tabs.Panel value="netapp-snapmirror-policies" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>NetApp SnapMirror-Policies</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={() => setPolicyFormOpen(true)}>
                Policy anlegen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Cluster</Table.Th>
                  <Table.Th>SVM</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Typ</Table.Th>
                  <Table.Th>Regeln</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {netappPolicies?.map((p) => (
                  <Table.Tr key={p.id}>
                    <Table.Td>{p.cluster_name}</Table.Td>
                    <Table.Td>{p.svm_name ?? "-"}</Table.Td>
                    <Table.Td>{p.name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color="blue">
                        {p.type ?? "-"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {p.rules.length ? p.rules.map((r) => `${r.label}: ${r.count}`).join(", ") : "-"}
                    </Table.Td>
                    <Table.Td>
                      <ActionIcon
                        variant="light"
                        onClick={() => {
                          setEditingPolicy(p);
                          setPolicyEditOpen(true);
                        }}
                      >
                        <IconEdit size={16} />
                      </ActionIcon>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {netappPolicies?.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine SnapMirror-Policies erkannt. Führe eine Discovery unter Storage &gt; Cluster aus.
              </Text>
            )}
          </Paper>

          <SnapMirrorPolicyFormModal
            opened={policyFormOpen}
            onClose={() => setPolicyFormOpen(false)}
            clusters={netappClusters}
            svms={netappSvms}
            onSubmitPlan={(plan) => {
              setPolicyFormOpen(false);
              setProcess({ title: "SnapMirror-Policy anlegen", steps: buildPolicyCreationSteps(plan) });
            }}
          />
          <SnapMirrorPolicyEditModal
            opened={policyEditOpen}
            onClose={() => setPolicyEditOpen(false)}
            policy={editingPolicy}
            onSubmitPlan={(plan) => {
              setPolicyEditOpen(false);
              setProcess({ title: "SnapMirror-Policy bearbeiten", steps: buildPolicyEditSteps(plan) });
            }}
          />
        </Tabs.Panel>

        <Tabs.Panel value="netapp-schedules" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>NetApp-Schedules</Title>
              <Button leftSection={<IconPlus size={16} />} onClick={() => setNetappScheduleFormOpen(true)}>
                Schedule anlegen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Cluster</Table.Th>
                  <Table.Th>SVM</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Minuten</Table.Th>
                  <Table.Th>Stunden</Table.Th>
                  <Table.Th>Wochentage</Table.Th>
                  <Table.Th>Tage</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {netappSchedules?.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td>{s.cluster_name}</Table.Td>
                    <Table.Td>{s.svm_name ?? "cluster-weit"}</Table.Td>
                    <Table.Td>{s.name}</Table.Td>
                    <Table.Td>{s.minutes.join(", ") || "-"}</Table.Td>
                    <Table.Td>{s.hours.join(", ") || "jede"}</Table.Td>
                    <Table.Td>{s.weekdays.join(", ") || "jeder"}</Table.Td>
                    <Table.Td>{s.days.join(", ") || "jeder"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {netappSchedules?.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine Schedules erkannt. Führe eine Discovery unter Storage &gt; Cluster aus.
              </Text>
            )}
          </Paper>

          <NetAppScheduleFormModal
            opened={netappScheduleFormOpen}
            onClose={() => setNetappScheduleFormOpen(false)}
            clusters={netappClusters}
            svms={netappSvms}
            onSubmitPlan={(plan) => {
              setNetappScheduleFormOpen(false);
              setProcess({ title: "Schedule anlegen", steps: buildScheduleCreationSteps(plan) });
            }}
          />
        </Tabs.Panel>

        <Tabs.Panel value="ad" pt="md">
          <Paper p="md" maw={520}>
            <Stack gap="xs">
              <ConfigRow label="AD-Integration aktiv" value={settings?.ad_enabled ? "Ja" : "Nein"} />
              <ConfigRow label="Domain Controller" value={settings?.ad_server || "-"} />
              <ConfigRow label="Domaene" value={settings?.ad_domain || "-"} />
              <ConfigRow label="Base DN" value={settings?.ad_base_dn || "-"} />
            </Stack>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="netapp" pt="md">
          <Paper p="md" maw={520}>
            <Stack gap="xs">
              <ConfigRow label="Cluster-Management-LIF" value={settings?.ontap_cluster_mgmt_lif || "-"} />
              <ConfigRow label="SSL-Verifizierung" value={settings?.ontap_verify_ssl ? "Ja" : "Nein"} />
              <ConfigRow label="MetroCluster" value={settings?.ontap_is_metrocluster ? "Ja" : "Nein"} />
            </Stack>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="hyperv" pt="md">
          <Stack>
            <Paper p="md">
              <Group justify="space-between" mb="sm">
                <Title order={5}>Hyper-V-Cluster</Title>
                <Button leftSection={<IconPlus size={16} />} onClick={() => setHyperVFormOpen(true)}>
                  Hyper-V-Cluster hinzufügen
                </Button>
              </Group>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Name</Table.Th>
                    <Table.Th>IP-Adresse</Table.Th>
                    <Table.Th>Transport</Table.Th>
                    <Table.Th>Benutzer</Table.Th>
                    <Table.Th>Cluster-Name</Table.Th>
                    <Table.Th>Health</Table.Th>
                    <Table.Th>Letzte Prüfung</Table.Th>
                    <Table.Th>Aktionen</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {hyperVClusters?.map((c) => (
                    <Table.Tr key={c.id}>
                      <Table.Td>{c.name}</Table.Td>
                      <Table.Td>{c.management_address}</Table.Td>
                      <Table.Td>
                        <Badge variant="light" color={c.use_https ? "indigo" : "gray"}>
                          {c.use_https ? "HTTPS" : "HTTP"}
                        </Badge>
                      </Table.Td>
                      <Table.Td>{c.username}</Table.Td>
                      <Table.Td>{c.hyperv_cluster_name ?? "-"}</Table.Td>
                      <Table.Td>
                        <Tooltip label={c.last_check_error ?? ""} disabled={!c.last_check_error}>
                          <Badge color={HYPERV_HEALTH_COLOR[c.health]} variant="light">
                            {HYPERV_HEALTH_LABEL[c.health]}
                            {c.node_count > 0 ? ` (${c.healthy_node_count}/${c.node_count} Knoten)` : ""}
                          </Badge>
                        </Tooltip>
                      </Table.Td>
                      <Table.Td>{c.last_checked_at ? new Date(c.last_checked_at).toLocaleString("de-DE") : "nie"}</Table.Td>
                      <Table.Td>
                        <Group gap="xs">
                          <ActionIcon variant="light" onClick={() => handleVerifyHyperVCluster(c)}>
                            <IconRefresh size={16} />
                          </ActionIcon>
                          <ActionIcon variant="light" color="red" onClick={() => handleDeleteHyperVCluster(c)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {hyperVClusters?.length === 0 && (
                <Text c="dimmed" size="sm" ta="center" py="md">
                  Noch keine Hyper-V-Cluster hinzugefügt.
                </Text>
              )}
            </Paper>

            <HyperVClusterFormModal
              opened={hyperVFormOpen}
              onClose={() => setHyperVFormOpen(false)}
              onSubmitPlan={(plan) => {
                setHyperVFormOpen(false);
                setProcess({ title: "Hyper-V-Cluster hinzufügen", steps: buildHyperVClusterCreationSteps(plan) });
              }}
            />

            <Paper p="md" maw={520}>
              <Title order={5} mb="sm">
                Globale WinRM-Einstellungen
              </Title>
              <Stack gap="xs">
                <ConfigRow label="WinRM-Transport" value={settings?.winrm_transport ?? "-"} />
                <ConfigRow label="WinRM ueber HTTPS" value={settings?.winrm_use_https ? "Ja" : "Nein"} />
                <ConfigRow label="WinRM-Port" value={settings?.winrm_port ?? "-"} />
              </Stack>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="updates" pt="md">
          <Paper p="md" maw={520}>
            <Stack gap="xs">
              <ConfigRow label="Git-Repository" value={settings?.git_repo_url || "nicht konfiguriert"} />
              <ConfigRow label="Branch" value={settings?.git_branch ?? "-"} />
              <ConfigRow label="Auto-Update aktiv" value={settings?.auto_update_enabled ? "Ja" : "Nein"} />
              <ConfigRow label="Intervall (Minuten)" value={settings?.auto_update_interval_minutes ?? "-"} />
            </Stack>
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <ProcessModal opened={!!process} onClose={() => setProcess(null)} plan={process} />

      <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
        Die Tabs "Active Directory", "NetApp-Verbindung" und "Updates (Git)" zeigen die aktuell aktive
        Server-Konfiguration (aus Umgebungsvariablen/.env) nur an. Bearbeitung direkt aus der GUI folgt fuer diese
        Bereiche in einer kommenden Iteration. Die globalen WinRM-Einstellungen unter "Hyper-V-Hosts" gelten ebenso
        nur zur Anzeige -- sie werden fuer alle registrierten Hyper-V-Cluster verwendet.
      </Alert>
    </Stack>
  );
}
