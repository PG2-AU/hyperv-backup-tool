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
import { IconEdit, IconInfoCircle, IconKey, IconPlus, IconRadar2, IconRefresh, IconTrash, IconUserPlus } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useDeleteHyperVCluster,
  useDeleteSnapMirrorLabel,
  useDiscoverHyperVCluster,
  useHyperVClusters,
  useNetAppClusters,
  useNetAppSchedules,
  useSnapmirrorPolicies,
  useSnapMirrorLabels,
  useSvms,
  useVerifyHyperVCluster,
} from "@/api/hooks";
import { useCreateUser, usePublicSettings, useRoles, useUpdateUserPassword, useUsers, type UserRead } from "@/api/hooks.settings";
import { DiscoveryModal } from "@/components/DiscoveryModal";
import { HyperVClusterFormModal } from "@/components/HyperVClusterFormModal";
import { NetAppScheduleFormModal } from "@/components/NetAppScheduleFormModal";
import { ProcessModal, type ProcessPlan } from "@/components/ProcessModal";
import { SnapMirrorLabelFormModal } from "@/components/SnapMirrorLabelFormModal";
import { SnapMirrorPolicyEditModal } from "@/components/SnapMirrorPolicyEditModal";
import { SnapMirrorPolicyFormModal } from "@/components/SnapMirrorPolicyFormModal";
import type { HyperVCluster, NetAppSnapMirrorPolicy, SnapMirrorLabel } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { buildHyperVClusterCreationSteps } from "@/utils/hypervSteps";
import { buildPolicyCreationSteps, buildPolicyEditSteps, buildScheduleCreationSteps } from "@/utils/netappSteps";

// ONTAP-REST kennt auf Policy-Ebene nur type=async/sync/continuous; die
// feinere Kategorie, die die ONTAP-CLI als "Type" zeigt (vault,
// mirror-vault, async-mirror, sync-mirror, strict-sync-mirror), liefert
// das Backend bereits abgeleitet im Feld display_type -- hier nur noch
// die Anzeigebeschriftung.
const SNAPMIRROR_POLICY_TYPE_LABEL: Record<string, string> = {
  vault: "Vault",
  mirror_vault: "Mirror-Vault",
  async_mirror: "Async-Mirror",
  sync_mirror: "Sync-Mirror",
  strict_sync_mirror: "Strict-Sync-Mirror",
  automated_failover_sync: "Automated-FailOver-Sync",
  continuous: "Continuous",
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
    confirmAction({
      title: "Label löschen",
      message: `Label '${label.name}' wirklich löschen?`,
      confirmLabel: "Löschen",
      onConfirm: () =>
        deleteLabel.mutate(label.id, {
          onSuccess: () => notifications.show({ title: "Label gelöscht", message: label.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Label konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  const { data: hyperVClusters } = useHyperVClusters();
  const verifyHyperVCluster = useVerifyHyperVCluster();
  const deleteHyperVCluster = useDeleteHyperVCluster();
  const discoverHyperVCluster = useDiscoverHyperVCluster();
  const [hyperVFormOpen, setHyperVFormOpen] = useState(false);
  const [hyperVDiscoveryOpen, setHyperVDiscoveryOpen] = useState(false);
  const [hyperVDiscoveryClusterName, setHyperVDiscoveryClusterName] = useState<string | undefined>(undefined);

  function runHyperVDiscovery(clusterId: string, clusterName: string) {
    setHyperVDiscoveryClusterName(clusterName);
    setHyperVDiscoveryOpen(true);
    discoverHyperVCluster.mutate(clusterId, {
      onError: (err) =>
        notifications.show({ title: "Discovery fehlgeschlagen", message: apiErrorMessage(err, "Unbekannter Fehler."), color: "red" }),
    });
  }

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
    confirmAction({
      title: "Hyper-V-Cluster entfernen",
      message: `Hyper-V-Cluster '${cluster.name}' wirklich entfernen? Die gespeicherten Zugangsdaten werden gelöscht.`,
      confirmLabel: "Entfernen",
      onConfirm: () =>
        deleteHyperVCluster.mutate(cluster.id, {
          onSuccess: () => notifications.show({ title: "Cluster entfernt", message: cluster.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Cluster konnte nicht entfernt werden."), color: "red" }),
        }),
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
      <Title order={3}>Settings</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "users" })}>
        <Tabs.List>
          <Tabs.Tab value="users">Benutzer & Rollen</Tabs.Tab>
          <Tabs.Tab value="snapmirror-labels">SnapMirror-Labels</Tabs.Tab>
          <Tabs.Tab value="netapp-snapmirror-policies">SnapMirror-Policies</Tabs.Tab>
          <Tabs.Tab value="netapp-schedules">Schedules</Tabs.Tab>
          <Tabs.Tab value="ad">Active Directory</Tabs.Tab>
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
              <Group gap="xs" mb={4}>
                <Title order={5}>Rollen</Title>
                <Badge color="gray" variant="light" size="sm">
                  Verwaltung noch nicht implementiert
                </Badge>
              </Group>
              <Text size="xs" c="dimmed" mb="sm">
                Nur Anzeige der vier Standardrollen. Eigene Rollen anlegen oder eine Zuweisung auf bestimmte Objekte (z.B. nur eine
                VM-Gruppe) einschränken ist noch nicht möglich -- jede Rollenzuweisung gilt aktuell immer global.
              </Text>
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
              <Title order={5}>SnapMirror-Policies</Title>
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
                        {p.display_type ? (SNAPMIRROR_POLICY_TYPE_LABEL[p.display_type] ?? p.display_type) : (p.type ?? "-")}
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
              <Title order={5}>Schedules</Title>
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
            <Group gap="xs" mb={4}>
              <Title order={5}>Active Directory</Title>
              <Badge color="gray" variant="light" size="sm">
                Noch nicht vollständig implementiert
              </Badge>
            </Group>
            <Text size="xs" c="dimmed" mb="sm">
              Die Anmeldung per LDAP-Bind funktioniert bereits, aber neu angelegte AD-Benutzer bekommen noch keine Rolle automatisch
              zugewiesen (kein Gruppe-zu-Rolle-Mapping) und müssten manuell berechtigt werden. Konfiguration erfolgt weiterhin nur
              über Server-Umgebungsvariablen, nicht über diese Seite.
            </Text>
            <Stack gap="xs">
              <ConfigRow label="AD-Integration aktiv" value={settings?.ad_enabled ? "Ja" : "Nein"} />
              <ConfigRow label="Domain Controller" value={settings?.ad_server || "-"} />
              <ConfigRow label="Domaene" value={settings?.ad_domain || "-"} />
              <ConfigRow label="Base DN" value={settings?.ad_base_dn || "-"} />
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
                          <ActionIcon variant="light" onClick={() => runHyperVDiscovery(c.id, c.name)}>
                            <IconRadar2 size={16} />
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
                let createdClusterId: string | null = null;
                setProcess({
                  title: "Hyper-V-Cluster hinzufügen",
                  steps: buildHyperVClusterCreationSteps(plan, (id) => {
                    createdClusterId = id;
                  }),
                  onSettled: (hasError) => {
                    if (!hasError && createdClusterId) runHyperVDiscovery(createdClusterId, plan.name);
                  },
                });
              }}
            />

            <DiscoveryModal
              opened={hyperVDiscoveryOpen}
              onClose={() => setHyperVDiscoveryOpen(false)}
              clusterName={hyperVDiscoveryClusterName}
              steps={discoverHyperVCluster.data}
              isLoading={discoverHyperVCluster.isPending}
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
        Die Tabs "Active Directory" und "Updates (Git)" zeigen die aktuell aktive
        Server-Konfiguration (aus Umgebungsvariablen/.env) nur an. Bearbeitung direkt aus der GUI folgt fuer diese
        Bereiche in einer kommenden Iteration. Die globalen WinRM-Einstellungen unter "Hyper-V-Hosts" gelten ebenso
        nur zur Anzeige -- sie werden fuer alle registrierten Hyper-V-Cluster verwendet.
      </Alert>
    </Stack>
  );
}
