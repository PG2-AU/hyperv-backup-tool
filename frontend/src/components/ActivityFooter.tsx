import { useEffect, useRef, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Progress,
  ScrollArea,
  SegmentedControl,
  Stack,
  Stepper,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconChevronDown, IconChevronUp, IconX } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import { useAuthStore } from "@/store/authStore";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

// Aktivitaeten-Fusszeile (Backlog #83, Nutzer-Vorgabe 2026-10-03): wie
// "Kuerzlich bearbeitete Aufgaben" im vCenter -- alle laufenden und die
// Ablaeufe der letzten 24 Stunden (Backups, Restores, Verschiebungen,
// Anlegen/Loeschen, VM-Power ...). Einklappbar; Klick auf eine Zeile zeigt
// das Schritt-Protokoll, bei offener Rueckfrage samt Zurueckrollen/Behalten.

export interface Activity {
  kind: string;
  id: string;
  task: string;
  target: string;
  status: "running" | "succeeded" | "warning" | "failed" | "cleaned_up" | "cancelled";
  detail?: string | null;
  progress_percent?: number | null;
  initiator: string;
  started_at: string;
  finished_at?: string | null;
  needs_decision: boolean;
}

interface ActivityDetail {
  activity: Activity;
  steps: { label: string; status: string; message?: string | null }[];
  error_message?: string | null;
  rollback_path?: string | null;
  keep_path?: string | null;
  cancel_path?: string | null;
  cancel_requested?: boolean;
}

export const ACTIVITY_FOOTER_COLLAPSED = 34;
export const ACTIVITY_FOOTER_EXPANDED = 280;

const STATUS: Record<Activity["status"], { label: string; color: string }> = {
  running: { label: "Läuft", color: "blue" },
  succeeded: { label: "Abgeschlossen", color: "green" },
  warning: { label: "Mit Warnungen", color: "yellow" },
  failed: { label: "Fehlgeschlagen", color: "red" },
  cleaned_up: { label: "Zurückgerollt", color: "gray" },
  cancelled: { label: "Abgebrochen", color: "gray" },
};

function formatTime(value?: string | null): string {
  if (!value) return "–";
  const date = new Date(value);
  const today = new Date().toDateString() === date.toDateString();
  return today ? date.toLocaleTimeString("de-DE") : date.toLocaleString("de-DE");
}

function formatDuration(a: Activity): string {
  const end = a.finished_at ? new Date(a.finished_at).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(a.started_at).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes} min ${seconds % 60} s` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function useActivities() {
  return useQuery({
    queryKey: ["activities"],
    queryFn: async () => (await apiClient.get<Activity[]>("/activities")).data,
    // Solange etwas laeuft engmaschig, sonst gemaechlich.
    refetchInterval: (query) => (query.state.data?.some((a) => a.status === "running") ? 3000 : 20000),
  });
}

function StatusCell({ a }: { a: Activity }) {
  const s = STATUS[a.status] ?? { label: a.status, color: "gray" };
  return (
    <Stack gap={2}>
      <Group gap={4} wrap="nowrap">
        {a.status === "running" ? (
          <Loader size={12} />
        ) : a.status === "succeeded" ? (
          <IconCheck size={14} color="var(--mantine-color-green-6)" />
        ) : a.status === "failed" ? (
          <IconX size={14} color="var(--mantine-color-red-6)" />
        ) : a.status === "warning" ? (
          <IconAlertTriangle size={14} color="var(--mantine-color-yellow-7)" />
        ) : null}
        <Text size="xs" c={a.status === "failed" ? "red" : undefined}>
          {s.label}
        </Text>
        {a.needs_decision && (
          <Badge size="xs" color="orange" variant="light">
            Rückfrage offen
          </Badge>
        )}
      </Group>
      {a.status === "running" && a.progress_percent != null && <Progress value={a.progress_percent} size="xs" w={120} />}
    </Stack>
  );
}

// Endet ein Ablauf: betroffene Listen neu laden (u.a. Dashboard-/Kalender-
// Zeitstrahl -- uebernommen von der frueheren Backup-Anzeige in der
// Kopfzeile) und bei eigenen Ablaeufen eine Meldung zeigen, damit man ein im
// Hintergrund weiterlaufendes Ergebnis nicht verpasst (VM-Power meldet selbst).
const INVALIDATE_ON_FINISH = ["job-runs", "vms", "csvs", "smb-shares", "volumes", "luns", "resource-groups", "backups", "alerts"];

function useFinishWatcher(activities: Activity[] | undefined) {
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const previous = useRef<Map<string, Activity> | null>(null);
  useEffect(() => {
    if (!activities) return;
    const current = new Map(activities.map((a) => [`${a.kind}:${a.id}`, a]));
    const before = previous.current;
    previous.current = current;
    if (!before) return;
    const finished = [...before.values()].filter((old) => {
      const now = current.get(`${old.kind}:${old.id}`);
      return old.status === "running" && (!now || now.status !== "running");
    });
    if (finished.length === 0) return;
    INVALIDATE_ON_FINISH.forEach((key) => queryClient.invalidateQueries({ queryKey: [key] }));
    const mine = new Set([user?.display_name, user?.username].filter(Boolean));
    for (const old of finished) {
      const now = current.get(`${old.kind}:${old.id}`);
      if (!now || old.kind === "vm_power" || !mine.has(now.initiator)) continue;
      const s = STATUS[now.status] ?? { label: now.status, color: "gray" };
      notifications.show({
        title: `${now.task}: ${s.label}`,
        message: `${now.target}${now.needs_decision ? " -- Rückfrage offen, siehe Aktivitäten" : ""}`,
        color: s.color,
        autoClose: now.status === "succeeded" ? 6000 : false,
      });
    }
  }, [activities, queryClient, user]);
}

export function ActivityFooter({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const { data: activities } = useActivities();
  useFinishWatcher(activities);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<Activity | null>(null);
  const items = activities ?? [];
  const running = items.filter((a) => a.status === "running");
  const lastFinished = items.find((a) => a.status !== "running");
  const decisions = items.filter((a) => a.needs_decision).length;
  const shown = items.filter((a) =>
    filter === "running" ? a.status === "running" : filter === "problems" ? ["failed", "warning"].includes(a.status) || a.needs_decision : true,
  );

  return (
    <Stack gap={0} h="100%">
      <Group h={ACTIVITY_FOOTER_COLLAPSED} px="md" gap="sm" wrap="nowrap" style={{ cursor: "pointer", flexShrink: 0 }} onClick={onToggle}>
        <ActionIcon variant="subtle" size="sm" aria-label={open ? "Einklappen" : "Ausklappen"}>
          {open ? <IconChevronDown size={16} /> : <IconChevronUp size={16} />}
        </ActionIcon>
        <Text size="sm" fw={600}>
          Aktivitäten
        </Text>
        {running.length > 0 ? (
          <Badge color="blue" variant="light" leftSection={<Loader size={10} color="blue" />}>
            {running.length} laufend
          </Badge>
        ) : (
          <Text size="xs" c="dimmed">
            nichts läuft
          </Text>
        )}
        {decisions > 0 && (
          <Badge color="orange" variant="light">
            {decisions} Rückfrage(n) offen
          </Badge>
        )}
        {!open && (running[0] ?? lastFinished) && (
          <Text size="xs" c="dimmed" truncate style={{ minWidth: 0 }}>
            {running[0]
              ? `${running[0].task} · ${running[0].target}${running[0].detail ? ` -- ${running[0].detail}` : ""}`
              : `zuletzt: ${lastFinished!.task} · ${lastFinished!.target} -- ${STATUS[lastFinished!.status]?.label ?? lastFinished!.status}`}
          </Text>
        )}
        {open && (
          <Group ml="auto" onClick={(e) => e.stopPropagation()}>
            <SegmentedControl
              size="xs"
              value={filter}
              onChange={setFilter}
              data={[
                { value: "all", label: `Alle (${items.length})` },
                { value: "running", label: `Laufend (${running.length})` },
                { value: "problems", label: "Fehler/Warnungen" },
              ]}
            />
          </Group>
        )}
      </Group>
      {open && (
        <ScrollArea style={{ flex: 1, minHeight: 0 }} px="md">
          <Table striped highlightOnHover fz="xs" stickyHeader verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Aufgabe</Table.Th>
                <Table.Th>Ziel</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Details</Table.Th>
                <Table.Th>Initiator</Table.Th>
                <Table.Th>Start</Table.Th>
                <Table.Th>Ende</Table.Th>
                <Table.Th>Dauer</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {shown.map((a) => (
                <Table.Tr
                  key={`${a.kind}-${a.id}`}
                  onClick={() => a.kind !== "vm_power" && setSelected(a)}
                  style={{ cursor: a.kind !== "vm_power" ? "pointer" : undefined }}
                >
                  <Table.Td>{a.task}</Table.Td>
                  <Table.Td>{a.target}</Table.Td>
                  <Table.Td>
                    <StatusCell a={a} />
                  </Table.Td>
                  <Table.Td maw={420}>
                    <Text size="xs" c={a.status === "failed" ? "red" : "dimmed"} lineClamp={1}>
                      {a.detail ?? ""}
                    </Text>
                  </Table.Td>
                  <Table.Td>{a.initiator}</Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{formatTime(a.started_at)}</Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{formatTime(a.finished_at)}</Table.Td>
                  <Table.Td style={{ whiteSpace: "nowrap" }}>{formatDuration(a)}</Table.Td>
                </Table.Tr>
              ))}
              {shown.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={8}>
                    <Text size="xs" c="dimmed" ta="center" py="sm">
                      Keine Aktivitäten in den letzten 24 Stunden.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      )}
      <ActivityDetailModal activity={selected} onClose={() => setSelected(null)} />
    </Stack>
  );
}

function ActivityDetailModal({ activity, onClose }: { activity: Activity | null; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<"rollback" | "keep" | "cancel" | null>(null);
  const { data } = useQuery({
    queryKey: ["activity", activity?.kind, activity?.id],
    queryFn: async () => (await apiClient.get<ActivityDetail>(`/activities/${activity!.kind}/${activity!.id}`)).data,
    enabled: !!activity,
    refetchInterval: (query) => (query.state.data?.activity.status === "running" ? 2000 : false),
  });

  function cancel(path: string) {
    confirmAction({
      title: "Ablauf abbrechen",
      message:
        "Wirklich abbrechen? Ein Backup stoppt nach dem aktuellen Schritt, bereits erstellte Checkpoints werden aufgeräumt; ein Storage-Move bricht die Kopie ab, die VM bleibt am bisherigen Ort.",
      confirmLabel: "Abbrechen",
      color: "red",
      onConfirm: () => decide("cancel", path),
    });
  }

  function decide(kind: "rollback" | "keep" | "cancel", path: string) {
    setBusy(kind);
    apiClient
      .post(path)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: ["activity"] });
        queryClient.invalidateQueries({ queryKey: ["activities"] });
      })
      .catch((err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Aktion fehlgeschlagen."), color: "red" }))
      .finally(() => setBusy(null));
  }

  const a = data?.activity ?? activity;
  const steps = data?.steps ?? [];
  const active = steps.findIndex((s) => s.status === "running");
  return (
    <Modal opened={!!activity} onClose={onClose} title={a ? `${a.task}: ${a.target}` : ""} size="lg">
      {!data ? (
        <Loader size="sm" />
      ) : (
        <Stack gap="md">
          <Group gap="lg">
            <StatusCell a={data.activity} />
            <Text size="xs" c="dimmed">
              {data.activity.initiator} · Start {formatTime(data.activity.started_at)} · Dauer {formatDuration(data.activity)}
            </Text>
          </Group>
          {steps.length > 0 && (
            <Stepper
              active={active === -1 ? steps.length : active}
              size="sm"
              orientation="vertical"
              allowNextStepsSelect={false}
            >
              {steps.map((s, i) => (
                <Stepper.Step
                  key={`${i}-${s.label}`}
                  label={s.label}
                  description={s.message && s.message !== "OK" ? s.message : undefined}
                  color={s.status === "error" ? "red" : undefined}
                  loading={s.status === "running"}
                  completedIcon={s.status === "error" ? <IconX size={16} /> : <IconCheck size={16} />}
                />
              ))}
            </Stepper>
          )}
          {data.cancel_path && (
            <Group justify="flex-end">
              <Button
                color="red"
                variant="light"
                size="xs"
                loading={busy === "cancel"}
                disabled={data.cancel_requested}
                onClick={() => cancel(data.cancel_path!)}
              >
                {data.cancel_requested ? "Abbruch angefordert" : "Ablauf abbrechen"}
              </Button>
            </Group>
          )}
          {data.error_message && data.activity.status !== "running" && (
            <Alert color={data.activity.status === "warning" ? "yellow" : "red"}>{data.error_message}</Alert>
          )}
          {data.rollback_path && data.keep_path && (
            <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Angelegte Objekte zurückrollen?">
              <Text size="sm" mb="sm">
                Dieser Lauf ist fehlgeschlagen und hat bereits Objekte angelegt. „Zurückrollen“ entfernt genau diese, „Behalten“
                lässt sie stehen.
              </Text>
              <Group>
                <Button color="red" loading={busy === "rollback"} onClick={() => decide("rollback", data.rollback_path!)}>
                  Zurückrollen
                </Button>
                <Button variant="default" loading={busy === "keep"} onClick={() => decide("keep", data.keep_path!)}>
                  Behalten
                </Button>
              </Group>
            </Alert>
          )}
          <Group justify="flex-end">
            <Tooltip label="Schließen">
              <Button variant="default" onClick={onClose}>
                Schließen
              </Button>
            </Tooltip>
          </Group>
        </Stack>
      )}
    </Modal>
  );
}
