import { useEffect, useMemo, useState } from "react";
import { ActionIcon, Alert, Button, Group, Loader, Modal, NumberInput, Stack, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconInfoCircle, IconRefresh, IconX } from "@tabler/icons-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import { type BarModel, UsageBar } from "@/components/CsvResizeModal";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes } from "@/utils/format";

const GIB = 1024 ** 3;

interface SmbResizeInfo {
  cluster_id: string;
  netapp_cluster_id: string;
  netapp_cluster_name: string;
  share: { server: string; share: string; path: string | null };
  volume: {
    uuid: string;
    name: string;
    svm_name: string | null;
    size_bytes: number;
    used_bytes: number | null;
    available_bytes: number | null;
    max_size_bytes: number | null;
    snapshot_reserve_bytes: number | null;
    snapshot_reserve_percent: number | null;
    snapshot_used_bytes: number | null;
    guarantee: string | null;
    autosize_mode: string | null;
    junction_path: string | null;
    lun_count: number;
  };
  aggregate: { name: string; size_bytes: number | null; used_bytes: number | null; available_bytes: number | null } | null;
  aggregate_count: number;
  share_below_volume_root: boolean;
}

interface SmbResizeResult {
  volume_name: string;
  size_before_bytes: number;
  size_after_bytes: number;
}

function toGb(bytes: number): number {
  return Math.round((bytes / GIB) * 10) / 10;
}

// SMB3-Freigabe vergroessern (Gegenstueck zu CsvResizeModal): Windows sieht
// den Datenbereich des NetApp-Volumes direkt als Groesse der Freigabe,
// deshalb genuegt es, das Volume zu vergroessern -- kein Rescan, keine
// Partition. Gleiche Balken-Darstellung wie beim CSV-Dialog.
export function SmbShareResizeModal({
  share,
  onClose,
}: {
  share: { server: string; share: string; cluster_id?: string | null } | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const opened = share !== null;
  const [result, setResult] = useState<SmbResizeResult | null>(null);
  const {
    data: info,
    isLoading,
    error,
    isFetching,
    refetch,
    dataUpdatedAt,
  } = useQuery({
    queryKey: ["smb-resize-info", share?.cluster_id, share?.server, share?.share],
    queryFn: async () =>
      (
        await apiClient.get<SmbResizeInfo>(`/smb-resize/${share!.cluster_id}`, {
          params: { server: share!.server, share: share!.share },
        })
      ).data,
    enabled: opened && !!share?.cluster_id && !result,
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const resize = useMutation({
    mutationFn: async (payload: { cluster_id: string; server: string; share: string; new_volume_size_bytes: number }) =>
      (await apiClient.post<SmbResizeResult>("/smb-resize", payload)).data,
  });

  useEffect(() => {
    if (!opened) {
      setResult(null);
      queryClient.removeQueries({ queryKey: ["smb-resize-info"] });
    }
  }, [opened, queryClient]);

  function handleStart(newSize: number) {
    if (!info) return;
    resize
      .mutateAsync({ cluster_id: info.cluster_id, server: info.share.server, share: info.share.share, new_volume_size_bytes: newSize })
      .then((r) => {
        setResult(r);
        for (const key of ["smb-shares", "volumes", "vms"]) queryClient.invalidateQueries({ queryKey: [key] });
      })
      .catch((err) =>
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Vergrößerung fehlgeschlagen."),
          color: "red",
        }),
      );
  }

  const unc = share ? `\\\\${share.server}\\${share.share}` : "";

  return (
    <Modal opened={opened} onClose={resize.isPending ? () => undefined : onClose} title={`SMB3-Freigabe vergrößern: ${unc}`} size={880}>
      {result ? (
        <Stack gap="md">
          <Alert color="green" icon={<IconCheck size={16} />}>
            Volume {result.volume_name} vergrößert: {formatBytes(result.size_before_bytes)} → {formatBytes(result.size_after_bytes)}.
            Die Hyper-V-Hosts sehen die neue Größe der Freigabe sofort.
          </Alert>
          <Group justify="flex-end">
            <Button onClick={onClose}>Schließen</Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap="sm">
          {isLoading && (
            <Group gap="xs">
              <Loader size="xs" />
              <Text size="sm">Volume und Aggregat werden live abgefragt…</Text>
            </Group>
          )}
          {error && <Alert color="red">{apiErrorMessage(error, "Ist-Stand konnte nicht abgefragt werden.")}</Alert>}
          {info && (
            <ResizeForm
              info={info}
              fetching={isFetching}
              updatedAt={dataUpdatedAt}
              pending={resize.isPending}
              onRefresh={() => refetch()}
              onStart={handleStart}
              onClose={onClose}
            />
          )}
        </Stack>
      )}
    </Modal>
  );
}

function ResizeForm({
  info,
  fetching,
  updatedAt,
  pending,
  onRefresh,
  onStart,
  onClose,
}: {
  info: SmbResizeInfo;
  fetching: boolean;
  updatedAt: number;
  pending: boolean;
  onRefresh: () => void;
  onStart: (newSize: number) => void;
  onClose: () => void;
}) {
  const [volumeGb, setVolumeGb] = useState<number | string>(toGb(info.volume.size_bytes));
  useEffect(() => setVolumeGb(toGb(info.volume.size_bytes)), [info]);
  const volumeNew =
    Number(volumeGb) === toGb(info.volume.size_bytes) ? info.volume.size_bytes : Math.round(Number(volumeGb) * GIB);
  const calc = useMemo(() => evaluate(info, volumeNew), [info, volumeNew]);
  const canStart = calc.errors.length === 0 && calc.grow > 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Text size="sm">
          Volume <b>{info.volume.name}</b> · SVM {info.volume.svm_name ?? "?"} auf {info.netapp_cluster_name}
          {info.share.path ? ` · Pfad ${info.share.path}` : ""}
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

      <Stack gap="lg">
        {calc.aggregate && <UsageBar title={`Aggregat ${info.aggregate!.name}`} {...calc.aggregate} />}
        {!info.aggregate && (
          <Text size="xs" c="dimmed">
            Aggregat: nicht sichtbar (System als einzelne SVM registriert) -- Platz im Aggregat wird nicht geprüft.
          </Text>
        )}
        <UsageBar title={`Volume ${info.volume.name} = Freigabe \\\\${info.share.server}\\${info.share.share}`} {...calc.volume} />
      </Stack>

      <Stack gap={4} maw={420}>
        <NumberInput
          label="Neue Volume-Größe"
          description={`bisher ${formatBytes(info.volume.size_bytes)}, für Hyper-V nutzbar ${formatBytes(calc.dataBefore)}`}
          suffix=" GB"
          min={toGb(info.volume.size_bytes)}
          decimalScale={1}
          thousandSeparator="."
          decimalSeparator=","
          value={volumeGb}
          onChange={setVolumeGb}
        />
        <Group gap={6}>
          {[50, 100, 250, 500].map((gb) => (
            <Button
              key={gb}
              size="compact-xs"
              variant="light"
              onClick={() => setVolumeGb((v) => Math.round((Number(v) + gb) * 10) / 10)}
            >
              +{gb} GB
            </Button>
          ))}
          <Button size="compact-xs" variant="subtle" color="gray" onClick={() => setVolumeGb(toGb(info.volume.size_bytes))}>
            zurücksetzen
          </Button>
        </Group>
      </Stack>

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

      <Group justify="flex-end">
        <Button variant="default" onClick={onClose} disabled={pending}>
          Abbrechen
        </Button>
        <Button onClick={() => onStart(volumeNew)} loading={pending} disabled={!canStart}>
          Vergrößern
        </Button>
      </Group>
    </Stack>
  );
}

function evaluate(info: SmbResizeInfo, volumeNew: number) {
  const errors: string[] = [];
  const warnings: string[] = [];
  const notes: string[] = [];
  const vol = info.volume;
  const grow = Math.max(volumeNew - vol.size_bytes, 0);
  const after = Math.max(volumeNew, vol.size_bytes);
  if (volumeNew < vol.size_bytes) errors.push("Das Volume kann nicht verkleinert werden.");
  if (vol.max_size_bytes && after > vol.max_size_bytes)
    errors.push(`Maximale Volume-Größe (${formatBytes(vol.max_size_bytes)}) überschritten.`);

  // --- Aggregat (wie im CSV-Dialog) ---
  let aggregate: BarModel | null = null;
  const agg = info.aggregate;
  const thick = vol.guarantee === "volume";
  if (agg && agg.size_bytes && agg.used_bytes != null) {
    const consumed = thick ? grow : 0;
    const pctBefore = (agg.used_bytes / agg.size_bytes) * 100;
    const pctAfter = ((agg.used_bytes + consumed) / agg.size_bytes) * 100;
    if (thick && agg.available_bytes != null && info.aggregate_count === 1 && grow > agg.available_bytes) {
      errors.push(`Im Aggregat ${agg.name} sind nur ${formatBytes(agg.available_bytes)} frei -- zu wenig für +${formatBytes(grow)}.`);
    } else if (pctAfter >= 85) {
      warnings.push(`Aggregat ${agg.name} wäre danach zu ${pctAfter.toFixed(0)} % belegt.`);
    }
    if (!thick && grow > 0)
      notes.push("Das Volume ist thin provisioned -- der Zuwachs belegt im Aggregat erst Platz, wenn er tatsächlich beschrieben wird.");
    aggregate = {
      scale: agg.size_bytes,
      segments: [
        { value: agg.used_bytes, color: "gray.6", label: `belegt ${formatBytes(agg.used_bytes)}` },
        ...(grow > 0
          ? [{ value: grow, color: thick ? "teal.6" : "teal.3", label: `Volume-Zuwachs ${formatBytes(grow)}${thick ? "" : " (thin)"}`, striped: true }]
          : []),
      ],
      summary: `${pctBefore.toFixed(0)} %${grow > 0 && thick ? ` → ${pctAfter.toFixed(0)} %` : ""} · frei ${formatBytes(agg.available_bytes ?? 0)}`,
      level: errors.some((e) => e.includes("Aggregat")) ? "error" : pctAfter >= 85 ? "warn" : "ok",
    };
  }

  // --- Volume: belegt / frei / Zuwachs / Snapshot-Reserve ---
  const reservePct = vol.snapshot_reserve_percent ?? 0;
  const reserveBefore = vol.snapshot_reserve_bytes ?? (vol.size_bytes * reservePct) / 100;
  const reserveAfter = vol.snapshot_reserve_percent != null ? (after * reservePct) / 100 : reserveBefore;
  const dataBefore = vol.size_bytes - reserveBefore;
  const dataAfter = after - reserveAfter;
  const used = vol.used_bytes ?? 0;
  const pctUsedAfter = dataAfter > 0 ? (used / dataAfter) * 100 : 0;
  const volume: BarModel = {
    scale: after,
    marker: grow > 0 ? vol.size_bytes : undefined,
    segments: [
      { value: used, color: "blue.6", label: `belegt ${formatBytes(used)}` },
      { value: Math.max(dataBefore - used, 0), color: "blue.1", label: `frei ${formatBytes(Math.max(dataBefore - used, 0))}` },
      ...(grow > 0
        ? [{ value: dataAfter - dataBefore, color: "green.5", label: `Zuwachs nutzbar ${formatBytes(dataAfter - dataBefore)}`, striped: true }]
        : []),
      ...(reserveAfter > 0 ? [{ value: reserveAfter, color: "grape.3", label: `Snapshot-Reserve ${formatBytes(reserveAfter)}` }] : []),
    ],
    summary: `nutzbar ${formatBytes(dataBefore)}${grow > 0 ? ` → ${formatBytes(dataAfter)}` : ""} · ${pctUsedAfter.toFixed(0)} % belegt`,
    level: errors.some((e) => e.includes("Volume")) ? "error" : "ok",
  };
  if (grow > 0 && reservePct > 0)
    notes.push(`${reservePct} % Snapshot-Reserve wachsen mit: ${formatBytes(reserveBefore)} → ${formatBytes(reserveAfter)}.`);
  if (vol.autosize_mode && vol.autosize_mode !== "off")
    notes.push(`Volume-Autosize ist aktiv (${vol.autosize_mode}) -- ONTAP passt die Volume-Größe zusätzlich selbst an.`);
  if (vol.lun_count > 0) warnings.push(`Im Volume liegen zusätzlich ${vol.lun_count} LUN(s) -- sie teilen sich den Platz mit der Freigabe.`);
  if (info.share_below_volume_root)
    warnings.push(
      `Die Freigabe zeigt auf ${info.share.path}, nicht auf die Wurzel des Volumes (${vol.junction_path}). Liegt dort ein Qtree mit Kontingent, begrenzt dieses die Größe unabhängig vom Volume.`,
    );
  if (grow === 0) notes.push("Neue Größe eingeben -- die Balken zeigen die Wirkung sofort.");

  return { aggregate, volume, errors, warnings, notes, grow, dataBefore };
}
