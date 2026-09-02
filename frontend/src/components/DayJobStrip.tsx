import { useMemo } from "react";
import { ActionIcon, Group, Paper, Text, Title } from "@mantine/core";
import { IconX } from "@tabler/icons-react";

import { JobTimelineTrack, type TimelineEntry } from "@/components/JobTimelineTrack";
import type { BackupJobRun, UpcomingJob } from "@/api/types";

const MIN_BAR_MS = 15 * 60 * 1000; // Mindestbreite, damit sehr kurze/laufende Balken sichtbar bleiben
const POINT_MARKER_MS = 20 * 60 * 1000; // nominale Breite fuer geplante (noch nicht gelaufene) Vorkommen

function statusColor(status: BackupJobRun["status"]): string {
  if (status === "succeeded") return "green";
  if (status === "failed" || status === "cleaned_up_after_failure") return "red";
  return "blue"; // running/pending/cleaning_up -- noch kein abschliessendes Ergebnis, wie ein geplanter Job
}

function startOfDay(d: Date): number {
  const r = new Date(d);
  r.setHours(0, 0, 0, 0);
  return r.getTime();
}

/** Kompakter Tages-Zeitstrahl (00:00-24:00, ohne Liste darunter -- siehe
 * JobTimelineTrack showList=false) fuer einen schnellen Ueberblick: nur
 * farbige Marker, tatsaechliche Laeufe des Tages nach Ergebnis (gruen/rot),
 * noch ausstehende geplante Vorkommen (Zeitpunkt in der Zukunft) blau.
 * Details ausschliesslich per Hover. Gemeinsam genutzt vom Dashboard
 * (`day` weggelassen -- default heute, kein `onClose`) und der Backup-
 * Kalenderansicht (Tagesauswahl im Monatskalender, `day`+`onClose` gesetzt)
 * -- eine Komponente statt zwei fast identischer Implementierungen, damit
 * beide Ansichten garantiert gleich aussehen und sich bleiben. */
export function DayJobStrip({
  day,
  runs,
  scheduled,
  title = "Backup-Zeitstrahl",
  onClose,
}: {
  /** Tag, dessen 00:00-24:00-Fenster angezeigt wird -- Default: heute
   * (Dashboard-Nutzung). Fuer Vergangenheits-/Zukunftstage (Kalender)
   * zeigt sich automatisch nur die jeweils sinnvolle Haelfte: an
   * vergangenen Tagen bleiben keine "geplanten" Vorkommen mehr (die
   * Zukunfts-Filterung unten greift schon), an zukuenftigen Tagen gibt es
   * noch keine tatsaechlichen Laeufe. */
  day?: Date;
  runs: BackupJobRun[];
  scheduled: UpcomingJob[];
  title?: string;
  onClose?: () => void;
}) {
  const now = Date.now();
  const windowStart = useMemo(() => startOfDay(day ?? new Date()), [day]);
  const windowEnd = windowStart + 24 * 60 * 60 * 1000;

  const entries = useMemo(() => {
    const result: TimelineEntry[] = [];

    for (const run of runs) {
      const startMs = new Date(run.started_at).getTime();
      if (startMs < windowStart || startMs >= windowEnd) continue;
      const rawEndMs = run.finished_at ? new Date(run.finished_at).getTime() : Math.min(now, startMs + MIN_BAR_MS);
      const endMs = Math.max(rawEndMs, startMs + MIN_BAR_MS);
      const groupOrPolicyName = run.resource_group_name ?? run.job_name;
      result.push({
        key: `run-${run.id}`,
        label: groupOrPolicyName,
        color: statusColor(run.status),
        startMs,
        endMs,
        tooltip: `${new Date(startMs).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} — ${run.job_name}${
          run.resource_group_name ? ` / ${run.resource_group_name}` : ""
        }\n${run.status}${run.targets.length ? `\nZiele: ${run.targets.join(", ")}` : ""}`,
      });
    }

    for (const job of scheduled) {
      const startMs = new Date(job.next_run_at).getTime();
      if (startMs <= now) continue; // fuer die Vergangenheit zaehlt der tatsaechliche Lauf, siehe oben
      result.push({
        key: `upcoming-${job.resource_group_id}-${job.policy_id}-${job.next_run_at}`,
        label: job.resource_group_name,
        color: "blue",
        startMs,
        endMs: startMs + POINT_MARKER_MS,
        tooltip: `${new Date(startMs).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} — ${job.policy_name} / ${job.resource_group_name} (geplant)`,
      });
    }

    return result;
  }, [runs, scheduled, windowStart, windowEnd, now]);

  return (
    <Paper p="md" withBorder={!!onClose} mt={onClose ? "md" : undefined}>
      <Group justify="space-between" mb="sm" align="flex-end">
        <Title order={5}>{title}</Title>
        <Group gap="md">
          <ColorLegendDot color="green" label="Erfolgreich" />
          <ColorLegendDot color="red" label="Fehlgeschlagen" />
          <ColorLegendDot color="blue" label="Geplant" />
          {onClose && (
            <ActionIcon variant="subtle" size="sm" onClick={onClose} aria-label="Schliessen">
              <IconX size={14} />
            </ActionIcon>
          )}
        </Group>
      </Group>
      <JobTimelineTrack windowStart={windowStart} windowEnd={windowEnd} entries={entries} showNowMarker tickStepHours={2} showList={false} />
    </Paper>
  );
}

function ColorLegendDot({ color, label }: { color: string; label: string }) {
  return (
    <Group gap={6}>
      <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, backgroundColor: `var(--mantine-color-${color}-6)` }} />
      <Text size="xs" c="dimmed">
        {label}
      </Text>
    </Group>
  );
}
