import { useState } from "react";
import { Alert, Badge, Button, Checkbox, FileButton, Group, List, Paper, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconDownload, IconFileImport, IconInfoCircle } from "@tabler/icons-react";

import { useConfigImportStatus, useExportConfig, usePreviewConfigImport, useRunConfigImport } from "@/api/hooks.configTransfer";
import type { ConfigImportPreview, ConfigImportResult } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

// Settings > System: Konfiguration exportieren/importieren (Backlog #45).
// Kennwoerter sind nie enthalten, Import nur in eine leere Installation,
// Backup-Katalog optional -- siehe backend app.core.config_transfer.
export function ConfigTransferTab() {
  return (
    <Stack maw={980}>
      <ExportSection />
      <ImportSection />
    </Stack>
  );
}

function ExportSection() {
  const [includeCatalog, setIncludeCatalog] = useState(true);
  const exportConfig = useExportConfig();

  function handleExport() {
    exportConfig.mutate(includeCatalog, {
      onSuccess: (filename) => notifications.show({ title: "Export erstellt", message: filename, color: "green" }),
      onError: (err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Export konnte nicht erstellt werden."), color: "red" }),
    });
  }

  return (
    <Paper p="md">
      <Title order={5} mb={4}>
        Konfiguration exportieren
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Lädt die komplette Einrichtung dieser Installation als ZIP herunter: Hyper-V-Cluster, NetApp-Systeme, Restore-Setup,
        Policies, Protection Groups, Zeitpläne, SnapMirror-Labels, Standorte, alle Settings, WinRM-Zertifikate sowie Benutzer und
        Rollen. Die Datei wird direkt beim Klick erzeugt und nicht auf dem Server abgelegt.
      </Text>
      <Alert color="blue" icon={<IconInfoCircle size={16} />} mb="md">
        Kennwörter, NetApp-Client-Zertifikate und Kennwörter lokaler Benutzer sind <b>nicht</b> enthalten. Nach einem Import
        trägt man sie einmal neu ein -- die Exportdatei nennt genau, wo.
      </Alert>
      <Checkbox
        label="Backup-Katalog einschließen"
        description="Abgeschlossene Backup-Läufe mit ihren Snapshots und VM-Konfigurationen. Ohne Katalog kennt eine neue Installation die weiterhin auf der NetApp liegenden Snapshots nicht als Wiederherstellungspunkte."
        checked={includeCatalog}
        onChange={(e) => setIncludeCatalog(e.currentTarget.checked)}
      />
      <Group justify="flex-end" mt="md">
        <Button leftSection={<IconDownload size={16} />} onClick={handleExport} loading={exportConfig.isPending}>
          Konfiguration exportieren
        </Button>
      </Group>
    </Paper>
  );
}

function ImportSection() {
  const { data: status } = useConfigImportStatus();
  const previewImport = usePreviewConfigImport();
  const runImport = useRunConfigImport();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ConfigImportPreview | null>(null);
  const [result, setResult] = useState<ConfigImportResult | null>(null);

  function handleFile(selected: File | null) {
    setFile(selected);
    setPreview(null);
    setResult(null);
    if (!selected) return;
    previewImport.mutate(selected, {
      onSuccess: setPreview,
      onError: (err) =>
        notifications.show({ title: "Datei nicht lesbar", message: apiErrorMessage(err, "Vorschau fehlgeschlagen."), color: "red" }),
    });
  }

  function handleImport() {
    if (!file) return;
    confirmAction({
      title: "Konfiguration importieren",
      message:
        "Die Einrichtung aus der Datei wird in diese Installation übernommen. Alle Protection Groups werden dabei pausiert, bis die Zugangsdaten nachgetragen sind.",
      confirmLabel: "Importieren",
      onConfirm: () =>
        runImport.mutate(file, {
          onSuccess: (res) => {
            setResult(res);
            setPreview(null);
            setFile(null);
            notifications.show({ title: "Import abgeschlossen", message: "Bitte die offenen Schritte abarbeiten.", color: "green" });
          },
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Import fehlgeschlagen."), color: "red" }),
        }),
    });
  }

  const notEmpty = (status?.blockers.length ?? 0) > 0;

  return (
    <Paper p="md">
      <Title order={5} mb={4}>
        Konfiguration importieren
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Übernimmt eine exportierte Einrichtung in diese Installation -- z. B. auf einem neuen Server nach Ausfall oder Umzug. Nur
        in eine leere Installation möglich (keine Cluster, Systeme, Policies, Protection Groups, Zeitpläne oder Backup-Läufe). Vor
        dem Import zeigt eine Vorschau, was übernommen wird.
      </Text>

      {result ? (
        <ImportResultView result={result} />
      ) : (
        <>
          {notEmpty && (
            <Alert color="gray" icon={<IconInfoCircle size={16} />} mb="md">
              Diese Installation ist bereits eingerichtet ({status!.blockers.join(", ")}) -- ein Import ist hier nicht möglich.
            </Alert>
          )}
          <Group>
            <FileButton onChange={handleFile} accept=".zip,application/zip">
              {(props) => (
                <Button {...props} variant="light" leftSection={<IconFileImport size={16} />} disabled={notEmpty} loading={previewImport.isPending}>
                  Export-Datei auswählen
                </Button>
              )}
            </FileButton>
            {file && <Text size="sm">{file.name}</Text>}
          </Group>
          {preview && <PreviewView preview={preview} />}
          {preview && (
            <Group justify="flex-end" mt="md">
              <Button onClick={handleImport} loading={runImport.isPending} disabled={preview.blockers.length > 0}>
                Importieren
              </Button>
            </Group>
          )}
        </>
      )}
    </Paper>
  );
}

function PreviewView({ preview }: { preview: ConfigImportPreview }) {
  return (
    <Stack gap="sm" mt="md">
      <Text size="sm">
        Export vom <b>{preview.created_at ? formatDateTime(preview.created_at) : "?"}</b> durch {preview.created_by ?? "?"}
        {preview.app_commit ? ` (Version ${preview.app_commit})` : ""}
        {preview.include_catalog && (
          <Badge ml="xs" size="sm" variant="light">
            inkl. Backup-Katalog
          </Badge>
        )}
      </Text>
      {preview.blockers.map((b) => (
        <Alert key={b} color="red" icon={<IconAlertTriangle size={16} />}>
          {b}
        </Alert>
      ))}
      {preview.warnings.map((w) => (
        <Alert key={w} color="yellow" icon={<IconAlertTriangle size={16} />}>
          {w}
        </Alert>
      ))}
      <Table withTableBorder maw={520}>
        <Table.Tbody>
          {preview.tables.map((t) => (
            <Table.Tr key={t.table}>
              <Table.Td>
                <Text size="sm">{t.label}</Text>
              </Table.Td>
              <Table.Td w={80} ta="right">
                <Text size="sm">{t.count}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {preview.manual_steps.length > 0 && <ManualSteps steps={preview.manual_steps} title="Nach dem Import von Hand zu erledigen" />}
    </Stack>
  );
}

function ImportResultView({ result }: { result: ConfigImportResult }) {
  const total = Object.values(result.imported).reduce((a, b) => a + b, 0);
  return (
    <Stack gap="sm">
      <Alert color="green" icon={<IconCheck size={16} />}>
        {total} Einträge importiert.
        {result.bundle_rebuilt ? " WinRM-Zertifikatsbundle wurde neu erzeugt." : ""}
        {result.skipped_users.length > 0
          ? ` Bereits vorhandene Benutzer blieben unverändert: ${result.skipped_users.join(", ")}.`
          : ""}
      </Alert>
      {result.paused_groups.length > 0 && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          Pausiert (vor dem Export aktiv, nach dem Nachtragen der Zugangsdaten wieder aktivieren): {result.paused_groups.join(", ")}
        </Alert>
      )}
      {result.manual_steps.length > 0 && <ManualSteps steps={result.manual_steps} title="Jetzt noch zu erledigen" />}
    </Stack>
  );
}

function ManualSteps({ steps, title }: { steps: string[]; title: string }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        {title}
      </Text>
      <List size="sm" spacing={4}>
        {steps.map((s) => (
          <List.Item key={s}>{s}</List.Item>
        ))}
      </List>
    </Stack>
  );
}
