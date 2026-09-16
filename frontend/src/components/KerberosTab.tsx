import { useEffect, useState } from "react";
import { Alert, Anchor, Badge, Button, Code, CopyButton, Group, Paper, Select, Stack, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconCopy, IconSearch, IconX } from "@tabler/icons-react";

import { useDetectKerberosRealm, useHyperVClusters, useKerberosConfig, useTestKerberosConnection, useUpdateKerberosConfig } from "@/api/hooks";
import { apiErrorMessage } from "@/utils/errors";

function fmtDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString("de-DE") : "–";
}

/** Ein kopierbarer Shell-Befehlsblock fuer die Rollout-Checkliste unten --
 * dieselbe Code+CopyButton-Kombination wie beim PowerShell-Skript in
 * WinrmCertsTab, hier aber fuer mehrere statische Befehlsgruppen statt
 * eines einzelnen generierten Skripts. */
function CommandBlock({ title, commands }: { title: string; commands: string }) {
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="xs" fw={600} c="dimmed">
          {title}
        </Text>
        <CopyButton value={commands}>
          {({ copied, copy }) => (
            <Button size="compact-xs" variant="subtle" leftSection={<IconCopy size={12} />} color={copied ? "teal" : undefined} onClick={copy}>
              {copied ? "Kopiert" : "Kopieren"}
            </Button>
          )}
        </CopyButton>
      </Group>
      <Code block style={{ whiteSpace: "pre", overflowX: "auto" }}>
        {commands}
      </Code>
    </Stack>
  );
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

      <Paper withBorder p="md">
        <Title order={5} mb="xs">
          Rollout-Checkliste (vor der produktiven Umstellung)
        </Title>
        <Text size="sm" c="dimmed" mb="md">
          Alle Befehle laufen auf dem HVNB-Server selbst (SSH), im Projektverzeichnis (z.B. <Code>~/hyperv-netapp-backup</Code>).
          Ausführlicher Hintergrund je Schritt: <Anchor href="/docs/deployment" target="_blank">DEPLOYMENT.md, Abschnitt "Alternative
          zu CredSSP/NTLM: Kerberos"</Anchor>.
        </Text>

        <Stack gap="lg">
          <div>
            <Text size="sm" fw={600} mb={4}>
              1. Aktuellen Code-Stand holen
            </Text>
            <Text size="xs" c="dimmed" mb={6}>
              Kerberos braucht Code-Änderungen (Dockerfile + Backend) -- ohne <Code>git pull</Code> baut Schritt 2 aus einem
              veralteten Dockerfile und die neuen System-Pakete fehlen im Image.
            </Text>
            <CommandBlock title="Shell" commands={"git pull\ngit log -1 --oneline"} />
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>
              2. Docker-Image neu bauen und Pakete verifizieren
            </Text>
            <Text size="xs" c="dimmed" mb={6}>
              Der normale Git-Pull-Auto-Update-Mechanismus des laufenden Containers baut <strong>kein</strong> neues Image --
              das reine Ausrollen des neuen Codes reicht nicht. Der laufende Container bleibt waehrend des Bauens unangetastet.
              Erwartete Ausgabe der zweiten Zeile: fuenf Paketnamen mit Version, keine "is not installed"-Meldung.
            </Text>
            <CommandBlock
              title="Shell"
              commands={
                "podman-compose -f docker-compose.yml build\n" +
                'podman run --rm --entrypoint bash localhost/hyperv-netapp-backup:local -c "rpm -q krb5-devel gcc python3.12-devel krb5-workstation krb5-libs"'
              }
            />
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>
              3. Realm/KDC einrichten (oben auf dieser Seite)
            </Text>
            <Text size="xs" c="dimmed">
              Cluster auswählen → "Automatisch erkennen" → "Verbindung testen" → "Speichern". Pro Installation nur einmal
              nötig. Falls ein Restore-Proxy-Host im Einsatz ist: zusätzlich dessen <strong>Hostname</strong>-Feld setzen
              (Restore &gt; Setup &gt; Proxy-Host) -- sonst schlägt jeder Restore trotz erfolgreichem Cluster-Test fehl.
            </Text>
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>
              4. <Code>.env</Code> anpassen
            </Text>
            <Text size="xs" c="dimmed" mb={6}>
              Empfohlen: erst NACH einem erfolgreichen Ad-hoc-Checkpoint-Test gegen eine unkritische Test-VM ohne aktive
              Policy (Skript in der Doku, siehe Link oben). In der <Code>.env</Code>-Datei im Projektverzeichnis die Zeile
              suchen/ergänzen:
            </Text>
            <CommandBlock title=".env" commands={"HVNB_WINRM_TRANSPORT=kerberos"} />
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>
              5. Container neu erstellen und verifizieren
            </Text>
            <Text size="xs" c="dimmed" mb={6}>
              Eine reine <Code>.env</Code>-Änderung wird nicht automatisch übernommen -- braucht einen Neustart. Der erste
              Start danach dauert spürbar länger (Repo-Klon + Kompilieren von <Code>gssapi</Code>/<Code>pykerberos</Code>,
              ca. 1,5–2 Minuten) -- kein Fehler, kurz warten.
            </Text>
            <CommandBlock
              title="Shell"
              commands={
                "systemctl --user restart hvnb-backup.service\n" +
                "systemctl --user status hvnb-backup.service\n" +
                "podman logs --tail 50 hvnb-backup\n" +
                "podman exec hvnb-backup git -C /opt/app rev-parse HEAD\n" +
                "curl -sk4 https://127.0.0.1:8443/api/health"
              }
            />
            <Text size="xs" c="dimmed" mt={6}>
              Erwartet: Status <Code>active (running)</Code>, keine Fehler in den Logs, der Commit-Hash entspricht dem aus
              Schritt 1, und <Code>{'{"status":"ok","app":"Hyper-V NetApp Backup"}'}</Code> vom Health-Check.
            </Text>
          </div>

          <div>
            <Text size="sm" fw={600} mb={4}>
              6. Beobachten, nicht nur einmal testen
            </Text>
            <Text size="xs" c="dimmed">
              Den nächsten planmäßig ausgelösten Backup-Lauf im Job-Verlauf verfolgen. Rückfallebene bei Auffälligkeiten:
              jederzeit <Code>HVNB_WINRM_TRANSPORT=ntlm</Code> zurückstellen + erneut Schritt 5 (Neustart) ausführen.
            </Text>
          </div>
        </Stack>
      </Paper>
    </Stack>
  );
}
