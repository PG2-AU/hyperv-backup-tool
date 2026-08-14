import { Alert, Badge, Group, Paper, Stack, Table, Tabs, Text, Title } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { usePublicSettings, useRoles, useUsers } from "@/api/hooks.settings";

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

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") ?? "users";
  const { data: users } = useUsers();
  const { data: roles } = useRoles();
  const { data: settings } = usePublicSettings();

  return (
    <Stack>
      <Title order={3}>Einstellungen</Title>

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "users" })}>
        <Tabs.List>
          <Tabs.Tab value="users">Benutzer & Rollen</Tabs.Tab>
          <Tabs.Tab value="ad">Active Directory</Tabs.Tab>
          <Tabs.Tab value="netapp">NetApp-Verbindung</Tabs.Tab>
          <Tabs.Tab value="hyperv">Hyper-V-Hosts</Tabs.Tab>
          <Tabs.Tab value="updates">Updates (Git)</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="users" pt="md">
          <Stack>
            <Paper p="md">
              <Title order={5} mb="sm">
                Benutzer
              </Title>
              <Table striped highlightOnHover>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Benutzername</Table.Th>
                    <Table.Th>Anzeigename</Table.Th>
                    <Table.Th>Quelle</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Letzte Anmeldung</Table.Th>
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
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Paper>

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
          <Paper p="md" maw={520}>
            <Stack gap="xs">
              <ConfigRow label="WinRM-Transport" value={settings?.winrm_transport ?? "-"} />
              <ConfigRow label="WinRM ueber HTTPS" value={settings?.winrm_use_https ? "Ja" : "Nein"} />
              <ConfigRow label="WinRM-Port" value={settings?.winrm_port ?? "-"} />
            </Stack>
          </Paper>
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

      <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
        Diese Ansicht zeigt die aktuell aktive Server-Konfiguration (aus Umgebungsvariablen/.env). Bearbeitung direkt aus
        der GUI folgt in einer kommenden Iteration.
      </Alert>
    </Stack>
  );
}
