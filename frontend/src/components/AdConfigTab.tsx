import { useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Paper, PasswordInput, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconX } from "@tabler/icons-react";

import { useAdConfig, useTestAdConnection, useUpdateAdConfig } from "@/api/hooks.settings";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime as fmtDate } from "@/utils/format";

/** Settings > Active Directory -- GUI-verwaltete AD-Integration fuer die
 * GUI-Anmeldung lokaler Benutzerkonten (Backlog #12, schlanke Variante
 * ohne Gruppe-zu-Rolle-Mapping). NICHT zu verwechseln mit Settings >
 * Kerberos, das ausschliesslich die WinRM-Verbindung der App zu
 * Hyper-V betrifft -- zwei getrennte Subsysteme. Das hier konfigurierte
 * Lese-Service-Konto (bind_user/bind_password) wird NUR fuer die
 * Verzeichnis-Suche beim proaktiven "Benutzer hinzufuegen" gebraucht;
 * der normale Login bindet weiterhin direkt als der anzumeldende
 * Nutzer selbst. */
export function AdConfigTab() {
  const { data: config } = useAdConfig();
  const updateConfig = useUpdateAdConfig();
  const testConnection = useTestAdConnection();

  const [enabled, setEnabled] = useState(false);
  const [server, setServer] = useState("");
  const [domain, setDomain] = useState("");
  const [baseDn, setBaseDn] = useState("");
  const [useSsl, setUseSsl] = useState(true);
  const [bindUser, setBindUser] = useState("");
  const [bindPassword, setBindPassword] = useState("");
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  useEffect(() => {
    if (config) {
      setEnabled(config.enabled);
      setServer(config.server);
      setDomain(config.domain);
      setBaseDn(config.base_dn);
      setUseSsl(config.use_ssl);
      setBindUser(config.bind_user);
    }
  }, [config]);

  function runTest() {
    if (!server.trim() || !domain.trim() || !baseDn.trim() || !bindUser.trim()) return;
    setTestResult(null);
    testConnection.mutate(
      { server: server.trim(), domain: domain.trim(), base_dn: baseDn.trim(), use_ssl: useSsl, bind_user: bindUser.trim(), bind_password: bindPassword || null },
      {
        onSuccess: (result) => setTestResult(result),
        onError: (err) => setTestResult({ success: false, message: apiErrorMessage(err, "Test fehlgeschlagen.") }),
      },
    );
  }

  function save() {
    updateConfig.mutate(
      {
        enabled, server: server.trim(), domain: domain.trim(), base_dn: baseDn.trim(), use_ssl: useSsl,
        bind_user: bindUser.trim(), bind_password: bindPassword || null,
      },
      {
        onSuccess: () => {
          notifications.show({ title: "Gespeichert", message: "AD-Konfiguration aktualisiert.", color: "green" });
          setBindPassword("");
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Speichern fehlgeschlagen."), color: "red" }),
      },
    );
  }

  const canTest = !!server.trim() && !!domain.trim() && !!baseDn.trim() && !!bindUser.trim();

  return (
    <Stack>
      <Paper withBorder p="md" maw={560}>
        <Group gap="xs" mb="xs">
          <Title order={5}>Active Directory</Title>
          {config?.enabled ? (
            <Badge color="green" variant="light">aktiv</Badge>
          ) : (
            <Badge color="gray" variant="light">deaktiviert</Badge>
          )}
        </Group>
        <Text size="sm" c="dimmed" mb="md">
          Ermöglicht die GUI-Anmeldung per AD-Zugangsdaten (LDAP-Bind) und das proaktive Hinzufügen von AD-Benutzern über
          "Benutzer hinzufügen" (Settings &gt; Benutzer &amp; Rollen). Lokale Benutzerkonten funktionieren unabhängig davon
          immer weiter. Kein Domain-Join des Containers nötig.
        </Text>

        <Stack gap="sm">
          <Switch label="Active-Directory-Integration aktiv" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />
          <TextInput label="Domain Controller" placeholder="z.B. dc01.example.local" value={server} onChange={(e) => setServer(e.currentTarget.value)} />
          <TextInput label="Domäne (NetBIOS)" placeholder="z.B. EXAMPLE" value={domain} onChange={(e) => setDomain(e.currentTarget.value)} />
          <TextInput label="Base DN" placeholder="z.B. DC=example,DC=local" value={baseDn} onChange={(e) => setBaseDn(e.currentTarget.value)} />
          <Switch label="LDAP über SSL (LDAPS)" checked={useSsl} onChange={(e) => setUseSsl(e.currentTarget.checked)} />

          <Text size="xs" fw={600} mt="xs">
            Lese-Service-Konto für die Verzeichnis-Suche
          </Text>
          <Text size="xs" c="dimmed">
            Nur für die Suche beim Hinzufügen eines AD-Benutzers nötig (braucht Leserecht im Verzeichnis) -- die normale
            Anmeldung bindet weiterhin direkt als der jeweilige Nutzer selbst.
          </Text>
          <TextInput label="Benutzername" placeholder="z.B. svc-hvnb-ad" value={bindUser} onChange={(e) => setBindUser(e.currentTarget.value)} />
          <PasswordInput
            label="Passwort"
            placeholder={config?.bind_password_set ? "gesetzt -- leer lassen, um es zu behalten" : "noch nicht gesetzt"}
            value={bindPassword}
            onChange={(e) => setBindPassword(e.currentTarget.value)}
          />
        </Stack>

        {testResult && (
          <Alert
            mt="md"
            color={testResult.success ? "green" : "red"}
            icon={testResult.success ? <IconCheck size={16} /> : <IconX size={16} />}
            title={testResult.success ? "Verbindung erfolgreich" : "Verbindung fehlgeschlagen"}
          >
            {testResult.message}
          </Alert>
        )}

        <Group justify="space-between" mt="md">
          <Button variant="default" disabled={!canTest} loading={testConnection.isPending} onClick={runTest}>
            Verbindung testen
          </Button>
          <Button loading={updateConfig.isPending} onClick={save}>
            Speichern
          </Button>
        </Group>
      </Paper>

      <Paper withBorder p="md" maw={560}>
        <Text size="sm" fw={600}>Aktueller Stand</Text>
        <Text size="xs" c="dimmed" mt={4}>
          Zuletzt gespeichert: {fmtDate(config?.updated_at)} {config?.updated_by ? `von ${config.updated_by}` : ""}
        </Text>
      </Paper>

      <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
        Gruppe-zu-Rolle-Mapping ist noch nicht umgesetzt -- ein neu hinzugefügter oder per erstem Login automatisch
        angelegter AD-Benutzer bekommt die Rolle nur, die beim Hinzufügen manuell gewählt bzw. später in Settings &gt;
        Benutzer &amp; Rollen zugewiesen wurde.
      </Alert>
    </Stack>
  );
}
