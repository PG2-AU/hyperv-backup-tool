import { useState } from "react";
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Code,
  CopyButton,
  Divider,
  FileButton,
  Group,
  List,
  Paper,
  Radio,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconCopy, IconTrash, IconUpload } from "@tabler/icons-react";

import {
  useBuildWinrmBundle,
  useDeleteWinrmCert,
  useGenerateWinrmSetupScript,
  useUploadWinrmCert,
  useWinrmCerts,
} from "@/api/hooks";
import type { WinrmClusterType } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

function fmtDate(value?: string | null): string {
  return value ? new Date(value).toLocaleString("de-DE") : "–";
}

export function WinrmCertsTab() {
  const { data: overview } = useWinrmCerts();
  const generateScript = useGenerateWinrmSetupScript();
  const uploadCert = useUploadWinrmCert();
  const deleteCert = useDeleteWinrmCert();
  const buildBundle = useBuildWinrmBundle();

  // Block 1 -- Assistent
  const [clusterType, setClusterType] = useState<WinrmClusterType>("failover_cluster");
  const [cnoHostname, setCnoHostname] = useState("");
  const [cnoIp, setCnoIp] = useState("");
  const [ownIp, setOwnIp] = useState("");
  const [script, setScript] = useState<string | null>(null);

  // Block 3 -- Upload
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [uploadLabel, setUploadLabel] = useState("");
  const [uploadAddress, setUploadAddress] = useState("");

  const isCluster = clusterType === "failover_cluster";
  const canGenerate = !isCluster || (cnoHostname.trim() !== "" && cnoIp.trim() !== "");

  function handleGenerate() {
    generateScript
      .mutateAsync({
        cluster_type: clusterType,
        cno_hostname: isCluster ? cnoHostname.trim() : null,
        cno_ip: isCluster ? cnoIp.trim() : null,
        own_ip: ownIp.trim() || null,
      })
      .then(setScript)
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Skript konnte nicht erzeugt werden."), color: "red" }),
      );
  }

  function handleUpload() {
    if (!pendingFile) return;
    uploadCert
      .mutateAsync({ file: pendingFile, label: uploadLabel.trim() || undefined, hostAddress: uploadAddress.trim() || undefined })
      .then(() => {
        notifications.show({ title: "Hochgeladen", message: `${pendingFile.name} übernommen.`, color: "green" });
        setPendingFile(null);
        setUploadLabel("");
        setUploadAddress("");
      })
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zertifikat konnte nicht gelesen werden."), color: "red" }),
      );
  }

  function handleDelete(id: string, label: string) {
    confirmAction({
      title: "Zertifikat entfernen",
      message: `„${label}" aus der Vertrauensliste entfernen? Das Bundle muss danach neu erzeugt werden.`,
      confirmLabel: "Entfernen",
      onConfirm: () =>
        deleteCert
          .mutateAsync(id)
          .then(() => notifications.show({ title: "Entfernt", message: `„${label}" gelöscht.`, color: "blue" }))
          .catch((err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Löschen fehlgeschlagen."), color: "red" }),
          ),
    });
  }

  function handleBuild() {
    buildBundle
      .mutateAsync()
      .then((data) =>
        notifications.show({
          title: "Bundle erzeugt",
          message: `${data.trust_state.bundle_cert_count} Zertifikate → ${data.trust_state.bundle_path}. Wirkt sofort, ohne Neustart.`,
          color: "green",
        }),
      )
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Bundle konnte nicht geschrieben werden."), color: "red" }),
      );
  }

  const certs = overview?.certificates ?? [];
  const trust = overview?.trust_state;

  return (
    <Stack maw={860}>
      <Text size="sm" c="dimmed">
        Damit der Container den WinRM-HTTPS-Zertifikaten der Hyper-V-Hosts vertraut, muss ein CA-Trust-Bundle hinterlegt sein. Diese
        Sektion ersetzt den manuellen Weg (Zertifikate von Hand bündeln, per <Code>podman cp</Code> kopieren, <Code>.env</Code>{" "}
        anpassen, Neustart) – der Ablauf funktioniert auch bei einer Erstinstallation, bevor der erste Cluster hinzugefügt wird.
      </Text>

      {/* -------------------------------------------------- 1 -------------- */}
      <Paper p="md" withBorder>
        <Title order={5} mb={4}>
          1 · Zertifikat-Assistent
        </Title>
        <Text size="xs" c="dimmed" mb="md">
          Erzeugt ein fertiges PowerShell-Skript für einen Hyper-V-Host: selbstsigniertes Zertifikat mit korrekten DNS- und
          IP-Address-SANs, HTTPS-Listener, Firewall-Regel und CredSSP (jeweils mit Vorab-Prüfung), abschließend Export der
          hochladbaren <Code>.pem</Code>.
        </Text>
        <Stack gap="sm">
          <Radio.Group label="Cluster-Typ" value={clusterType} onChange={(v) => setClusterType(v as WinrmClusterType)}>
            <Group mt="xs">
              <Radio value="failover_cluster" label="Failover-Cluster" />
              <Radio value="single_host" label="Einzelner Host" />
            </Group>
          </Radio.Group>
          {isCluster && (
            <Group grow>
              <TextInput
                label="Cluster-DNS-Name (CNO)"
                placeholder="z. B. svhvclu01.rvm.local"
                value={cnoHostname}
                onChange={(e) => setCnoHostname(e.currentTarget.value)}
              />
              <TextInput
                label="Cluster-IP (CNO)"
                placeholder="z. B. 10.10.2.10"
                value={cnoIp}
                onChange={(e) => setCnoIp(e.currentTarget.value)}
              />
            </Group>
          )}
          <TextInput
            label="Host-IP (Management-IP dieses Knotens)"
            description="Optional, aber empfohlen – im Skript pro Knoten anzupassen."
            placeholder="z. B. 10.10.2.11"
            value={ownIp}
            onChange={(e) => setOwnIp(e.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button onClick={handleGenerate} loading={generateScript.isPending} disabled={!canGenerate}>
              Skript erzeugen
            </Button>
          </Group>
        </Stack>

        {script && (
          <Stack gap="xs" mt="md">
            <Group justify="space-between">
              <Text size="sm" fw={600}>
                PowerShell-Skript
              </Text>
              <CopyButton value={script}>
                {({ copied, copy }) => (
                  <Button size="xs" variant="light" leftSection={<IconCopy size={14} />} color={copied ? "teal" : undefined} onClick={copy}>
                    {copied ? "Kopiert" : "Kopieren"}
                  </Button>
                )}
              </CopyButton>
            </Group>
            <Code block style={{ whiteSpace: "pre", overflowX: "auto", maxHeight: 360 }}>
              {script}
            </Code>
          </Stack>
        )}
      </Paper>

      {/* -------------------------------------------------- 2 -------------- */}
      <Paper p="md" withBorder>
        <Title order={5} mb={4}>
          2 · Was jetzt zu tun ist
        </Title>
        <List type="ordered" size="sm" spacing="xs">
          <List.Item>Skript auf jedem Clusterknoten als Administrator ausführen – bei einem Failover-Cluster die Host-IP je Knoten anpassen.</List.Item>
          <List.Item>
            Die dabei erzeugte Datei <Code>C:\temp\winrm-&lt;host&gt;.pem</Code> per RDP/Copy vom Knoten holen.
          </List.Item>
          <List.Item>Unten je Knoten eine Datei hochladen, danach „Zertifikatsbundle erzeugen".</List.Item>
        </List>
        <Text size="xs" c="dimmed" mt="xs">
          Details und Sonderfälle (interne CA, MAC-Bindung, Firewall-Einschränkung): <Anchor href="/docs/deployment" target="_blank">DEPLOYMENT.md, Kapitel 10</Anchor>.
        </Text>
      </Paper>

      {/* -------------------------------------------------- 3 -------------- */}
      <Paper p="md" withBorder>
        <Title order={5} mb={4}>
          3 · Hochgeladene Host-Zertifikate
        </Title>

        <Group align="flex-end" gap="sm" mb="sm">
          <FileButton onChange={setPendingFile} accept=".pem,.cer,.crt,.der">
            {(props) => (
              <Button {...props} variant="light" leftSection={<IconUpload size={16} />}>
                Zertifikatsdatei wählen
              </Button>
            )}
          </FileButton>
          {pendingFile && (
            <Text size="sm" c="dimmed">
              {pendingFile.name}
            </Text>
          )}
        </Group>

        {pendingFile && (
          <Group align="flex-end" gap="sm" mb="md">
            <TextInput
              label="Host-Name"
              description="leer = Common Name aus dem Zertifikat"
              value={uploadLabel}
              onChange={(e) => setUploadLabel(e.currentTarget.value)}
            />
            <TextInput
              label="Host-IP"
              description="leer = erste IP-Address-SAN"
              value={uploadAddress}
              onChange={(e) => setUploadAddress(e.currentTarget.value)}
            />
            <Button onClick={handleUpload} loading={uploadCert.isPending}>
              Hinzufügen
            </Button>
            <Button variant="subtle" color="gray" onClick={() => setPendingFile(null)}>
              Abbrechen
            </Button>
          </Group>
        )}

        {certs.length === 0 ? (
          <Text size="sm" c="dimmed">
            Noch keine Zertifikate hinterlegt.
          </Text>
        ) : (
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>IP</Table.Th>
                <Table.Th>Zertifikat</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {certs.map((c) => {
                const expired = c.not_after ? new Date(c.not_after).getTime() < Date.now() : false;
                return (
                  <Table.Tr key={c.id}>
                    <Table.Td>{c.label}</Table.Td>
                    <Table.Td>{c.host_address ?? "–"}</Table.Td>
                    <Table.Td>
                      <Stack gap={2}>
                        <Text size="sm">{c.subject_cn ?? "(kein CN)"}</Text>
                        <Text size="xs" c="dimmed">
                          {c.sans.length > 0 ? c.sans.join(", ") : "keine SAN"}
                        </Text>
                        <Group gap="xs">
                          <Badge size="xs" color={expired ? "red" : "gray"} variant="light">
                            {expired ? "abgelaufen" : `gültig bis ${fmtDate(c.not_after)}`}
                          </Badge>
                          <Tooltip label={c.fingerprint_sha256} withArrow>
                            <Text size="xs" c="dimmed" ff="monospace">
                              {c.fingerprint_sha256.slice(0, 16)}…
                            </Text>
                          </Tooltip>
                        </Group>
                      </Stack>
                    </Table.Td>
                    <Table.Td>
                      <Button
                        size="xs"
                        variant="subtle"
                        color="red"
                        leftSection={<IconTrash size={14} />}
                        onClick={() => handleDelete(c.id, c.label)}
                      >
                        Entfernen
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}

        <Divider my="md" />

        <Stack gap="xs">
          <Text size="xs" c="dimmed">
            Aktiv genutzter Trust-Pfad: <Code>{overview?.active_trust_path ?? "…"}</Code>
          </Text>
          {trust?.last_bundle_built_at ? (
            <Text size="sm">
              Bundle zuletzt geschrieben: {fmtDate(trust.last_bundle_built_at)} nach <Code>{trust.bundle_path}</Code> (
              {trust.bundle_cert_count} Zertifikate)
            </Text>
          ) : (
            <Text size="sm" c="dimmed">
              Noch kein Bundle erzeugt.
            </Text>
          )}
          {overview?.bundle_outdated && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} variant="light">
              Seit dem letzten Bundle wurden Zertifikate geändert – bitte neu erzeugen.
            </Alert>
          )}
          <Group>
            <Button
              leftSection={<IconCheck size={16} />}
              onClick={handleBuild}
              loading={buildBundle.isPending}
              disabled={certs.length === 0}
            >
              Zertifikatsbundle erzeugen
            </Button>
          </Group>
        </Stack>
      </Paper>
    </Stack>
  );
}
