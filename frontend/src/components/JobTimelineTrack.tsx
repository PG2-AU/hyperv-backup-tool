import { useMemo } from "react";
import { Box, Text, Tooltip } from "@mantine/core";

export interface TimelineEntry {
  key: string;
  label: string;
  color: string;
  startMs: number;
  endMs: number;
  tooltip: string;
}

const LANE_HEIGHT = 34;

function assignLanes(entries: TimelineEntry[]): { entry: TimelineEntry; lane: number }[] {
  const sorted = [...entries].sort((a, b) => a.startMs - b.startMs);
  const laneEnds: number[] = [];
  const result: { entry: TimelineEntry; lane: number }[] = [];
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

/** Horizontaler Zeitstrahl (links = frueh, rechts = spaet): Stunden-Achse
 * oben, Eintraege darunter als beschriftete Balken -- bei zeitlicher
 * Ueberschneidung wandert ein Eintrag automatisch in eine zusaetzliche Zeile
 * darunter (Lane-Zuordnung), statt sich die Breite mit einem anderen
 * Eintrag zu teilen (dadurch bleibt jede Beschriftung immer lesbar). Wird
 * sowohl im Dashboard (48h um "jetzt") als auch in der Backup-Kalender-
 * Tagesansicht (ein Kalendertag, 00:00-24:00) verwendet. */
export function JobTimelineTrack({
  windowStart,
  windowEnd,
  entries,
  showNowMarker = false,
  tickStepHours,
}: {
  windowStart: number;
  windowEnd: number;
  entries: TimelineEntry[];
  showNowMarker?: boolean;
  tickStepHours?: number;
}) {
  const totalMs = windowEnd - windowStart;
  const hourMs = 60 * 60 * 1000;
  const stepHours = tickStepHours ?? (totalMs > 30 * hourMs ? 4 : 2);

  const { laned, laneCount } = useMemo(() => {
    const laned = assignLanes(entries);
    return { laned, laneCount: Math.max(1, ...laned.map((l) => l.lane + 1)) };
  }, [entries]);

  const hourMarks = useMemo(() => {
    const marks: { pct: number; label: string }[] = [];
    for (let t = windowStart; t <= windowEnd; t += stepHours * hourMs) {
      marks.push({
        pct: ((t - windowStart) / totalMs) * 100,
        label: new Date(t).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }),
      });
    }
    return marks;
  }, [windowStart, windowEnd, totalMs, stepHours, hourMs]);

  const now = Date.now();
  const nowPct = ((now - windowStart) / totalMs) * 100;
  const trackHeight = laneCount * LANE_HEIGHT;

  return (
    <Box>
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
        {showNowMarker && now >= windowStart && now <= windowEnd && (
          <Box
            style={{ position: "absolute", left: `${nowPct}%`, top: 0, bottom: 0, borderLeft: "2px solid var(--mantine-color-red-6)", zIndex: 2 }}
          />
        )}
        {laned.length === 0 && (
          <Text size="sm" c="dimmed" style={{ position: "absolute", top: 8, left: 8 }}>
            Keine Job-Läufe in diesem Zeitraum.
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
                  top: lane * LANE_HEIGHT + 2,
                  height: LANE_HEIGHT - 6,
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
    </Box>
  );
}
