import { useEffect, useRef, useState } from "react";
import { Accordion, Alert, Badge, Button, Code, FileInput, Group, List, Paper, Progress, Stack, Switch, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconCloudDownload, IconInfoCircle, IconRefresh, IconUpload } from "@tabler/icons-react";

import { usePublicSettings, useVersion } from "@/api/hooks.settings";
import {
  useCheckRegistry,
  useDiscardUpdatePackage,
  useInstallFromRegistry,
  useInstallUpdatePackage,
  useSetAutoUpdate,
  useSetRegistryAuto,
  useUpdateStatus,
  useUploadUpdatePackage,
} from "@/api/hooks.updates";
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
  const [fastPoll, setFastPoll] = useState(false);
  const { data: status, isError } = useUpdateStatus(installing || fastPoll);
  const [file, setFile] = useState<File | null>(null);
  const [checksumFile, setChecksumFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const upload = useUploadUpdatePackage(setProgress);
  const discard = useDiscardUpdatePackage();
  const install = useInstallUpdatePackage();
  const setAutoUpdate = useSetAutoUpdate();
  const checkRegistry = useCheckRegistry();
  const installFromRegistry = useInstallFromRegistry();
  const setRegistryAuto = useSetRegistryAuto();
  // Zeitpunkt der letzten Pruefung, als "Suchen" gedrueckt wurde -- solange er sich nicht aendert, laeuft die Suche noch.
  const [checkingSince, setCheckingSince] = useState<string | null | undefined>(undefined);
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

  const registryCheckedAt = status?.registry?.checked_at;
  const checking = checkingSince !== undefined && checkingSince === (registryCheckedAt ?? null);
  useEffect(() => {
    if (checkingSince !== undefined && !checking) setCheckingSince(undefined);
    setFastPoll(checking);
  }, [checking, checkingSince]);

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

  function startRegistryInstall(target: string) {
    confirmAction({
      title: "Online-Update einspielen",
      message:
        `Version ${target} aus der Registry holen und einspielen (aktuell ${status?.version ?? "?"})? Die Datenbank wird vorher gesichert. ` +
        "Die Oberfläche ist währenddessen etwa eine Minute nicht erreichbar.",
      confirmLabel: "Einspielen",
      color: "blue",
      onConfirm: () => {
        versionBefore.current = status?.version ?? null;
        installFromRegistry.mutate(target, {
          onSuccess: () => setInstalling(true),
          onError: (err) => fail(err, "Das Online-Update konnte nicht gestartet werden."),
        });
      },
    });
  }

  const registry = status?.registry;
  const newerAvailable = !!registry?.latest_version && registry.latest_version !== status?.version;
  const staged = status?.staged;
  const result = status?.last_result;
  const busy = installing || !!status?.pending;
  const auto = status?.auto_update;
  const autoColor: Record<string, string> = {
    current: "green", building: "blue", installing: "blue", waiting: "yellow", failed: "red", error: "red", disabled: "gray",
  };
  const autoLabel: Record<string, string> = {
    current: "aktuell", building: "baut", installing: "spielt ein", waiting: "wartet", failed: "fehlgeschlagen", error: "Fehler",
    disabled: "ausgeschaltet",
  };

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

      {auto && (
        <Paper p="md">
          <Group justify="space-between" mb="xs">
            <Group gap="xs">
              <Title order={5}>Automatische Updates aus Git</Title>
              <Badge color="grape" variant="light">
                Entwicklungsumgebung
              </Badge>
            </Group>
            <Switch
              label={auto.enabled ? "eingeschaltet" : "ausgeschaltet"}
              checked={auto.enabled}
              disabled={setAutoUpdate.isPending}
              onChange={(e) => setAutoUpdate.mutate(e.currentTarget.checked, { onError: (err) => fail(err, "Umschalten fehlgeschlagen.") })}
            />
          </Group>
          <Stack gap="xs">
            <Row label="Repository" value={`${auto.repo ?? "-"} (${auto.branch ?? "-"}), Prüfung alle ${auto.interval_minutes ?? "?"} min`} />
            <Row
              label="Zustand"
              value={
                <Group gap="xs" wrap="nowrap" align="flex-start">
                  <Badge color={auto.enabled ? autoColor[auto.state ?? ""] ?? "gray" : "gray"} variant="light" style={{ flexShrink: 0 }}>
                    {auto.enabled ? autoLabel[auto.state ?? ""] ?? "unbekannt" : "ausgeschaltet"}
                  </Badge>
                  <Text size="sm">{auto.enabled ? auto.message : "Der Server prüft weiter, baut und installiert aber nichts."}</Text>
                </Group>
              }
            />
            <Row label="Zuletzt geprüft" value={formatDateTime(auto.last_check_at, "noch nie")} />
            <Row label="Zuletzt aktualisiert" value={formatDateTime(auto.last_update_at, "noch nie")} />
            <Text size="xs" c="dimmed">
              Der Server baut bei jedem neuen Commit auf diesem Branch selbst ein Image und spielt es ein, sobald keine Backups oder
              Restores laufen. Einrichten und Entfernen nur auf dem Server (<Code>hvnb-git-autoupdate</Code>).
            </Text>
          </Stack>
        </Paper>
      )}

      {registry && (
        <Paper p="md">
          <Group justify="space-between" mb="xs">
            <Title order={5}>Online-Update aus der Registry</Title>
            <Switch
              label={registry.auto_enabled ? "automatisch einspielen" : "nur auf Knopfdruck"}
              checked={registry.auto_enabled}
              disabled={setRegistryAuto.isPending}
              onChange={(e) => setRegistryAuto.mutate(e.currentTarget.checked, { onError: (err) => fail(err, "Umschalten fehlgeschlagen.") })}
            />
          </Group>
          <Stack gap="xs">
            <Row label="Registry" value={registry.repo} />
            <Row
              label="Neueste Version dort"
              value={
                registry.error ? (
                  <Text size="sm" c="red">
                    {registry.error}
                  </Text>
                ) : registry.latest_version ? (
                  <Group gap="xs">
                    <Text size="sm">{registry.latest_version}</Text>
                    <Badge color={newerAvailable ? "blue" : "green"} variant="light">
                      {newerAvailable ? "Update verfügbar" : "aktuell"}
                    </Badge>
                  </Group>
                ) : (
                  "noch nicht geprüft"
                )
              }
            />
            <Row label="Zuletzt geprüft" value={formatDateTime(registry.checked_at, "noch nie")} />
            <Group mt="xs">
              <Button
                variant="default"
                leftSection={<IconRefresh size={16} />}
                loading={checkRegistry.isPending || checking}
                disabled={busy || !status?.agent_active}
                onClick={() => {
                  const before = registryCheckedAt ?? null;
                  checkRegistry.mutate(undefined, {
                    onSuccess: () => setCheckingSince(before),
                    onError: (err) => fail(err, "Die Suche konnte nicht gestartet werden."),
                  });
                }}
              >
                Nach Update suchen
              </Button>
              {newerAvailable && registry.latest_version && (
                <Button
                  leftSection={<IconCloudDownload size={16} />}
                  loading={installFromRegistry.isPending}
                  disabled={busy || !status?.agent_active}
                  onClick={() => startRegistryInstall(registry.latest_version as string)}
                >
                  Version {registry.latest_version} einspielen
                </Button>
              )}
            </Group>
            <Text size="xs" c="dimmed">
              Der Server holt das Image selbst aus der Registry; die App gibt nur den Anstoß und kennt keine Zugangsdaten.
              {registry.auto_enabled
                ? " Automatisch: der Server prüft stündlich und spielt eine neuere Version ein, sobald keine Backups oder Restores laufen."
                : ""}
            </Text>
          </Stack>
        </Paper>
      )}

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
      <Paper p="md">
        <Title order={5} mb="xs">
          So wird diese Installation aktualisiert
        </Title>
        <Text size="sm" c="dimmed" mb="xs">
          Die App lädt selbst nichts nach. Jedes Update tauscht das komplette Image aus; vorher wird die Datenbank gesichert, und wenn die
          neue Version nicht startet, läuft die vorherige weiter. Befehle gelten auf dem Server, als der Linux-Benutzer des Containers.
        </Text>
        <Accordion variant="separated" chevronPosition="left">
          <Accordion.Item value="cli">
            <Accordion.Control>Paketdatei auf dem Server einspielen (immer möglich, ohne Internet)</Accordion.Control>
            <Accordion.Panel>
              <List size="sm" type="ordered" spacing={4}>
                <List.Item>
                  Paket <Code>hvnb-&lt;Version&gt;.tar.gz</Code>, die Datei <Code>.sha256</Code> und <Code>hvnb-update</Code> auf den Server
                  kopieren.
                </List.Item>
                <List.Item>
                  <Code>hvnb-update hvnb-&lt;Version&gt;.tar.gz</Code>
                </List.Item>
                <List.Item>
                  Zurück auf die vorherige Version: <Code>hvnb-update --rollback</Code> (mit <Code>--with-db</Code> samt Datenbank).
                </List.Item>
              </List>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value="upload">
            <Accordion.Control>Paketdatei hier hochladen (ohne Internet, ohne Shell)</Accordion.Control>
            <Accordion.Panel>
              <List size="sm" type="ordered" spacing={4}>
                <List.Item>
                  Einmalig auf dem Server: <Code>hvnb-update --install-agent</Code> – oben erscheint der Update-Dienst als „aktiv“.
                </List.Item>
                <List.Item>Paketdatei und Prüfsummendatei oben hochladen, dann „Jetzt einspielen“.</List.Item>
              </List>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value="registry">
            <Accordion.Control>Online-Update aus der Registry (Server braucht Zugang zur Registry)</Accordion.Control>
            <Accordion.Panel>
              <List size="sm" type="ordered" spacing={4}>
                <List.Item>
                  Einmalig auf dem Server: bei privaten Images <Code>podman login ghcr.io</Code>, dann{" "}
                  <Code>hvnb-update --set-registry ghcr.io/&lt;konto&gt;/hvnb-backup</Code> und <Code>hvnb-update --install-agent</Code>.
                </List.Item>
                <List.Item>Danach hier „Nach Update suchen“ und die angebotene Version einspielen – oder automatisch per Schalter.</List.Item>
                <List.Item>
                  Auf dem Server direkt: <Code>hvnb-update --check-registry</Code>, <Code>hvnb-update --from-registry</Code>.
                </List.Item>
              </List>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item value="git">
            <Accordion.Control>Automatisch aus Git (nur Entwicklungsumgebung)</Accordion.Control>
            <Accordion.Panel>
              <List size="sm" type="ordered" spacing={4}>
                <List.Item>
                  Einmalig auf dem Server: <Code>hvnb-git-autoupdate --install --repo &lt;Git-Adresse&gt; --branch master</Code>
                </List.Item>
                <List.Item>
                  Der Server baut bei jedem neuen Commit selbst ein Image und spielt es ein; ein/aus per Schalter oben. Stand:{" "}
                  <Code>hvnb-git-autoupdate --status</Code>
                </List.Item>
              </List>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      </Paper>
    </Stack>
  );
}
