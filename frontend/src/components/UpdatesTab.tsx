import { useEffect, useRef, useState } from "react";
import { Alert, Badge, Button, Code, FileInput, Group, Paper, Progress, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconUpload } from "@tabler/icons-react";

import { usePublicSettings, useVersion } from "@/api/hooks.settings";
import { useDiscardUpdatePackage, useInstallUpdatePackage, useUpdateStatus, useUploadUpdatePackage } from "@/api/hooks.updates";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

// Settings > Updates. Im Release-Image: laufende Version, Paket hochladen und
// vom Update-Dienst des Servers einspielen lassen (die App selbst holt nichts
// aus dem Internet und tauscht sich nicht selbst aus). Bei der bisherigen
// Auslieferung per git nur die Anzeige der Git-Einstellungen.

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Group gap="md" wrap="nowrap" align="flex-start">
      <Text size="sm" c="dimmed" w={190} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Text size="sm" component="div">
        {value}
      </Text>
    </Group>
  );
}

// Erste 64-stellige Hex-Folge aus dem Inhalt einer .sha256-Datei ("<summe>  <datei>").
function checksumFrom(text: string): string | null {
  return text.toLowerCase().match(/\b[0-9a-f]{64}\b/)?.[0] ?? null;
}

export function UpdatesTab() {
  const { data: settings } = usePublicSettings();
  const { data: version } = useVersion();
  const [installing, setInstalling] = useState(false);
  const { data: status, isError } = useUpdateStatus(installing);
  const [file, setFile] = useState<File | null>(null);
  const [checksumFile, setChecksumFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const upload = useUploadUpdatePackage(setProgress);
  const discard = useDiscardUpdatePackage();
  const install = useInstallUpdatePackage();
  const versionBefore = useRef<string | null>(null);

  const fail = (err: unknown, fallback: string) =>
    notifications.show({ title: "Fehler", message: apiErrorMessage(err, fallback), color: "red", autoClose: 12000 });

  // Das Einspielen ist vorbei, sobald die App wieder antwortet und kein Auftrag mehr offen ist.
  useEffect(() => {
    if (!installing || isError || !status || status.pending) return;
    setInstalling(false);
    const ok = status.last_result?.status === "succeeded";
    notifications.show({
      title: ok ? "Update eingespielt" : "Update fehlgeschlagen",
      message: ok
        ? `Es läuft jetzt Version ${status.version ?? "?"}. Die Seite wird neu geladen.`
        : "Die vorherige Version läuft weiter. Einzelheiten stehen im Protokoll unten.",
      color: ok ? "green" : "red",
      autoClose: ok ? 4000 : 15000,
    });
    // Neue Version = neues Frontend-Bundle: einmal neu laden, damit Oberflaeche und Backend zusammenpassen.
    if (ok && status.version !== versionBefore.current) window.setTimeout(() => window.location.reload(), 2500);
  }, [installing, isError, status]);

  if (version && !version.version) {
    return (
      <Paper p="md" maw={860}>
        <Stack gap="xs">
          <Row label="Auslieferung" value="Git-Checkout im Container (bisheriger Weg)" />
          <Row label="Git-Repository" value={settings?.git_repo_url || "nicht konfiguriert"} />
          <Row label="Branch" value={settings?.git_branch ?? "-"} />
          <Row label="Auto-Update aktiv" value={settings?.auto_update_enabled ? "Ja" : "Nein"} />
          <Row label="Intervall (Minuten)" value={settings?.auto_update_interval_minutes ?? "-"} />
        </Stack>
      </Paper>
    );
  }

  async function startUpload() {
    if (!file || !checksumFile) return;
    const sha256 = checksumFrom(await checksumFile.text());
    if (!sha256) {
      notifications.show({ title: "Fehler", message: "Die Prüfsummendatei enthält keine SHA-256-Prüfsumme.", color: "red" });
      return;
    }
    setProgress(0);
    upload.mutate(
      { file, sha256 },
      {
        onSuccess: () => {
          setFile(null);
          setChecksumFile(null);
        },
        onError: (err) => fail(err, "Hochladen fehlgeschlagen."),
      },
    );
  }

  function startInstall() {
    const staged = status?.staged;
    if (!staged) return;
    confirmAction({
      title: "Update einspielen",
      message:
        `Version ${staged.version} einspielen (aktuell ${status?.version ?? "?"})? Die Datenbank wird vorher gesichert. ` +
        "Die Oberfläche ist währenddessen etwa eine Minute nicht erreichbar; alle Benutzer bleiben angemeldet.",
      confirmLabel: "Einspielen",
      color: "blue",
      onConfirm: () => {
        versionBefore.current = status?.version ?? null;
        install.mutate(undefined, {
          onSuccess: () => setInstalling(true),
          onError: (err) => fail(err, "Das Einspielen konnte nicht gestartet werden."),
        });
      },
    });
  }

  const staged = status?.staged;
  const result = status?.last_result;
  const busy = installing || !!status?.pending;

  return (
    <Stack gap="md" maw={860}>
      <Paper p="md">
        <Stack gap="xs">
          <Row label="Auslieferung" value="Release-Image" />
          <Row label="Version" value={version?.version ?? status?.version ?? "-"} />
          <Row label="Commit" value={version?.commit_short ?? "-"} />
          <Row label="Gebaut am" value={formatDateTime(version?.last_deploy_at, "unbekannt")} />
          <Row
            label="Update-Dienst des Servers"
            value={
              status?.agent_active ? (
                <Badge color="green" variant="light">
                  aktiv
                </Badge>
              ) : (
                <Group gap="xs">
                  <Badge color="gray" variant="light">
                    nicht aktiv
                  </Badge>
                  <Text size="xs" c="dimmed">
                    {status?.agent_last_seen_at ? `zuletzt gemeldet ${formatDateTime(status.agent_last_seen_at)}` : "noch nie gemeldet"}
                  </Text>
                </Group>
              )
            }
          />
          <Text size="xs" c="dimmed">
            Diese Installation lädt keinen Code aus dem Internet nach. Ein Update kommt als Paketdatei: hier hochladen oder auf dem Server
            mit <Code>hvnb-update &lt;Paket&gt;</Code> einspielen.
          </Text>
        </Stack>
      </Paper>

      <Paper p="md">
        <Title order={5} mb="xs">
          Update per Paketdatei
        </Title>
        {status && !status.agent_active && !busy && (
          <Alert icon={<IconInfoCircle size={16} />} color="gray" variant="light" mb="sm">
            Zum Einspielen aus der Oberfläche muss auf dem Server einmalig der Update-Dienst eingerichtet werden:{" "}
            <Code>hvnb-update --install-agent</Code>. Hochladen geht auch ohne ihn.
          </Alert>
        )}
        {busy ? (
          <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
            <Text size="sm" fw={600}>
              Update wird eingespielt …
            </Text>
            <Text size="sm">
              {isError
                ? "Die App startet gerade neu. Diese Seite meldet sich von selbst wieder."
                : "Der Server prüft das Paket, sichert die Datenbank und startet die neue Version."}
            </Text>
            <Progress value={100} animated mt="xs" />
          </Alert>
        ) : staged ? (
          <Stack gap="xs">
            <Row label="Hochgeladenes Paket" value={staged.package} />
            <Row label="Enthaltene Version" value={`${staged.version} (aktuell läuft ${status?.version ?? "?"})`} />
            <Row label="Größe" value={`${Math.round(staged.size_bytes / (1024 * 1024))} MB`} />
            <Row
              label="Prüfsumme"
              value={
                <Group gap={6}>
                  <IconCheck size={14} color="var(--mantine-color-green-6)" />
                  <Text size="xs" ff="monospace">
                    {staged.sha256.slice(0, 16)}… stimmt
                  </Text>
                </Group>
              }
            />
            <Row label="Hochgeladen" value={`${formatDateTime(staged.uploaded_at)}${staged.uploaded_by ? ` durch ${staged.uploaded_by}` : ""}`} />
            <Group mt="xs">
              <Button onClick={startInstall} loading={install.isPending} disabled={!status?.agent_active}>
                Jetzt einspielen
              </Button>
              <Button
                variant="default"
                loading={discard.isPending}
                onClick={() => discard.mutate(undefined, { onError: (err) => fail(err, "Verwerfen fehlgeschlagen.") })}
              >
                Paket verwerfen
              </Button>
            </Group>
          </Stack>
        ) : (
          <Stack gap="xs">
            <Group grow align="flex-start">
              <FileInput
                label="Paketdatei"
                description="hvnb-<Version>.tar.gz"
                placeholder="Datei wählen"
                accept=".gz,application/gzip"
                value={file}
                onChange={setFile}
                clearable
              />
              <FileInput
                label="Prüfsummendatei"
                description="hvnb-<Version>.tar.gz.sha256"
                placeholder="Datei wählen"
                accept=".sha256,text/plain"
                value={checksumFile}
                onChange={setChecksumFile}
                clearable
              />
            </Group>
            {upload.isPending && <Progress value={progress} animated />}
            <Group>
              <Button
                leftSection={<IconUpload size={16} />}
                onClick={startUpload}
                loading={upload.isPending}
                disabled={!file || !checksumFile}
              >
                {upload.isPending ? `Lade hoch (${progress} %)` : "Hochladen und prüfen"}
              </Button>
            </Group>
          </Stack>
        )}
      </Paper>

      {result && !busy && (
        <Paper p="md">
          <Group gap="xs" mb="xs">
            <Title order={5}>Letztes Update aus der Oberfläche</Title>
            <Badge color={result.status === "succeeded" ? "green" : "red"} variant="light">
              {result.status === "succeeded" ? "erfolgreich" : "fehlgeschlagen"}
            </Badge>
          </Group>
          <Stack gap="xs">
            <Row label="Paket" value={result.package ?? "-"} />
            <Row label="Beendet" value={formatDateTime(result.finished_at, "-")} />
            {result.status !== "succeeded" && (
              <Alert icon={<IconAlertTriangle size={16} />} color="red" variant="light">
                Das Update wurde nicht eingespielt; es läuft weiter die vorherige Version.
              </Alert>
            )}
            {result.log && (
              <Code block style={{ maxHeight: 260, overflow: "auto", fontSize: 12 }}>
                {result.log}
              </Code>
            )}
          </Stack>
        </Paper>
      )}
    </Stack>
  );
}
