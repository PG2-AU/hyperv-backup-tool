import { useMemo } from "react";
import { Box, Group, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";

export interface TimelineEntry {
  key: string;
  /** Primaere Beschriftung (Protection-Group-Name) -- wird NICHT in den
   * kompakten Zeitstrahl-Marker geschrieben (der bleibt bewusst textlos,
   * damit er bei vielen Eintraegen nicht unleserlich/ueberladen wird),
   * sondern in der Liste darunter, die immer vollstaendig sichtbar ist. */
  label: string;
  /** Zweite Zeile in der Liste (z.B. Policy-Name, Status). */
  sublabel?: string;
  color: string;
  startMs: number;
  endMs: number;
  tooltip: string;
}

const LANE_HEIGHT = 14;
const MARKER_MIN_WIDTH_PX = 6;

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

function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

/** Kompakter horizontaler Zeitstrahl (links = frueh, rechts = spaet) als
 * reine Dichte-/Verteilungs-Uebersicht: Eintraege sind kleine farbige
 * Marker OHNE Beschriftung (bei z.B. 30 CSVs mit 15 verschiedenen
 * Zeitplaenen wuerden ausgeschriebene Kaestchen entweder ueberlappen oder
 * unlesbar klein werden). Die vollstaendige, immer sichtbare Beschriftung
 * (Zeit, Protection Group, Zusatzinfo) steht stattdessen in der Liste
 * direkt darunter -- kein Hover noetig, skaliert durch Scrollen beliebig.
 * Hover auf einen Marker zeigt zusaetzlich Details punktgenau am Zeitstrahl. */
export function JobTimelineTrack({
  windowStart,
  windowEnd,
  entries,
  showNowMarker = false,
  tickStepHours,
  listMaxHeight = 240,
  showList = true,
}: {
  windowStart: number;
  windowEnd: number;
  entries: TimelineEntry[];
  showNowMarker?: boolean;
  tickStepHours?: number;
  listMaxHeight?: number;
  /** false = nur der Marker-Zeitstrahl, ohne die Liste darunter -- fuer
   * Stellen, an denen bewusst nur eine kompakte Dichte-Uebersicht mit
   * Hover-Details gewuenscht ist (siehe Dashboard-Tageszeitstrahl). */
  showList?: boolean;
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
      marks.push({ pct: ((t - windowStart) / totalMs) * 100, label: formatTime(t) });
    }
    return marks;
  }, [windowStart, windowEnd, totalMs, stepHours, hourMs]);

  const now = Date.now();
  const nowPct = ((now - windowStart) / totalMs) * 100;
  const trackHeight = laneCount * LANE_HEIGHT;

  const sortedEntries = useMemo(() => [...entries].sort((a, b) => a.startMs - b.startMs), [entries]);

  return (
    <Stack gap="xs">
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
          {laned.map(({ entry, lane }) => {
            const leftPct = Math.max(0, ((entry.startMs - windowStart) / totalMs) * 100);
            const rightPct = Math.min(100, ((entry.endMs - windowStart) / totalMs) * 100);
            const widthPct = Math.max(rightPct - leftPct, 0.3);
            return (
              <Tooltip key={entry.key} label={<div style={{ whiteSpace: "pre-line" }}>{entry.tooltip}</div>} withinPortal>
                <Box
                  style={{
                    position: "absolute",
                    left: `${leftPct}%`,
                    width: `max(${widthPct}%, ${MARKER_MIN_WIDTH_PX}px)`,
                    top: lane * LANE_HEIGHT + 1,
                    height: LANE_HEIGHT - 2,
                    backgroundColor: `var(--mantine-color-${entry.color}-6)`,
                    borderRadius: 3,
                    cursor: "default",
                  }}
                />
              </Tooltip>
            );
          })}
        </Box>
      </Box>

      {showList && (sortedEntries.length === 0 ? (
        <Text size="sm" c="dimmed">
          Keine Job-Läufe in diesem Zeitraum.
        </Text>
      ) : (
        <ScrollArea.Autosize mah={listMaxHeight} type="auto">
          <Stack gap={4}>
            {sortedEntries.map((entry) => (
              <Group key={entry.key} gap="xs" wrap="nowrap">
                <Box w={8} h={8} style={{ flexShrink: 0, borderRadius: 2, backgroundColor: `var(--mantine-color-${entry.color}-6)` }} />
                <Text size="sm" fw={600} style={{ flexShrink: 0 }}>
                  {formatTime(entry.startMs)}
                </Text>
                <Text size="sm" truncate>
                  {entry.label}
                </Text>
                {entry.sublabel && (
                  <Text size="xs" c="dimmed" truncate>
                    {entry.sublabel}
                  </Text>
                )}
              </Group>
            ))}
          </Stack>
        </ScrollArea.Autosize>
      ))}
    </Stack>
  );
}
