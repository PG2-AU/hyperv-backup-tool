import { useEffect, useState } from "react";
import { Alert, Badge, Button, Group, Paper, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconSearch, IconX } from "@tabler/icons-react";

import { useDetectKerberosRealm, useHyperVClusters, useKerberosConfig, useTestKerberosConnection, useUpdateKerberosConfig } from "@/api/hooks";
import { apiErrorMessage } from "@/utils/errors";

function fmtDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString("de-DE") : "–";
}

/** Settings > Kerberos (Backlog-Punkt 50) -- ein einziges, globales
 * Realm/KDC-Paar fuer HVNB_WINRM_TRANSPORT=kerberos (bewusst kein
 * Multi-Domain, siehe app.core.kerberos_config). "Automatisch erkennen"
 * fragt Realm/KDC live bei einem bereits registrierten Cluster ab (ueber
 * dessen aktuell funktionierenden Transport), "Verbindung testen" prueft
 * Ticket-Beschaffung + eine echte WinRM-Verbindung gegen die im Formular
 * stehenden (noch nicht zwingend gespeicherten) Werte. */
export function KerberosTab() {
  const { data: config } = useKerberosConfig();
  const { data: clusters } = useHyperVClusters();
  const updateConfig = useUpdateKerberosConfig();
  const detectRealm = useDetectKerberosRealm();
  const testConnection = useTestKerberosConnection();

  const [realm, setRealm] = useState("");
  const [kdcHostname, setKdcHostname] = useState("");
  const [kdcAddress, setKdcAddress] = useState<string | null>(null);
  const [selectedClusterId, setSelectedClusterId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  useEffect(() => {
    if (config) {
      setRealm(config.realm ?? "");
      setKdcHostname(config.kdc_hostname ?? "");
      setKdcAddress(config.kdc_address ?? null);
    }
  }, [config]);

  const clusterOptions = (clusters ?? []).map((c) => ({ value: c.id, label: c.name }));

  function runDetect() {
    if (!selectedClusterId) return;
    detectRealm.mutate(selectedClusterId, {
      onSuccess: (result) => {
        setRealm(result.realm);
        setKdcHostname(result.kdc_hostname);
        setKdcAddress(result.kdc_address ?? null);
        setTestResult(null);
        notifications.show({ title: "Realm/KDC erkannt", message: `${result.realm} @ ${result.kdc_hostname}`, color: "green" });
      },
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Erkennung fehlgeschlagen."), color: "red" }),
    });
  }

  function runTest() {
    if (!selectedClusterId || !realm.trim() || !kdcHostname.trim()) return;
    setTestResult(null);
    testConnection.mutate(
      { cluster_id: selectedClusterId, realm: realm.trim(), kdc_hostname: kdcHostname.trim() },
      {
        onSuccess: (result) => setTestResult(result),
        onError: (err) => setTestResult({ success: false, message: apiErrorMessage(err, "Test fehlgeschlagen.") }),
      },
    );
  }

  function save() {
    updateConfig.mutate(
      { realm: realm.trim(), kdc_hostname: kdcHostname.trim(), kdc_address: kdcAddress },
      {
        onSuccess: () => notifications.show({ title: "Gespeichert", message: "Kerberos-Konfiguration aktualisiert.", color: "green" }),
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Speichern fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Stack>
      <Paper withBorder p="md">
        <Title order={5} mb="xs">
          Kerberos-Realm/KDC
        </Title>
        <Text size="sm" c="dimmed" mb="md">
          Ein einziges, globales Realm/KDC-Paar fuer den Transport "kerberos" (Settings &gt; Hyper-V-Hosts) -- das Tool
          registriert nur Cluster innerhalb einer AD-Domäne, eine Konfiguration pro Cluster ist daher nicht nötig. Kein
          Domain-Join des Containers erforderlich -- die Ticket-Beschaffung erfolgt zur Laufzeit mit den ohnehin
          hinterlegten Zugangsdaten.
        </Text>

        <Group align="flex-end" mb="md">
          <Select
            label="Cluster fuer Erkennung/Test"
            placeholder="Cluster wählen"
            data={clusterOptions}
            value={selectedClusterId}
            onChange={setSelectedClusterId}
            style={{ flex: 1 }}
          />
          <Button
            variant="light"
            leftSection={<IconSearch size={16} />}
            disabled={!selectedClusterId}
            loading={detectRealm.isPending}
            onClick={runDetect}
          >
            Automatisch erkennen
          </Button>
        </Group>

        <Group grow mb="md">
          <TextInput label="Realm" placeholder="z.B. HYPERV.DEMO.AU.LOCAL" value={realm} onChange={(e) => setRealm(e.currentTarget.value)} />
          <TextInput
            label="KDC (Domain Controller)"
            placeholder="z.B. dc1.hyperv.demo.au.local"
            value={kdcHostname}
            onChange={(e) => setKdcHostname(e.currentTarget.value)}
          />
        </Group>
        {kdcAddress && (
          <Text size="xs" c="dimmed" mb="md">
            KDC-Adresse (zuletzt erkannt): {kdcAddress}
          </Text>
        )}

        {testResult && (
          <Alert
            mb="md"
            color={testResult.success ? "green" : "red"}
            icon={testResult.success ? <IconCheck size={16} /> : <IconX size={16} />}
            title={testResult.success ? "Verbindung erfolgreich" : "Verbindung fehlgeschlagen"}
          >
            {testResult.message}
          </Alert>
        )}

        <Group justify="space-between">
          <Button
            variant="default"
            disabled={!selectedClusterId || !realm.trim() || !kdcHostname.trim()}
            loading={testConnection.isPending}
            onClick={runTest}
          >
            Verbindung testen
          </Button>
          <Button disabled={!realm.trim() || !kdcHostname.trim()} loading={updateConfig.isPending} onClick={save}>
            Speichern
          </Button>
        </Group>
      </Paper>

      <Paper withBorder p="md">
        <Group justify="space-between">
          <Text size="sm" fw={600}>
            Aktueller Stand
          </Text>
          {config?.realm ? (
            <Badge color="green" variant="light">
              konfiguriert
            </Badge>
          ) : (
            <Badge color="gray" variant="light">
              nicht konfiguriert
            </Badge>
          )}
        </Group>
        <Text size="xs" c="dimmed" mt={4}>
          Zuletzt gespeichert: {fmtDate(config?.updated_at)} {config?.updated_by ? `von ${config.updated_by}` : ""}
        </Text>
      </Paper>

      <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
        Der Transport selbst wird weiterhin über die Umgebungsvariable <code>HVNB_WINRM_TRANSPORT</code> gewählt (aktueller
        Wert unter Settings &gt; Hyper-V-Hosts einsehbar, dort nur zur Anzeige). Diese Seite konfiguriert nur das
        Realm/KDC, das dabei bei "kerberos" verwendet wird -- ohne gültige Konfiguration hier schlägt eine
        Kerberos-Verbindung fehl.
      </Alert>
    </Stack>
  );
}
