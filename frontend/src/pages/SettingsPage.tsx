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
  Switch,
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
  useSnapMirrorLabels,
  useStorageAccess,
  useUpdateStorageAccess,
  useVerifyHyperVCluster,
} from "@/api/hooks";
import { useCreateUser, usePublicSettings, useRoles, useUpdateUserPassword, useUsers, type UserRead } from "@/api/hooks.settings";
import { DiscoveryModal } from "@/components/DiscoveryModal";
import { AlertSettingsTab } from "@/components/AlertSettingsTab";
import { EmailSettingsTab } from "@/components/EmailSettingsTab";
import { SchedulerConfigTab } from "@/components/SchedulerConfigTab";
import { HyperVClusterFormModal } from "@/components/HyperVClusterFormModal";
import { ProcessModal, type ProcessPlan } from "@/components/ProcessModal";
import { SnapMirrorLabelFormModal } from "@/components/SnapMirrorLabelFormModal";
import type { HyperVCluster, SnapMirrorLabel } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { buildHyperVClusterCreationSteps } from "@/utils/hypervSteps";
import { LOG_FONT_SIZE_OPTIONS, useDisplayStore, type ContentFontSize } from "@/store/displayStore";

const HYPERV_HEALTH_COLOR: Record<string, string> = { healthy: "green", degraded: "yellow", unreachable: "red", unknown: "gray" };
const HYPERV_HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Eingeschränkt",
  unreachable: "Nicht erreichbar",
  unknown: "Unbekannt",
};

function ConfigRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Group justify="space-between" wrap="nowrap" align="flex-start" gap="md">
      <Text c="dimmed" size="sm" style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Text size="sm" fw={600} ta="right" style={{ wordBreak: "break-all" }}>
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
  const contentFontSize = useDisplayStore((s) => s.contentFontSize);
  const setContentFontSize = useDisplayStore((s) => s.setContentFontSize);
  const logFontSizePx = useDisplayStore((s) => s.logFontSizePx);
  const setLogFontSizePx = useDisplayStore((s) => s.setLogFontSizePx);
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

  const [process, setProcess] = useState<ProcessPlan | null>(null);
  const { data: storageAccess } = useStorageAccess();
  const updateStorageAccess = useUpdateStorageAccess();

  return (
    <Stack>
      <Title order={3}>Settings</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "users" })}>
        <Tabs.List>
          <Tabs.Tab value="users">Benutzer & Rollen</Tabs.Tab>
          <Tabs.Tab value="snapmirror-labels">SnapMirror-Labels</Tabs.Tab>
          <Tabs.Tab value="ad">Active Directory</Tabs.Tab>
          <Tabs.Tab value="hyperv">Hyper-V-Hosts</Tabs.Tab>
          <Tabs.Tab value="storage">Storage</Tabs.Tab>
          <Tabs.Tab value="email">E-Mail</Tabs.Tab>
          <Tabs.Tab value="scheduler">Hintergrundjobs</Tabs.Tab>
          <Tabs.Tab value="alerts">Alarms</Tabs.Tab>
          <Tabs.Tab value="display">Ansicht</Tabs.Tab>
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

        <Tabs.Panel value="storage" pt="md">
          <Paper p="md" maw={620}>
            <Title order={5} mb="xs">
              Storage-Aktionen
            </Title>
            <Text size="sm" c="dimmed" mb="md">
              Steuert global, ob aendernde Aktionen unter Storage (Volume/LUN/IGroup/SnapMirror/Cluster-Peer/SVM-Peer/
              SnapMirror-Policy/-Schedule anlegen/aendern/loeschen, Cluster verifizieren/discovern/Zertifikat umstellen/
              entfernen) ueberhaupt moeglich sind -- unabhaengig von den Berechtigungen des einzelnen Benutzers.
              Ist der Schalter deaktiviert, sind alle diese Aktionen in der GUI ausgegraut UND werden vom Server
              abgelehnt (kein reiner Anzeige-Schutz). Einzige Ausnahme: einen neuen NetApp-Cluster hinzufuegen bleibt
              immer moeglich, damit die initiale Anbindung nicht blockiert wird. Gedacht fuer den Fall, dass das Tool
              nicht nur von Storage-Admins bedient wird.
            </Text>
            <Switch
              label="Storage-Aktionen erlauben"
              checked={storageAccess?.actions_enabled ?? true}
              onChange={(e) =>
                updateStorageAccess.mutate(
                  { actions_enabled: e.currentTarget.checked },
                  {
                    onSuccess: (c) =>
                      notifications.show({
                        title: c.actions_enabled ? "Storage-Aktionen erlaubt" : "Storage-Aktionen gesperrt",
                        message: "",
                        color: c.actions_enabled ? "green" : "orange",
                      }),
                    onError: (err) =>
                      notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Konnte nicht gespeichert werden."), color: "red" }),
                  },
                )
              }
              disabled={updateStorageAccess.isPending}
            />
          </Paper>
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
                        <Group gap={6} wrap="nowrap">
                          <Tooltip label={c.last_check_error ?? ""} disabled={!c.last_check_error}>
                            <Badge color={HYPERV_HEALTH_COLOR[c.health]} variant="light">
                              {HYPERV_HEALTH_LABEL[c.health]}
                              {c.node_count > 0 ? ` (${c.healthy_node_count}/${c.node_count} Knoten)` : ""}
                            </Badge>
                          </Tooltip>
                          {c.unreachable_nodes.length > 0 && (
                            <Tooltip
                              multiline
                              w={320}
                              label={
                                <Stack gap={2}>
                                  <Text size="xs" fw={600}>
                                    Cluster-Mitglied, aber per WinRM nicht direkt erreichbar (fehlende Einrichtung? siehe
                                    Deployment-Doku):
                                  </Text>
                                  {c.unreachable_nodes.map((n) => (
                                    <Text key={n.name} size="xs">
                                      {n.name}{n.address ? ` (${n.address})` : ""}: {n.error ?? "nicht erreichbar"}
                                    </Text>
                                  ))}
                                </Stack>
                              }
                            >
                              <Badge color="red" variant="filled">
                                {c.unreachable_nodes.length} Knoten nicht erreichbar
                              </Badge>
                            </Tooltip>
                          )}
                        </Group>
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
                <ConfigRow label="WinRM CA-Trust-Datei" value={settings?.winrm_ca_trust_path || "nur System-Truststore"} />
              </Stack>
            </Paper>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="email" pt="md">
          <EmailSettingsTab />
        </Tabs.Panel>

        <Tabs.Panel value="scheduler" pt="md">
          <SchedulerConfigTab />
        </Tabs.Panel>

        <Tabs.Panel value="alerts" pt="md">
          <AlertSettingsTab />
        </Tabs.Panel>

        <Tabs.Panel value="display" pt="md">
          <Paper p="md" maw={520}>
            <Title order={5} mb={4}>
              Ansicht
            </Title>
            <Text size="xs" c="dimmed" mb="md">
              Gilt nur für diesen Browser (wie das Farbschema), nicht serverweit für alle Benutzer.
            </Text>
            <Stack gap="md">
              <Select
                label="Content-Schriftgröße"
                description="Schriftgröße für Tabellen und Texte im Hauptbereich"
                data={[
                  { value: "small", label: "Klein" },
                  { value: "normal", label: "Standard" },
                  { value: "large", label: "Groß" },
                ]}
                value={contentFontSize}
                onChange={(v) => v && setContentFontSize(v as ContentFontSize)}
                allowDeselect={false}
              />
              <Select
                label="Log-Schriftgröße"
                description="Schriftgröße im System-Log-Viewer"
                data={LOG_FONT_SIZE_OPTIONS.map((px) => ({ value: String(px), label: `${px}px` }))}
                value={String(logFontSizePx)}
                onChange={(v) => v && setLogFontSizePx(Number(v))}
                allowDeselect={false}
              />
            </Stack>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="updates" pt="md">
          <Paper p="md" maw={860}>
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
