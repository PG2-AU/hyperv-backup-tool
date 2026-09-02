import { useMemo, useState } from "react";
import { ActionIcon, Badge, Box, Button, Group, Paper, Stack, Text } from "@mantine/core";
import { IconChevronLeft, IconChevronRight, IconX } from "@tabler/icons-react";

import { useJobsCalendar } from "@/api/hooks";
import { JobTimelineTrack, type TimelineEntry } from "@/components/JobTimelineTrack";
import type { UpcomingJob } from "@/api/types";

const WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTH_LABELS = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];
// Feste Farbpalette, deterministisch per Policy-ID zugeordnet (siehe
// policyColor) -- verbindet visuell dieselbe Policy zwischen Monatszellen
// und Tagesansicht, ohne eine globale Farbzuordnung verwalten zu muessen.
const POLICY_COLORS = ["blue", "teal", "grape", "orange", "cyan", "pink", "lime", "indigo"];

function policyColor(policyId: string): string {
  let hash = 0;
  for (let i = 0; i < policyId.length; i++) hash = (hash * 31 + policyId.charCodeAt(i)) >>> 0;
  return POLICY_COLORS[hash % POLICY_COLORS.length];
}

function dayKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function toDateParam(d: Date): string {
  return dayKey(d);
}

function isSameDay(a: Date, b: Date): boolean {
  return dayKey(a) === dayKey(b);
}

/** Montag der Woche, die `d` enthaelt. */
function startOfWeek(d: Date): Date {
  const result = new Date(d);
  const mondayIndex = (result.getDay() + 6) % 7; // JS: 0=So..6=Sa -> 0=Mo..6=So
  result.setDate(result.getDate() - mondayIndex);
  result.setHours(0, 0, 0, 0);
  return result;
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function BackupCalendarTab() {
  const [viewDate, setViewDate] = useState(() => {
    const d = new Date();
    d.setDate(1);
    d.setHours(0, 0, 0, 0);
    return d;
  });
  const [selectedDay, setSelectedDay] = useState<Date | null>(null);

  const gridStart = useMemo(() => startOfWeek(viewDate), [viewDate]);
  const monthEnd = useMemo(() => new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 0), [viewDate]);
  const gridEnd = useMemo(() => startOfWeek(new Date(monthEnd.getTime() + 6 * DAY_MS)), [monthEnd]);
  const gridDays = useMemo(() => {
    const days: Date[] = [];
    for (let t = gridStart.getTime(); t <= gridEnd.getTime(); t += DAY_MS) days.push(new Date(t));
    return days;
  }, [gridStart, gridEnd]);

  const { data: jobs, isFetching } = useJobsCalendar(toDateParam(gridStart), toDateParam(gridEnd));

  const jobsByDay = useMemo(() => {
    const map = new Map<string, UpcomingJob[]>();
    for (const job of jobs ?? []) {
      const key = dayKey(new Date(job.next_run_at));
      const list = map.get(key);
      if (list) list.push(job);
      else map.set(key, [job]);
    }
    for (const list of map.values()) list.sort((a, b) => a.next_run_at.localeCompare(b.next_run_at));
    return map;
  }, [jobs]);

  const today = new Date();

  function changeMonth(delta: number) {
    setViewDate((prev) => {
      const next = new Date(prev.getFullYear(), prev.getMonth() + delta, 1);
      return next;
    });
    setSelectedDay(null);
  }

  function goToday() {
    const d = new Date();
    d.setDate(1);
    d.setHours(0, 0, 0, 0);
    setViewDate(d);
    setSelectedDay(new Date());
  }

  return (
    <Paper p="md">
      <Group justify="space-between" mb="md">
        <Group gap="xs">
          <ActionIcon variant="light" onClick={() => changeMonth(-1)} aria-label="Vorheriger Monat">
            <IconChevronLeft size={16} />
          </ActionIcon>
          <Text fw={600} size="lg" miw={180} ta="center">
            {MONTH_LABELS[viewDate.getMonth()]} {viewDate.getFullYear()}
          </Text>
          <ActionIcon variant="light" onClick={() => changeMonth(1)} aria-label="Nächster Monat">
            <IconChevronRight size={16} />
          </ActionIcon>
        </Group>
        <Button variant="default" size="xs" onClick={goToday}>
          Heute
        </Button>
      </Group>

      <Box
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(7, 1fr)",
          gap: 4,
          opacity: isFetching ? 0.6 : 1,
        }}
      >
        {WEEKDAY_LABELS.map((label) => (
          <Text key={label} size="xs" c="dimmed" tt="uppercase" fw={700} ta="center" pb={4}>
            {label}
          </Text>
        ))}
        {gridDays.map((day) => {
          const inMonth = day.getMonth() === viewDate.getMonth();
          const dayJobs = jobsByDay.get(dayKey(day)) ?? [];
          const distinctPolicies = [...new Map(dayJobs.map((j) => [j.policy_id, j])).values()];
          const isSelected = selectedDay && isSameDay(day, selectedDay);
          return (
            <Paper
              key={day.toISOString()}
              withBorder
              p={6}
              onClick={() => setSelectedDay(day)}
              style={{
                cursor: "pointer",
                minHeight: 84,
                backgroundColor: isSelected ? "var(--mantine-color-blue-light)" : undefined,
                opacity: inMonth ? 1 : 0.4,
                borderColor: isSameDay(day, today) ? "var(--mantine-color-blue-6)" : undefined,
              }}
            >
              <Group justify="space-between" wrap="nowrap" mb={4}>
                <Text size="sm" fw={isSameDay(day, today) ? 700 : 400}>
                  {day.getDate()}
                </Text>
                {dayJobs.length > 0 && (
                  <Badge size="xs" variant="filled" color="gray">
                    {dayJobs.length}
                  </Badge>
                )}
              </Group>
              <Stack gap={2}>
                {distinctPolicies.slice(0, 3).map((j) => (
                  <Badge key={j.policy_id} size="xs" variant="light" color={policyColor(j.policy_id)} styles={{ root: { textTransform: "none" } }}>
                    {j.policy_name}
                  </Badge>
                ))}
                {distinctPolicies.length > 3 && (
                  <Text size="xs" c="dimmed">
                    +{distinctPolicies.length - 3} weitere
                  </Text>
                )}
              </Stack>
            </Paper>
          );
        })}
      </Box>

      {selectedDay && (
        <DayTimeline
          day={selectedDay}
          jobs={jobsByDay.get(dayKey(selectedDay)) ?? []}
          onClose={() => setSelectedDay(null)}
        />
      )}
    </Paper>
  );
}

// Nominale Breite eines Vorkommens auf dem Zeitstrahl -- ein geplantes
// Vorkommen hat keine echte Dauer (es ist ein Zeitpunkt, kein Lauf mit
// Start/Ende), dieser Wert bestimmt nur, ab welchem zeitlichen Abstand
// zwei Vorkommen in eine zusaetzliche Zeile ausweichen statt sich zu
// ueberlappen (siehe JobTimelineTrack).
const POINT_MARKER_MS = 30 * 60 * 1000;

function DayTimeline({ day, jobs, onClose }: { day: Date; jobs: UpcomingJob[]; onClose: () => void }) {
  const windowStart = useMemo(() => {
    const d = new Date(day);
    d.setHours(0, 0, 0, 0);
    return d.getTime();
  }, [day]);
  const windowEnd = windowStart + 24 * 60 * 60 * 1000;

  const entries: TimelineEntry[] = useMemo(
    () =>
      jobs.map((job) => {
        const startMs = new Date(job.next_run_at).getTime();
        const timeLabel = new Date(startMs).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
        return {
          key: `${job.resource_group_id}-${job.policy_id}-${job.next_run_at}`,
          label: `${timeLabel} ${job.policy_name}`,
          color: policyColor(job.policy_id),
          startMs,
          endMs: startMs + POINT_MARKER_MS,
          tooltip: `${timeLabel} — ${job.policy_name} / ${job.resource_group_name}`,
        };
      }),
    [jobs],
  );

  return (
    <Paper withBorder p="md" mt="md">
      <Group justify="space-between" mb="sm">
        <Text fw={600}>
          {day.toLocaleDateString("de-DE", { weekday: "long", day: "2-digit", month: "long", year: "numeric" })}
        </Text>
        <ActionIcon variant="subtle" size="sm" onClick={onClose}>
          <IconX size={14} />
        </ActionIcon>
      </Group>

      {jobs.length === 0 ? (
        <Text size="sm" c="dimmed">
          Keine geplanten Backup-Läufe an diesem Tag.
        </Text>
      ) : (
        <JobTimelineTrack windowStart={windowStart} windowEnd={windowEnd} entries={entries} showNowMarker tickStepHours={2} />
      )}
    </Paper>
  );
}
