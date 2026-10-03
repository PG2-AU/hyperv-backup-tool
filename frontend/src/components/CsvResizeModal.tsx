import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Box,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  SimpleGrid,
  Stack,
  Stepper,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconRefresh, IconX } from "@tabler/icons-react";
import { useQueryClient } from "@tanstack/react-query";

import { resetCsvResizeQueries, useCsvResizeInfo, useCsvResizeRun, useStartCsvResize } from "@/api/hooks.csvResize";
import type { CsvResizeInfo } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";
import { BackgroundRunHint } from "@/components/BackgroundRunHint";

const GIB = 1024 ** 3;
// Unter dieser Differenz gilt die Partition als voll ausgedehnt (wie im Backend).
const PARTITION_SLACK = 16 * 1024 * 1024;

interface CsvResizeModalProps {
  opened: boolean;
  onClose: () => void;
  csv: { name: string; cluster_id?: string | null } | null;
}

// CSV vergroessern (Backlog #69, Nutzer-Vorgabe 2026-09-28): Aggregat, Volume
// und LUN/CSV werden live grafisch dargestellt; Volume und LUN werden hier
// manuell vergroessert, die Balken zeigen die Wirkung sofort, harte Grenzen
// sperren den Start, weiche Grenzen erscheinen als Warnung.
export function CsvResizeModal({ opened, onClose, csv }: CsvResizeModalProps) {
  const queryClient = useQueryClient();
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const {
    data: info,
    isLoading,
    error,
    isFetching,
    refetch,
    dataUpdatedAt,
  } = useCsvResizeInfo(csv?.cluster_id, csv?.name, opened && !runId);
  const { data: run } = useCsvResizeRun(runId);
  const running = run?.status === "running";

  useEffect(() => {
    if (!opened) {
      setRunId(undefined);
      resetCsvResizeQueries(queryClient);
    }
  }, [opened, queryClient]);

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      closeOnClickOutside={!running}
      title={`CSV vergrößern: ${csv?.name ?? ""}`}
      size={960}
    >
      {runId ? (
        <RunView runId={runId} onClose={onClose} />
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">CSV, Partition, LUN, Volume und Aggregat werden live abgefragt…</Text>
            </Group>
          )}
          {error && <Alert color="red">{apiErrorMessage(error, "Ist-Stand konnte nicht abgefragt werden.")}</Alert>}
          {info && (
            <ResizeForm
              info={info}
              fetching={isFetching}
              updatedAt={dataUpdatedAt}
              onRefresh={() => refetch()}
              onStarted={setRunId}
              onClose={onClose}
            />
          )}
        </Stack>
      )}
      {running && <BackgroundRunHint onClose={onClose} />}
    </Modal>
  );
}

// --- Eingabe + Grafik -------------------------------------------------------------

function toGb(bytes: number): number {
  return Math.round((bytes / GIB) * 10) / 10;
}

function ResizeForm({
  info,
  fetching,
  updatedAt,
  onRefresh,
  onStarted,
  onClose,
}: {
  info: CsvResizeInfo;
  fetching: boolean;
  updatedAt: number;
  onRefresh: () => void;
  onStarted: (id: string) => void;
  onClose: () => void;
}) {
  const start = useStartCsvResize();
  const [volumeGb, setVolumeGb] = useState<number | string>(toGb(info.volume.size_bytes));
  const [lunGb, setLunGb] = useState<number | string>(toGb(info.lun.size_bytes));

  useEffect(() => {
    setVolumeGb(toGb(info.volume.size_bytes));
    setLunGb(toGb(info.lun.size_bytes));
  }, [info]);

  // Eingaben in GB -> Bytes; unveraenderte Anzeige (Rundung) = exakt der Ist-Wert.
  const volumeNew =
    Number(volumeGb) === toGb(info.volume.size_bytes) ? info.volume.size_bytes : Math.round(Number(volumeGb) * GIB);
  const lunNew = Number(lunGb) === toGb(info.lun.size_bytes) ? info.lun.size_bytes : Math.round(Number(lunGb) * GIB);
  const calc = useMemo(() => evaluate(info, volumeNew, lunNew), [info, volumeNew, lunNew]);

  function volumeMatchingLun() {
    // Bisherigen Freiraum erhalten: Volume waechst um den LUN-Zuwachs,
    // hochgerechnet um die Snapshot-Reserve.
    const reservePct = info.volume.snapshot_reserve_percent ?? 0;
    const delta = Math.max(lunNew - info.lun.size_bytes, 0);
    const grown = info.volume.size_bytes + Math.ceil(delta / (1 - reservePct / 100));
    setVolumeGb(Math.ceil((grown / GIB) * 10) / 10);
  }

  function handleStart() {
    start
      .mutateAsync({
        cluster_id: info.cluster_id,
        csv_name: info.csv.name,
        new_volume_size_bytes: volumeNew > info.volume.size_bytes ? volumeNew : null,
        new_lun_size_bytes: lunNew > info.lun.size_bytes ? lunNew : null,
      })
      .then((r) => onStarted(r.id))
      .catch((err) =>
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Vergrößerung konnte nicht gestartet werden."),
          color: "red",
        }),
      );
  }

  const canStart = !info.blocked_reason && calc.errors.length === 0 && calc.hasWork;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm">
          <b>{info.csv.name}</b> · Owner {info.csv.owner_node ?? "?"} · LUN {info.lun.name} (
          {info.lun.space_reserved ? "platzreserviert" : "thin"}) auf {info.netapp_cluster_name}
        </Text>
        <Group gap={4}>
          <Text size="xs" c="dimmed">
            {fetching ? "Wird aktualisiert…" : `Stand: ${new Date(updatedAt).toLocaleTimeString("de-DE")}`}
          </Text>
          <Tooltip label="Neu abfragen">
            <ActionIcon size="sm" variant="subtle" onClick={onRefresh} loading={fetching}>
              <IconRefresh size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      {info.blocked_reason && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="Vergrößern derzeit nicht möglich">
          {info.blocked_reason}
        </Alert>
      )}

      <Stack gap="lg">
        {calc.aggregate && <UsageBar title={`Aggregat ${info.aggregate!.name}`} {...calc.aggregate} />}
        {!info.aggregate && (
          <Text size="xs" c="dimmed">
            Aggregat: nicht sichtbar (System als einzelne SVM registriert) -- Platz im Aggregat wird nicht geprüft.
          </Text>
        )}
        <UsageBar title={`Volume ${info.volume.name}`} {...calc.volume} />
        <UsageBar title={`LUN / CSV ${info.csv.name}`} {...calc.lun} />
      </Stack>

      <SimpleGrid cols={{ base: 1, sm: 2 }}>
        <Stack gap={4}>
          <NumberInput
            label="Neue Volume-Größe"
            description={`bisher ${formatBytes(info.volume.size_bytes)}`}
            suffix=" GB"
            min={toGb(info.volume.size_bytes)}
            decimalScale={1}
            thousandSeparator="."
            decimalSeparator=","
            value={volumeGb}
            onChange={setVolumeGb}
          />
          <Group gap={6}>
            {[100, 500].map((gb) => (
              <Button
                key={gb}
                size="compact-xs"
                variant="light"
                onClick={() => setVolumeGb((v) => Math.round((Number(v) + gb) * 10) / 10)}
              >
                +{gb} GB
              </Button>
            ))}
            <Button
              size="compact-xs"
              variant="light"
              color="teal"
              onClick={volumeMatchingLun}
              disabled={lunNew <= info.lun.size_bytes}
            >
              passend zur LUN
            </Button>
            <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setVolumeGb(toGb(info.volume.size_bytes))}>
              zurücksetzen
            </Button>
          </Group>
        </Stack>
        <Stack gap={4}>
          <NumberInput
            label="Neue LUN-/CSV-Größe"
            description={`bisher ${formatBytes(info.lun.size_bytes)}`}
            suffix=" GB"
            min={toGb(info.lun.size_bytes)}
            decimalScale={1}
            thousandSeparator="."
            decimalSeparator=","
            value={lunGb}
            onChange={setLunGb}
          />
          <Group gap={6}>
            {[100, 250, 500].map((gb) => (
              <Button
                key={gb}
                size="compact-xs"
                variant="light"
                onClick={() => setLunGb((v) => Math.round((Number(v) + gb) * 10) / 10)}
              >
                +{gb} GB
              </Button>
            ))}
            <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setLunGb(toGb(info.lun.size_bytes))}>
              zurücksetzen
            </Button>
          </Group>
        </Stack>
      </SimpleGrid>

      {calc.errors.map((e) => (
        <Alert key={e} color="red" icon={<IconX size={16} />} py={6}>
          {e}
        </Alert>
      ))}
      {calc.warnings.map((w) => (
        <Alert key={w} color="yellow" icon={<IconAlertTriangle size={16} />} py={6}>
          {w}
        </Alert>
      ))}
      {calc.notes.map((n) => (
        <Alert key={n} color="blue" variant="light" icon={<IconInfoCircle size={16} />} py={6}>
          {n}
        </Alert>
      ))}

      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          Ablauf: {calc.plan.join(" → ") || "–"}
        </Text>
        <Group>
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleStart} loading={start.isPending} disabled={!canStart}>
            {calc.onlyPartition ? "Partition erweitern" : "Vergrößern"}
          </Button>
        </Group>
      </Group>
    </Stack>
  );
}

// --- Berechnung -------------------------------------------------------------------

export interface Segment {
  value: number;
  color: string;
  label: string;
  striped?: boolean;
}

export interface BarModel {
  scale: number;
  segments: Segment[];
  // Position der bisherigen Groesse (Markierung), falls die neue groesser ist
  marker?: number;
  summary: string;
  level?: "ok" | "warn" | "error";
}

function evaluate(info: CsvResizeInfo, volumeNew: number, lunNew: number) {
  const errors: string[] = [];
  const warnings: string[] = [];
  const notes: string[] = [];
  const vol = info.volume;
  const lun = info.lun;
  const part = info.partition;

  if (volumeNew < vol.size_bytes) errors.push("Das Volume kann nicht verkleinert werden.");
  if (lunNew < lun.size_bytes) errors.push("Die LUN kann nicht verkleinert werden.");
  const volGrow = Math.max(volumeNew - vol.size_bytes, 0);
  const lunGrow = Math.max(lunNew - lun.size_bytes, 0);
  const volAfter = Math.max(volumeNew, vol.size_bytes);
  const lunAfter = Math.max(lunNew, lun.size_bytes);

  // --- Aggregat ---
  let aggregate: BarModel | null = null;
  const agg = info.aggregate;
  if (agg && agg.size_bytes && agg.used_bytes != null) {
    // Ein thin Volume (guarantee none) belegt den Zuwachs erst bei Nutzung.
    const thickVolume = vol.guarantee === "volume";
    const consumed = thickVolume ? volGrow : 0;
    const usedAfter = agg.used_bytes + consumed;
    const pctBefore = (agg.used_bytes / agg.size_bytes) * 100;
    const pctAfter = (usedAfter / agg.size_bytes) * 100;
    if (agg.available_bytes != null && info.aggregate_count === 1 && volGrow > agg.available_bytes) {
      errors.push(
        `Im Aggregat ${agg.name} sind nur ${formatBytes(agg.available_bytes)} frei -- zu wenig für +${formatBytes(volGrow)}.`,
      );
    } else if (pctAfter >= 95) {
      warnings.push(`Aggregat ${agg.name} wäre danach zu ${pctAfter.toFixed(0)} % belegt.`);
    } else if (pctAfter >= 85) {
      warnings.push(`Aggregat ${agg.name} wäre danach zu ${pctAfter.toFixed(0)} % belegt (über 85 %).`);
    }
    if (!thickVolume && volGrow > 0) {
      notes.push(
        "Das Volume ist thin provisioned -- der Zuwachs belegt im Aggregat erst Platz, wenn er tatsächlich beschrieben wird.",
      );
    }
    aggregate = {
      scale: agg.size_bytes,
      segments: [
        { value: agg.used_bytes, color: "gray.6", label: `belegt ${formatBytes(agg.used_bytes)}` },
        ...(volGrow > 0
          ? [
              {
                value: volGrow,
                color: thickVolume ? "teal.6" : "teal.3",
                label: `Volume-Zuwachs ${formatBytes(volGrow)}${thickVolume ? "" : " (thin)"}`,
                striped: true,
              },
            ]
          : []),
      ],
      summary: `${pctBefore.toFixed(0)} %${volGrow > 0 && thickVolume ? ` → ${pctAfter.toFixed(0)} %` : ""} · frei ${formatBytes(agg.available_bytes ?? 0)}${
        volGrow > 0 && thickVolume ? ` → ${formatBytes(Math.max((agg.available_bytes ?? 0) - volGrow, 0))}` : ""
      }`,
      level: errors.some((e) => e.includes("Aggregat")) ? "error" : pctAfter >= 85 ? "warn" : "ok",
    };
  }

  // --- Volume ---
  if (vol.max_size_bytes && volAfter > vol.max_size_bytes)
    errors.push(`Maximale Volume-Größe (${formatBytes(vol.max_size_bytes)}) überschritten.`);
  const reservePct = vol.snapshot_reserve_percent ?? 0;
  const reserveAfter = vol.snapshot_reserve_percent != null ? (volAfter * reservePct) / 100 : (vol.snapshot_reserve_bytes ?? 0);
  const dataArea = volAfter - reserveAfter;
  const lunsAfter = vol.other_luns_bytes + lunAfter;
  const overflow = Math.max(lunsAfter - dataArea, 0);
  if (overflow > 0) {
    const text = `Die LUNs im Volume (${formatBytes(lunsAfter)}) sind größer als dessen Datenbereich (${formatBytes(dataArea)} nach ${reservePct} % Snapshot-Reserve)`;
    if (lun.space_reserved)
      errors.push(
        `${text} -- die LUN ist platzreserviert, das Volume muss mindestens um ${formatBytes(overflow)} größer werden.`,
      );
    else
      warnings.push(
        `${text}. Das Volume ist damit überbucht: schreibt Windows die CSV voll, läuft das Volume voll. Empfehlung: „passend zur LUN“.`,
      );
  }
  if (vol.autosize_mode && vol.autosize_mode !== "off")
    notes.push(`Volume-Autosize ist aktiv (${vol.autosize_mode}) -- ONTAP passt die Volume-Größe zusätzlich selbst an.`);
  if (vol.lun_count > 1)
    notes.push(`Im Volume liegen noch ${vol.lun_count - 1} weitere LUN(s) mit zusammen ${formatBytes(vol.other_luns_bytes)}.`);
  if (volGrow > 0 && reservePct > 0)
    notes.push(
      `Die Snapshot-Reserve wächst mit: ${formatBytes(vol.snapshot_reserve_bytes ?? 0)} → ${formatBytes(reserveAfter)} (${reservePct} %).`,
    );
  const volScale = Math.max(volAfter, lunsAfter + reserveAfter);
  const lunGrowColor = overflow > 0 ? "red.6" : "blue.4";
  const volume: BarModel = {
    scale: volScale,
    marker: volGrow > 0 ? vol.size_bytes : undefined,
    segments: [
      { value: lun.size_bytes, color: "blue.6", label: `diese LUN ${formatBytes(lun.size_bytes)}` },
      ...(lunGrow > 0
        ? [{ value: lunGrow, color: lunGrowColor, label: `LUN-Zuwachs ${formatBytes(lunGrow)}`, striped: true }]
        : []),
      ...(vol.other_luns_bytes > 0
        ? [{ value: vol.other_luns_bytes, color: "gray.5", label: `andere LUNs ${formatBytes(vol.other_luns_bytes)}` }]
        : []),
      {
        value: Math.max(volAfter - lunsAfter - reserveAfter, 0),
        color: "gray.2",
        label: `frei ${formatBytes(Math.max(dataArea - lunsAfter, 0))}`,
      },
      ...(reserveAfter > 0
        ? [{ value: reserveAfter, color: "grape.3", label: `Snapshot-Reserve ${formatBytes(reserveAfter)}` }]
        : []),
    ],
    summary: `${formatBytes(vol.size_bytes)}${volGrow > 0 ? ` → ${formatBytes(volAfter)}` : ""}`,
    level: errors.some((e) => e.includes("Volume") || e.includes("platzreserviert")) ? "error" : overflow > 0 ? "warn" : "ok",
  };

  // --- LUN / CSV ---
  const unpartitioned = Math.max(part.partition_max_bytes - part.partition_size_bytes, 0);
  const csvUsed = info.csv.used_bytes ?? 0;
  const lunModel: BarModel = {
    scale: Math.max(lunAfter, part.disk_size_bytes),
    marker: lunGrow > 0 ? lun.size_bytes : undefined,
    segments: [
      { value: csvUsed, color: "blue.6", label: `belegt ${formatBytes(csvUsed)}` },
      {
        value: Math.max(part.partition_size_bytes - csvUsed, 0),
        color: "blue.1",
        label: `frei in der Partition ${formatBytes(Math.max(part.partition_size_bytes - csvUsed, 0))}`,
      },
      ...(unpartitioned > PARTITION_SLACK
        ? [{ value: unpartitioned, color: "orange.4", label: `noch nicht partitioniert ${formatBytes(unpartitioned)}` }]
        : []),
      ...(lunGrow > 0 ? [{ value: lunGrow, color: "green.5", label: `Zuwachs ${formatBytes(lunGrow)}`, striped: true }] : []),
    ],
    summary: `Partition ${formatBytes(part.partition_size_bytes)} → ${formatBytes(
      part.partition_size_bytes + (unpartitioned > PARTITION_SLACK ? unpartitioned : 0) + lunGrow,
    )}`,
  };
  if (unpartitioned > PARTITION_SLACK) {
    notes.push(
      `Auf der Disk liegen bereits ${formatBytes(unpartitioned)} unpartitioniert (z. B. frühere LUN-Vergrößerung) -- werden mit erweitert.`,
    );
  }

  const hasWork = volGrow > 0 || lunGrow > 0 || unpartitioned > PARTITION_SLACK;
  const onlyPartition = volGrow === 0 && lunGrow === 0 && unpartitioned > PARTITION_SLACK;
  if (!hasWork) notes.push("Neue Größen eingeben -- die Balken zeigen die Wirkung sofort.");
  const plan = hasWork
    ? [
        "Vorprüfung",
        ...(volGrow > 0 ? ["Volume"] : []),
        ...(lunGrow > 0 ? ["LUN"] : []),
        "Datenträger neu einlesen",
        "Partition erweitern",
        "Prüfen",
      ]
    : [];

  return { aggregate, volume, lun: lunModel, errors, warnings, notes, hasWork, onlyPartition, plan };
}

// --- Balken -----------------------------------------------------------------------

function cssColor(mantine: string): string {
  const [name, shade] = mantine.split(".");
  return `var(--mantine-color-${name}-${shade ?? 6})`;
}

export function UsageBar({ title, scale, segments, marker, summary, level = "ok" }: BarModel & { title: string }) {
  const border =
    level === "error"
      ? "var(--mantine-color-red-6)"
      : level === "warn"
        ? "var(--mantine-color-yellow-6)"
        : "var(--mantine-color-gray-4)";
  return (
    <Stack gap={4}>
      <Group justify="space-between">
        <Text size="sm" fw={600}>
          {title}
        </Text>
        <Text size="sm" c={level === "error" ? "red" : level === "warn" ? "yellow.8" : "dimmed"}>
          {summary}
        </Text>
      </Group>
      <Box
        pos="relative"
        h={18}
        style={{
          borderRadius: 4,
          border: `1px solid ${border}`,
          overflow: "hidden",
          display: "flex",
          background: "var(--mantine-color-body)",
        }}
      >
        {segments.map((s) =>
          s.value > 0 ? (
            <Tooltip key={s.label} label={s.label}>
              <Box
                h="100%"
                style={{
                  width: `${(s.value / scale) * 100}%`,
                  background: s.striped
                    ? `repeating-linear-gradient(45deg, ${cssColor(s.color)}, ${cssColor(s.color)} 5px, transparent 5px, transparent 9px)`
                    : cssColor(s.color),
                  transition: "width 150ms ease",
                }}
              />
            </Tooltip>
          ) : null,
        )}
        {marker != null && (
          <Box
            pos="absolute"
            top={0}
            bottom={0}
            style={{ left: `${(marker / scale) * 100}%`, borderLeft: "2px dashed var(--mantine-color-dark-3)" }}
          />
        )}
      </Box>
      <Group gap={12}>
        {segments
          .filter((s) => s.value > 0)
          .map((s) => (
            <Group key={s.label} gap={4}>
              <Box
                w={10}
                h={10}
                style={{
                  borderRadius: 2,
                  background: s.striped
                    ? `repeating-linear-gradient(45deg, ${cssColor(s.color)}, ${cssColor(s.color)} 2px, transparent 2px, transparent 4px)`
                    : cssColor(s.color),
                  border: "1px solid var(--mantine-color-gray-4)",
                }}
              />
              <Text size="xs" c="dimmed">
                {s.label}
              </Text>
            </Group>
          ))}
        {marker != null && (
          <Text size="xs" c="dimmed">
            ┆ bisherige Größe
          </Text>
        )}
      </Group>
    </Stack>
  );
}

// --- Ablauf -------------------------------------------------------------------------

function RunView({ runId, onClose }: { runId: string; onClose: () => void }) {
  const { data: run } = useCsvResizeRun(runId);
  if (!run) return <Loader size="sm" />;
  const active = run.steps.findIndex((s) => s.status === "running");
  const activeIndex = run.status === "running" ? (active === -1 ? run.steps.length : active) : run.steps.length;
  return (
    <Stack gap="md">
      <Stepper active={activeIndex} size="sm" orientation="vertical" allowNextStepsSelect={false}>
        {run.steps.map((s) => (
          <Stepper.Step
            key={s.step}
            label={s.label}
            description={s.message && s.message !== "OK" ? s.message : undefined}
            color={s.status === "error" ? "red" : undefined}
            loading={s.status === "running"}
            completedIcon={s.status === "error" ? <IconX size={16} /> : <IconCheck size={16} />}
          />
        ))}
      </Stepper>
      {run.status === "running" && (
        <Group gap="xs">
          <Loader size="xs" />
          <Text size="sm">Läuft… die CSV bleibt dabei online.</Text>
        </Group>
      )}
      {run.status === "succeeded" && (
        <Alert color="green" icon={<IconCheck size={16} />}>
          CSV {run.csv_name} vergrößert: {formatBytes(run.csv_size_before_bytes)} → {formatBytes(run.csv_size_after_bytes)}.
        </Alert>
      )}
      {run.status === "failed" && (
        <Alert color="red" icon={<IconX size={16} />}>
          {run.error_message}
        </Alert>
      )}
      {run.status !== "running" && (
        <Group justify="flex-end">
          <Button onClick={onClose}>Schließen</Button>
        </Group>
      )}
    </Stack>
  );
}
