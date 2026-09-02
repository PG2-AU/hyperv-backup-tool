import { useMemo } from "react";
import { Anchor, Group, Paper, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

import { JobTimelineTrack, type TimelineEntry } from "@/components/JobTimelineTrack";
import type { BackupJobRun, UpcomingJob } from "@/api/types";

const MIN_BAR_MS = 15 * 60 * 1000; // Mindestbreite, damit sehr kurze/laufende Balken sichtbar bleiben
const POINT_MARKER_MS = 30 * 60 * 1000; // nominale Breite fuer geplante (noch nicht gelaufene) Vorkommen

const STATUS_LABEL: Record<string, string> = {
  succeeded: "Erfolgreich",
  failed: "Fehlgeschlagen",
  running: "Läuft",
  pending: "Ausstehend",
  cleaning_up: "Räumt auf",
  cleaned_up_after_failure: "Nach Fehler aufgeräumt",
};

function statusColor(status: BackupJobRun["status"]): string {
  if (status === "succeeded") return "green";
  if (status === "failed" || status === "cleaned_up_after_failure") return "red";
  return "blue"; // running/pending/cleaning_up -- noch kein abschliessendes Ergebnis, wie ein geplanter Job
}

/** Gleitendes Fenster von -24h bis +24h um "jetzt" (nicht der feste
 * Kalendertag -- das ist die Kalender-Tagesansicht, siehe
 * BackupCalendarTab.tsx): Vergangenheit zeigt die tatsaechlichen
 * Job-Laeufe (gruen/rot), Zukunft die laut Zeitplan noch anstehenden
 * Vorkommen (blau). Ein Vorkommen, das laut Plan schon haette laufen
 * sollen, wird nicht zusaetzlich als "geplant" angezeigt -- fuer diesen
 * Zeitpunkt zaehlt der tatsaechliche Lauf (falls vorhanden). */
export function DashboardJobsTimeline({ runs, upcomingJobs }: { runs: BackupJobRun[]; upcomingJobs: UpcomingJob[] }) {
  const now = Date.now();
  const windowStart = now - 24 * 60 * 60 * 1000;
  const windowEnd = now + 24 * 60 * 60 * 1000;

  const entries = useMemo(() => {
    const result: TimelineEntry[] = [];

    for (const run of runs) {
      const startMs = new Date(run.started_at).getTime();
      if (startMs < windowStart || startMs >= windowEnd) continue;
      const rawEndMs = run.finished_at ? new Date(run.finished_at).getTime() : Math.min(now, startMs + MIN_BAR_MS);
      const endMs = Math.max(rawEndMs, startMs + MIN_BAR_MS);
      // Protection-Group-Name, falls bekannt (nur bei einem geplanten Lauf
      // gesetzt) -- ein manueller "Jetzt ausfuehren"-Lauf auf der ganzen
      // Policy kennt keine einzelne Gruppe, faellt dann auf den Policy-
      // Namen zurueck.
      const groupOrPolicyName = run.resource_group_name ?? run.job_name;
      const statusText = STATUS_LABEL[run.status] ?? run.status;
      result.push({
        key: `run-${run.id}`,
        label: groupOrPolicyName,
        sublabel: run.resource_group_name ? `${run.job_name} · ${statusText}` : statusText,
        color: statusColor(run.status),
        startMs,
        endMs,
        tooltip: `${run.job_name}${run.resource_group_name ? ` / ${run.resource_group_name}` : ""} — ${statusText}\n${new Date(run.started_at).toLocaleString("de-DE")}${
          run.finished_at ? ` – ${new Date(run.finished_at).toLocaleString("de-DE")}` : " (läuft)"
        }${run.targets.length ? `\nZiele: ${run.targets.join(", ")}` : ""}`,
      });
    }

    for (const job of upcomingJobs) {
      const startMs = new Date(job.next_run_at).getTime();
      if (startMs <= now) continue; // fuer die Vergangenheit zaehlt der tatsaechliche Lauf, siehe oben
      result.push({
        key: `upcoming-${job.resource_group_id}-${job.policy_id}-${job.next_run_at}`,
        label: job.resource_group_name,
        sublabel: `${job.policy_name} · geplant`,
        color: "blue",
        startMs,
        endMs: startMs + POINT_MARKER_MS,
        tooltip: `${job.policy_name} / ${job.resource_group_name} — geplant\n${new Date(job.next_run_at).toLocaleString("de-DE")}`,
      });
    }

    return result;
  }, [runs, upcomingJobs, windowStart, windowEnd, now]);

  return (
    <Paper p="md" h="100%">
      <Group justify="space-between" mb="sm" align="flex-end">
        <Title order={5}>Jobs</Title>
        <Group gap="md">
          <ColorLegendDot color="green" label="Erfolgreich" />
          <ColorLegendDot color="red" label="Fehlgeschlagen" />
          <ColorLegendDot color="blue" label="Geplant / läuft" />
        </Group>
      </Group>

      <JobTimelineTrack windowStart={windowStart} windowEnd={windowEnd} entries={entries} showNowMarker tickStepHours={2} />

      <Anchor component={Link} to="/jobs?tab=runs" size="sm" mt="sm" style={{ display: "inline-block" }}>
        Alle Job-Läufe anzeigen
      </Anchor>
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
