import { useEffect, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  FileButton,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  PasswordInput,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconDatabaseImport,
  IconPlayerPlay,
  IconPlugConnected,
  IconRefresh,
  IconRestore,
} from "@tabler/icons-react";
import { useNavigate } from "react-router-dom";

import {
  useDbBackupConfig,
  useDbBackupList,
  usePreviewDbRestore,
  useRunDbBackup,
  useRunDbRestore,
  useSaveDbBackupConfig,
  useTestDbBackupTarget,
  type RestoreSource,
} from "@/api/hooks.dbBackup";
import type { DbBackupFile, DbRestorePreview } from "@/api/types";
import { useAuthStore } from "@/store/authStore";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatDateTime } from "@/utils/format";

// Settings > DB-Sicherung (Backlog #66): taegliche Sicherung der App-
// Datenbank auf eine CIFS-Freigabe + 3 lokale Kopien, Wiederherstellen per
// GUI. Logik siehe backend app.core.db_backup.
export function DbBackupTab() {
  return (
    <Stack maw={1100}>
      <ConfigSection />
      <BackupsSection />
    </Stack>
  );
}

function utcHourToLocal(hour: number): string {
  const d = new Date();
  d.setUTCHours(hour, 30, 0, 0);
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function ConfigSection() {
  const { data: config } = useDbBackupConfig();
  const save = useSaveDbBackupConfig();
  const test = useTestDbBackupTarget();
  const runNow = useRunDbBackup();

  const [enabled, setEnabled] = useState(false);
  const [instanceName, setInstanceName] = useState("hvnb");
  const [sharePath, setSharePath] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [hour, setHour] = useState<number | string>(1);
  const [retention, setRetention] = useState<number | string>(30);
  const [localKeep, setLocalKeep] = useState<number | string>(3);

  useEffect(() => {
    if (!config) return;
    setEnabled(config.enabled);
    setInstanceName(config.instance_name);
    setSharePath(config.share_path);
    setUsername(config.username);
    setHour(config.hour_utc);
    setRetention(config.retention_days);
    setLocalKeep(config.local_keep);
  }, [config]);

  const onError = (fallback: string) => (err: unknown) =>
    notifications.show({ title: "Fehler", message: apiErrorMessage(err, fallback), color: "red" });

  function handleSave() {
    return save.mutateAsync({
      enabled,
      instance_name: instanceName,
      share_path: sharePath,
      username,
      // leer = unveraendert lassen (Kennwort wird nie angezeigt)
      password: password ? password : undefined,
      hour_utc: Number(hour),
      retention_days: Number(retention),
      local_keep: Number(localKeep),
    });
  }

  function handleSaveClick() {
    handleSave()
      .then(() => {
        setPassword("");
        notifications.show({ title: "Gespeichert", message: "Einstellungen der DB-Sicherung aktualisiert", color: "green" });
      })
      .catch(onError("Einstellungen konnten nicht gespeichert werden."));
  }

  function handleTest() {
    // Vorher speichern, damit mit den eingegebenen Werten getestet wird.
    handleSave()
      .then(() => {
        setPassword("");
        return test.mutateAsync();
      })
      .then((res) => notifications.show({ title: "Verbindung OK", message: res.message, color: "green" }))
      .catch(onError("Verbindungstest fehlgeschlagen."));
  }

  function handleRunNow() {
    runNow.mutate(undefined, {
      onSuccess: (res) =>
        res.last_error
          ? notifications.show({ title: "Sicherung mit Fehler", message: res.last_error, color: "red" })
          : notifications.show({ title: "Sicherung erstellt", message: res.last_file_name ?? "", color: "green" }),
      onError: onError("Sicherung fehlgeschlagen."),
    });
  }

  return (
    <Paper p="md">
      <Title order={5} mb={4}>
        DB-Sicherung
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Sichert die komplette Datenbank dieser Anwendung (Einrichtung, Backup-Katalog, Historie) täglich auf eine CIFS-Freigabe
        und behält zusätzlich die letzten Kopien lokal. Die Kopie ist im laufenden Betrieb konsistent, auch während Backups
        laufen. Auf der Freigabe werden nur Dateien nach dem Muster <code>hvnb-db-…sqlite.gz</code> mit der Kennung dieser
        Installation gelöscht.
      </Text>
      <Alert color="yellow" icon={<IconAlertTriangle size={16} />} mb="md">
        Die gespeicherten Kennwörter in der Sicherung sind mit <code>HVNB_SECRET_KEY</code> aus der <code>.env</code>{" "}
        verschlüsselt. Der Schlüssel wird bewusst <b>nicht</b> mitgesichert -- bitte getrennt verwahren (z. B. Passwort-Safe).
        Ohne ihn lässt sich eine Sicherung nur ohne Kennwörter nutzen. Die Freigabe sollte nur für das hier eingetragene Konto
        zugänglich sein.
      </Alert>
      <Stack gap="sm">
        <Switch label="Tägliche Sicherung aktiv" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />
        <TextInput
          label="Kennung dieser Installation"
          description={`Steht im Dateinamen (hvnb-db-${instanceName || "…"}-JJJJMMTT-HHMMSS.sqlite.gz). Die Aufbewahrung löscht auf der Freigabe nur Sicherungen mit dieser Kennung -- teilen sich mehrere Installationen einen Ordner, bekommt jede eine eigene. Nur Buchstaben, Ziffern, Bindestrich.`}
          placeholder="z. B. prod"
          maxLength={40}
          value={instanceName}
          onChange={(e) => setInstanceName(e.currentTarget.value)}
        />
        <TextInput
          label="Ziel (UNC-Pfad)"
          placeholder="\\fileserver\backup\hvnb"
          value={sharePath}
          onChange={(e) => setSharePath(e.currentTarget.value)}
        />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Benutzer"
            placeholder="DOMAIN\svc-hvnb-dbbackup"
            value={username}
            onChange={(e) => setUsername(e.currentTarget.value)}
          />
          <PasswordInput
            label="Kennwort"
            placeholder={config?.password_set ? "unverändert (gespeichert)" : ""}
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
          />
          <NumberInput
            label="Uhrzeit (Stunde, UTC)"
            description={`Täglich um ${String(Number(hour)).padStart(2, "0")}:30 UTC = ${utcHourToLocal(Number(hour))} Ortszeit`}
            min={0}
            max={23}
            value={hour}
            onChange={setHour}
          />
          <NumberInput
            label="Aufbewahrung auf der Freigabe"
            description="Ältere Sicherungen dieses Servers werden gelöscht"
            min={1}
            max={3650}
            suffix=" Tage"
            value={retention}
            onChange={setRetention}
          />
          <NumberInput
            label="Lokale Kopien"
            description="Zusätzlich im Datenverzeichnis des Containers, 0 = keine"
            min={0}
            max={30}
            value={localKeep}
            onChange={setLocalKeep}
          />
        </SimpleGrid>
        <Group justify="flex-end">
          <Button variant="default" leftSection={<IconPlugConnected size={16} />} onClick={handleTest} loading={test.isPending}>
            Verbindung testen
          </Button>
          <Button
            variant="light"
            leftSection={<IconPlayerPlay size={16} />}
            onClick={handleRunNow}
            loading={runNow.isPending}
            disabled={!config?.share_path}
          >
            Jetzt sichern
          </Button>
          <Button onClick={handleSaveClick} loading={save.isPending}>
            Speichern
          </Button>
        </Group>
        {config && <StatusView />}
      </Stack>
    </Paper>
  );
}

function StatusView() {
  const { data: config } = useDbBackupConfig();
  if (!config) return null;
  const failed = !!config.last_error;
  return (
    <Alert color={failed ? "red" : config.last_success_at ? "green" : "gray"} variant="light">
      <Stack gap={2}>
        <Text size="sm">
          Letzte erfolgreiche Sicherung: <b>{config.last_success_at ? formatDateTime(config.last_success_at) : "noch keine"}</b>
          {config.last_file_name && !failed ? ` -- ${config.last_file_name} (${formatBytes(config.last_size_bytes)})` : ""}
        </Text>
        {config.last_attempt_at && (
          <Text size="xs" c="dimmed">
            Letzter Versuch: {formatDateTime(config.last_attempt_at)}
          </Text>
        )}
        {failed && (
          <Text size="sm" c="red">
            {config.last_error}
          </Text>
        )}
      </Stack>
    </Alert>
  );
}

function BackupsSection() {
  const [load, setLoad] = useState(false);
  const { data: list, isFetching, refetch } = useDbBackupList(load);
  const [restoreSource, setRestoreSource] = useState<RestoreSource | null>(null);

  const rows: { source: "share" | "local"; file: DbBackupFile }[] = [
    ...(list?.share ?? []).map((file) => ({ source: "share" as const, file })),
    ...(list?.local ?? []).map((file) => ({ source: "local" as const, file })),
  ].sort((a, b) => b.file.created_at.localeCompare(a.file.created_at));

  return (
    <Paper p="md">
      <Group justify="space-between" mb={4}>
        <Title order={5}>Sicherungen & Wiederherstellung</Title>
        <Group gap="xs">
          <FileButton onChange={(file) => file && setRestoreSource({ kind: "upload", file })} accept=".gz,application/gzip">
            {(props) => (
              <Button {...props} size="xs" variant="default" leftSection={<IconDatabaseImport size={14} />}>
                Sicherungsdatei hochladen
              </Button>
            )}
          </FileButton>
          <Tooltip label={load ? "Neu laden" : "Sicherungen anzeigen"}>
            <ActionIcon variant="light" onClick={() => (load ? refetch() : setLoad(true))} loading={isFetching}>
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      <Text size="xs" c="dimmed" mb="md">
        Beim Wiederherstellen wird die komplette Datenbank ersetzt und die Anwendung neu gestartet. Der aktuelle Stand wird vorher
        automatisch lokal gesichert (<code>…-vor-restore</code>).
      </Text>
      {!load ? (
        <Button size="xs" variant="light" onClick={() => setLoad(true)}>
          Sicherungen anzeigen
        </Button>
      ) : (
        <>
          {list?.share_error && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} mb="sm">
              Freigabe nicht lesbar: {list.share_error}
            </Alert>
          )}
          {isFetching && !list && <Loader size="sm" />}
          {list && rows.length === 0 && (
            <Text size="sm" c="dimmed">
              Noch keine Sicherungen vorhanden.
            </Text>
          )}
          {rows.length > 0 && (
            <Table striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Zeitpunkt</Table.Th>
                  <Table.Th>Ablage</Table.Th>
                  <Table.Th>Datei</Table.Th>
                  <Table.Th>Größe</Table.Th>
                  <Table.Th w={60} />
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map(({ source, file }) => (
                  <Table.Tr key={`${source}:${file.name}`}>
                    <Table.Td>
                      <Text size="sm">{formatDateTime(file.created_at)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4}>
                        <Badge size="sm" variant="light" color={source === "share" ? "blue" : "gray"}>
                          {source === "share" ? "Freigabe" : "Lokal"}
                        </Badge>
                        {file.pre_restore && (
                          <Badge size="sm" variant="light" color="orange">
                            vor Wiederherstellung
                          </Badge>
                        )}
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs" ff="monospace">
                        {file.name}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{formatBytes(file.size_bytes)}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label="Wiederherstellen">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={() => setRestoreSource({ kind: "stored", source, name: file.name })}
                        >
                          <IconRestore size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </>
      )}
      <RestoreModal source={restoreSource} onClose={() => setRestoreSource(null)} />
    </Paper>
  );
}

function sourceLabel(source: RestoreSource): string {
  return source.kind === "upload" ? source.file.name : source.name;
}

function RestoreModal({ source, onClose }: { source: RestoreSource | null; onClose: () => void }) {
  const preview = usePreviewDbRestore();
  const restore = useRunDbRestore();
  const [confirm, setConfirm] = useState("");
  const [restarting, setRestarting] = useState(false);
  const navigate = useNavigate();
  const logout = useAuthStore((s) => s.logout);

  useEffect(() => {
    setConfirm("");
    setRestarting(false);
    preview.reset();
    if (source) preview.mutate(source);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  function handleRestore() {
    if (!source) return;
    restore.mutate(
      { source, confirm },
      {
        onSuccess: () => {
          setRestarting(true);
          waitForRestart().then(() => {
            logout();
            navigate("/login");
          });
        },
        onError: (err) =>
          notifications.show({
            title: "Fehler",
            message: apiErrorMessage(err, "Wiederherstellung fehlgeschlagen."),
            color: "red",
          }),
      },
    );
  }

  const data: DbRestorePreview | undefined = preview.data;
  const blocked = !!data && (data.blockers.length > 0 || data.running_jobs.length > 0);

  return (
    <Modal
      opened={source !== null}
      onClose={restore.isPending || restarting ? () => undefined : onClose}
      withCloseButton={!restore.isPending && !restarting}
      title="Datenbank wiederherstellen"
      size="lg"
    >
      {source && (
        <Stack gap="sm">
          <Text size="sm">
            Sicherung: <b>{sourceLabel(source)}</b>
          </Text>
          {restarting ? (
            <Alert color="blue" icon={<Loader size="xs" />}>
              Datenbank wiederhergestellt -- die Anwendung startet neu. Danach geht es automatisch zur Anmeldung.
            </Alert>
          ) : (
            <>
              {preview.isPending && (
                <Group gap="xs">
                  <Loader size="xs" />
                  <Text size="sm">Sicherung wird geladen und geprüft…</Text>
                </Group>
              )}
              {preview.isError && (
                <Alert color="red">{apiErrorMessage(preview.error, "Sicherung konnte nicht geprüft werden.")}</Alert>
              )}
              {data && (
                <>
                  <Table withTableBorder maw={420}>
                    <Table.Tbody>
                      {Object.entries(data.counts).map(([label, count]) => (
                        <Table.Tr key={label}>
                          <Table.Td>
                            <Text size="sm">{label}</Text>
                          </Table.Td>
                          <Table.Td ta="right">
                            <Text size="sm">{count}</Text>
                          </Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                  <Text size="xs" c="dimmed">
                    Letzter Backup-Lauf in der Sicherung: {data.latest_backup_run_at ?? "keiner"} (UTC) · Kennwörter mit dem
                    Schlüssel dieser Installation lesbar:{" "}
                    {data.secret_key_ok === null || data.secret_key_ok === undefined
                      ? "keine gespeichert"
                      : data.secret_key_ok
                        ? "ja"
                        : "nein"}
                  </Text>
                  {data.blockers.map((b) => (
                    <Alert key={b} color="red" icon={<IconAlertTriangle size={16} />}>
                      {b}
                    </Alert>
                  ))}
                  {data.running_jobs.length > 0 && (
                    <Alert color="orange" icon={<IconAlertTriangle size={16} />}>
                      Es läuft noch: {data.running_jobs.join(", ")} -- bitte abwarten bzw. beenden, dann erneut versuchen.
                    </Alert>
                  )}
                  {!blocked && (
                    <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
                      <Stack gap={6}>
                        <Text size="sm">
                          Die komplette aktuelle Datenbank wird durch diese Sicherung ersetzt: alle Änderungen, Backup-Läufe,
                          Alarme und Logs seit dem Sicherungszeitpunkt gehen verloren (der aktuelle Stand wird vorher lokal
                          gesichert). Die Anwendung startet danach neu, alle Benutzer müssen sich neu anmelden.
                        </Text>
                        <TextInput
                          label={`Zur Bestätigung ${data.confirm_word} eingeben`}
                          value={confirm}
                          onChange={(e) => setConfirm(e.currentTarget.value)}
                        />
                      </Stack>
                    </Alert>
                  )}
                </>
              )}
              <Group justify="flex-end">
                <Button variant="default" onClick={onClose} disabled={restore.isPending}>
                  Abbrechen
                </Button>
                <Button
                  color="red"
                  onClick={handleRestore}
                  loading={restore.isPending}
                  disabled={!data || blocked || confirm.trim() !== data.confirm_word}
                >
                  Wiederherstellen
                </Button>
              </Group>
            </>
          )}
        </Stack>
      )}
    </Modal>
  );
}

// Wartet, bis die App nach dem Neustart wieder antwortet: erst muss sie
// kurz weg sein (sonst antwortet noch der alte Prozess), dann wieder da.
async function waitForRestart(): Promise<void> {
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
  const healthy = async () => {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      return response.ok;
    } catch {
      return false;
    }
  };
  let wentDown = false;
  for (let i = 0; i < 90; i++) {
    await sleep(2000);
    const ok = await healthy();
    if (!ok) wentDown = true;
    if (ok && (wentDown || i > 10)) return;
  }
}
