import { useMemo } from "react";
import { Group, Paper, Text, Title } from "@mantine/core";

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

/** Kompakter Tages-Zeitstrahl (00:00-24:00 heute, ohne Liste darunter --
 * siehe JobTimelineTrack showList=false) fuer einen schnellen Ueberblick
 * direkt unter der Cluster-/NetApp-Uebersicht: nur farbige Marker,
 * Vergangenheit des Tages nach tatsaechlichem Ergebnis (gruen/rot),
 * Zukunft als geplant (blau). Details ausschliesslich per Hover -- siehe
 * die ausfuehrlichere Jobs-Tabelle weiter unten fuer die vollstaendige,
 * immer lesbare Liste. */
export function DashboardDayStrip({ runs, scheduledToday }: { runs: BackupJobRun[]; scheduledToday: UpcomingJob[] }) {
  const now = Date.now();
  const windowStart = useMemo(() => startOfDay(new Date()), []);
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

    for (const job of scheduledToday) {
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
  }, [runs, scheduledToday, windowStart, windowEnd, now]);

  return (
    <Paper p="md">
      <Group justify="space-between" mb="sm" align="flex-end">
        <Title order={5}>Backup-Zeitstrahl</Title>
        <Group gap="md">
          <ColorLegendDot color="green" label="Erfolgreich" />
          <ColorLegendDot color="red" label="Fehlgeschlagen" />
          <ColorLegendDot color="blue" label="Geplant" />
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
