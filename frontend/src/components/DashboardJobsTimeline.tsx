import { useMemo } from "react";
import { Anchor, Box, Group, Paper, Stack, Text, Title, Tooltip } from "@mantine/core";
import { Link } from "react-router-dom";

import type { BackupJobRun, UpcomingJob } from "@/api/types";

const HOUR_MS = 60 * 60 * 1000;
const WINDOW_MS = 24 * HOUR_MS;
const TRACK_HEIGHT_PER_LANE = 34;
const MIN_BAR_MS = 15 * 60 * 1000; // Mindestbreite, damit sehr kurze/laufende Balken sichtbar bleiben
const POINT_COLLISION_MS = 20 * 60 * 1000;

type Entry = {
  key: string;
  label: string;
  sublabel: string;
  startMs: number;
  endMs: number;
  color: "green" | "red" | "blue";
  tooltip: string;
};

function statusColor(status: BackupJobRun["status"]): "green" | "red" | "blue" {
  if (status === "succeeded") return "green";
  if (status === "failed" || status === "cleaned_up_after_failure") return "red";
  return "blue"; // running/pending/cleaning_up -- noch kein abschliessendes Ergebnis, wie ein geplanter Job
}

function assignLanes(entries: Entry[]): { entry: Entry; lane: number }[] {
  const sorted = [...entries].sort((a, b) => a.startMs - b.startMs);
  const laneEnds: number[] = [];
  const result: { entry: Entry; lane: number }[] = [];
  for (const entry of sorted) {
    let lane = laneEnds.findIndex((end) => end <= entry.startMs);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(0);
    }
    laneEnds[lane] = entry.endMs;
    result.push({ entry, lane });
  }
  return result;
}

export function DashboardJobsTimeline({ runs, upcomingJobs }: { runs: BackupJobRun[]; upcomingJobs: UpcomingJob[] }) {
  const now = Date.now();
  const windowStart = now - WINDOW_MS;
  const windowEnd = now + WINDOW_MS;
  const totalMs = windowEnd - windowStart;

  const { laned, laneCount } = useMemo(() => {
    const entries: Entry[] = [];

    for (const run of runs) {
      const startMs = new Date(run.started_at).getTime();
      if (startMs < windowStart) continue;
      const rawEndMs = run.finished_at ? new Date(run.finished_at).getTime() : Math.min(now, startMs + MIN_BAR_MS);
      const endMs = Math.max(rawEndMs, startMs + MIN_BAR_MS);
      const color = statusColor(run.status);
      entries.push({
        key: `run-${run.id}`,
        label: run.job_name,
        sublabel: run.targets.join(", ") || run.scope || "",
        startMs,
        endMs,
        color,
        tooltip: `${run.job_name} — ${STATUS_LABEL[run.status] ?? run.status}\n${new Date(run.started_at).toLocaleString("de-DE")}${
          run.finished_at ? ` – ${new Date(run.finished_at).toLocaleString("de-DE")}` : " (läuft)"
        }${run.targets.length ? `\nZiele: ${run.targets.join(", ")}` : ""}`,
      });
    }

    for (const job of upcomingJobs) {
      const startMs = new Date(job.next_run_at).getTime();
      entries.push({
        key: `upcoming-${job.resource_group_id}-${job.policy_id}-${job.next_run_at}`,
        label: job.policy_name,
        sublabel: job.resource_group_name,
        startMs,
        endMs: startMs + POINT_COLLISION_MS,
        color: "blue",
        tooltip: `${job.policy_name} / ${job.resource_group_name} — geplant\n${new Date(job.next_run_at).toLocaleString("de-DE")}`,
      });
    }

    const laned = assignLanes(entries);
    return { laned, laneCount: Math.max(1, ...laned.map((l) => l.lane + 1)) };
  }, [runs, upcomingJobs, windowStart, now]);

  const trackHeight = laneCount * TRACK_HEIGHT_PER_LANE;
  const nowPct = ((now - windowStart) / totalMs) * 100;

  const hourMarks = useMemo(() => {
    const marks: { pct: number; label: string }[] = [];
    for (let t = windowStart; t <= windowEnd; t += 4 * HOUR_MS) {
      marks.push({ pct: ((t - windowStart) / totalMs) * 100, label: new Date(t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) });
    }
    return marks;
  }, [windowStart, windowEnd, totalMs]);

  return (
    <Paper p="md" h="100%">
      <Group justify="space-between" mb="sm" align="flex-end">
        <Title order={5}>Jobs</Title>
        <Group gap="md">
          <Group gap={6}>
            <Box w={10} h={10} style={{ borderRadius: 2, backgroundColor: "var(--mantine-color-green-6)" }} />
            <Text size="xs" c="dimmed">Erfolgreich</Text>
          </Group>
          <Group gap={6}>
            <Box w={10} h={10} style={{ borderRadius: 2, backgroundColor: "var(--mantine-color-red-6)" }} />
            <Text size="xs" c="dimmed">Fehlgeschlagen</Text>
          </Group>
          <Group gap={6}>
            <Box w={10} h={10} style={{ borderRadius: 2, backgroundColor: "var(--mantine-color-blue-6)" }} />
            <Text size="xs" c="dimmed">Geplant / läuft</Text>
          </Group>
        </Group>
      </Group>

      <Stack gap={4}>
        <Box style={{ position: "relative", height: 16 }}>
          {hourMarks.map((mark) => (
            <Text
              key={mark.label + mark.pct}
              size="xs"
              c="dimmed"
              style={{ position: "absolute", left: `${mark.pct}%`, transform: "translateX(-50%)", whiteSpace: "nowrap" }}
            >
              {mark.label}
            </Text>
          ))}
        </Box>

        <Box style={{ position: "relative", height: trackHeight, borderLeft: "1px solid var(--mantine-color-default-border)" }}>
          {hourMarks.map((mark) => (
            <Box
              key={mark.pct}
              style={{ position: "absolute", left: `${mark.pct}%`, top: 0, bottom: 0, borderLeft: "1px dashed var(--mantine-color-default-border)" }}
            />
          ))}
          <Box
            style={{
              position: "absolute",
              left: `${nowPct}%`,
              top: 0,
              bottom: 0,
              borderLeft: "2px solid var(--mantine-color-red-6)",
              zIndex: 2,
            }}
          />
          {laned.length === 0 && (
            <Text size="sm" c="dimmed" style={{ position: "absolute", top: 8, left: 8 }}>
              Keine Job-Läufe in diesem Zeitraum und keine geplanten Jobs.
            </Text>
          )}
          {laned.map(({ entry, lane }) => {
            const leftPct = Math.max(0, ((entry.startMs - windowStart) / totalMs) * 100);
            const rightPct = Math.min(100, ((entry.endMs - windowStart) / totalMs) * 100);
            const widthPct = Math.max(rightPct - leftPct, 0.6);
            return (
              <Tooltip key={entry.key} label={<div style={{ whiteSpace: "pre-line" }}>{entry.tooltip}</div>} withinPortal>
                <Box
                  style={{
                    position: "absolute",
                    left: `${leftPct}%`,
                    width: `${widthPct}%`,
                    top: lane * TRACK_HEIGHT_PER_LANE + 2,
                    height: TRACK_HEIGHT_PER_LANE - 6,
                    backgroundColor: `var(--mantine-color-${entry.color}-6)`,
                    borderRadius: 4,
                    overflow: "hidden",
                    display: "flex",
                    alignItems: "center",
                    paddingLeft: 6,
                    paddingRight: 6,
                    cursor: "default",
                  }}
                >
                  <Text size="xs" c="white" truncate fw={600}>
                    {entry.label}
                  </Text>
                </Box>
              </Tooltip>
            );
          })}
        </Box>
      </Stack>

      <Anchor component={Link} to="/jobs?tab=runs" size="sm" mt="sm" style={{ display: "inline-block" }}>
        Alle Job-Läufe anzeigen
      </Anchor>
    </Paper>
  );
}

const STATUS_LABEL: Record<string, string> = {
  succeeded: "Erfolgreich",
  failed: "Fehlgeschlagen",
  running: "Läuft",
  pending: "Ausstehend",
  cleaning_up: "Räumt auf",
  cleaned_up_after_failure: "Nach Fehler aufgeräumt",
};
