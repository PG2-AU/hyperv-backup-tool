import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  Group,
  Modal,
  Progress,
  Skeleton,
  Stack,
  Table,
  Text,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconLock, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { NetAppVolume } from "@/api/types";
import { SearchInput } from "@/components/SearchInput";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatDateTime } from "@/utils/format";

interface VolumeSnapshot {
  uuid: string;
  name: string;
  create_time: string | null;
  expiry_time: string | null;
  snapmirror_label: string | null;
  owners: string[];
  state: string | null;
  comment: string | null;
  backup: {
    run_id: string;
    policy_name: string | null;
    vm_names: string[];
    csv_names: string[];
    is_replica: boolean;
  } | null;
}

// Warum ONTAP einen Snapshot nicht loeschen laesst -- gesperrt (Snapshot-
// Locking bis expiry_time) oder von etwas belegt (owners, z.B. SnapMirror-
// Basis-Snapshot oder Grundlage eines LUN-Klons waehrend eines Restores).
// Das Backend wuerde den Versuch ohnehin mit ONTAPs Fehlermeldung
// ablehnen; hier nur, damit solche Zeilen gar nicht erst auswaehlbar sind.
function blockReason(snap: VolumeSnapshot): string | null {
  if (snap.expiry_time && new Date(snap.expiry_time).getTime() > Date.now()) {
    return `Gesperrt bis ${formatDateTime(snap.expiry_time)} (Snapshot-Locking)`;
  }
  if (snap.owners.length > 0) {
    return `Wird verwendet von: ${snap.owners.join(", ")}`;
  }
  return null;
}

export function VolumeSnapshotsModal({
  volume,
  canDelete,
  onClose,
}: {
  volume: NetAppVolume | null;
  canDelete: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const queryKey = ["volume-snapshots", volume?.cluster_id, volume?.uuid];
  const { data, isLoading, isFetching, error, refetch } = useQuery({
    queryKey,
    enabled: !!volume?.uuid,
    queryFn: async () =>
      (await apiClient.get<VolumeSnapshot[]>(`/netapp/clusters/${volume!.cluster_id}/volumes/${volume!.uuid}/snapshots`)).data,
  });
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);

  const snapshots = data ?? [];
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return snapshots;
    return snapshots.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.snapmirror_label ?? "").toLowerCase().includes(q) ||
        (s.backup?.policy_name ?? "").toLowerCase().includes(q) ||
        (s.backup?.vm_names ?? []).some((v) => v.toLowerCase().includes(q)),
    );
  }, [snapshots, search]);
  const deletable = filtered.filter((s) => !blockReason(s));
  const selectedSnaps = snapshots.filter((s) => selected.has(s.uuid));
  const allVisibleSelected = deletable.length > 0 && deletable.every((s) => selected.has(s.uuid));
  const busy = progress !== null;

  function close() {
    if (busy) return;
    setSelected(new Set());
    setSearch("");
    onClose();
  }

  function toggle(uuid: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(uuid)) next.delete(uuid);
      else next.add(uuid);
      return next;
    });
  }

  function toggleAllVisible() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) deletable.forEach((s) => next.delete(s.uuid));
      else deletable.forEach((s) => next.add(s.uuid));
      return next;
    });
  }

  async function runDelete(targets: VolumeSnapshot[]) {
    if (!volume) return;
    setProgress({ done: 0, total: targets.length });
    const failed: string[] = [];
    for (const [i, snap] of targets.entries()) {
      try {
        await apiClient.delete(`/netapp/clusters/${volume.cluster_id}/volumes/${volume.uuid}/snapshots/${snap.uuid}`, {
          params: { snapshot_name: snap.name },
        });
      } catch (err) {
        failed.push(`${snap.name}: ${apiErrorMessage(err, "Unbekannter Fehler.")}`);
      }
      setProgress({ done: i + 1, total: targets.length });
    }
    setProgress(null);
    setSelected(new Set());
    await queryClient.invalidateQueries({ queryKey: ["volume-snapshots", volume.cluster_id, volume.uuid] });
    // Backup-Eintraege koennen als "nicht mehr vorhanden" markiert worden sein.
    queryClient.invalidateQueries({ queryKey: ["backups"] });
    const ok = targets.length - failed.length;
    if (ok > 0) {
      notifications.show({
        title: "Snapshots gelöscht",
        message: `${ok} von ${targets.length} Snapshot(s) auf '${volume.name}' gelöscht.`,
        color: "green",
      });
    }
    if (failed.length > 0) {
      notifications.show({
        title: `${failed.length} Snapshot(s) nicht gelöscht`,
        message: failed.join("\n"),
        color: "red",
        autoClose: false,
      });
    }
  }

  function confirmDelete(targets: VolumeSnapshot[]) {
    const backups = targets.filter((s) => s.backup);
    confirmAction({
      title: targets.length === 1 ? "Snapshot löschen" : `${targets.length} Snapshots löschen`,
      confirmLabel: "Löschen",
      message: (
        <Stack gap="xs">
          <Text size="sm">
            {targets.length === 1 ? `Snapshot '${targets[0].name}'` : `${targets.length} Snapshots`} auf Volume '{volume?.name}'
            endgültig löschen?
          </Text>
          {backups.length > 0 && (
            <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
              {backups.length === 1 ? "Ein Snapshot ist" : `${backups.length} Snapshots sind`} Wiederherstellungspunkt eines
              Backups dieser App. Danach ist von hier kein Restore mehr möglich. Eine SnapMirror-Kopie auf dem Ziel bleibt
              erhalten und weiterhin wiederherstellbar.
            </Alert>
          )}
        </Stack>
      ),
      onConfirm: () => void runDelete(targets),
    });
  }

  return (
    <Modal opened={!!volume} onClose={close} title={`Snapshots: ${volume?.name ?? ""}`} size="90%" closeOnClickOutside={!busy}>
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap" gap="sm">
          <Text size="sm" c="dimmed">
            {volume?.svm_name ? `SVM ${volume.svm_name} · ` : ""}
            {snapshots.length} Snapshot(s)
            {volume?.snapshot_used_bytes != null ? ` · Snapshot-Belegung des Volumes ${formatBytes(volume.snapshot_used_bytes)} (Stand letzte Discovery)` : ""}
          </Text>
          <Group gap="xs">
            <SearchInput value={search} onChange={setSearch} placeholder="Name, Label, Policy oder VM..." w={260} />
            <Tooltip label="Neu laden">
              <Button variant="default" size="xs" onClick={() => refetch()} loading={isFetching && !isLoading} disabled={busy}>
                <IconRefresh size={16} />
              </Button>
            </Tooltip>
            {canDelete && (
              <Button
                color="red"
                size="xs"
                leftSection={<IconTrash size={16} />}
                disabled={selectedSnaps.length === 0 || busy}
                onClick={() => confirmDelete(selectedSnaps)}
              >
                {selectedSnaps.length > 0 ? `${selectedSnaps.length} löschen` : "Ausgewählte löschen"}
              </Button>
            )}
          </Group>
        </Group>

        {progress && (
          <Stack gap={4}>
            <Text size="sm">
              Lösche Snapshot {Math.min(progress.done + 1, progress.total)} von {progress.total}...
            </Text>
            <Progress value={(progress.done / progress.total) * 100} animated />
          </Stack>
        )}

        {error ? (
          <Alert color="red" variant="light">
            {apiErrorMessage(error, "Snapshots konnten nicht geladen werden.")}
          </Alert>
        ) : isLoading ? (
          <Skeleton height={200} />
        ) : snapshots.length === 0 ? (
          <Text c="dimmed" size="sm" ta="center" py="md">
            Dieses Volume hat keine Snapshots.
          </Text>
        ) : (
          <Box style={{ maxHeight: "60vh", overflowY: "auto" }}>
            <Table striped highlightOnHover stickyHeader>
              <Table.Thead>
                <Table.Tr>
                  {canDelete && (
                    <Table.Th w={40}>
                      <Checkbox
                        aria-label="Alle sichtbaren auswählen"
                        checked={allVisibleSelected}
                        indeterminate={!allVisibleSelected && deletable.some((s) => selected.has(s.uuid))}
                        disabled={deletable.length === 0 || busy}
                        onChange={toggleAllVisible}
                      />
                    </Table.Th>
                  )}
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Erstellt</Table.Th>
                  <Table.Th>SnapMirror-Label</Table.Th>
                  <Table.Th>Backup</Table.Th>
                  <Table.Th>Status</Table.Th>
                  {canDelete && <Table.Th w={50} />}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filtered.map((snap) => {
                  const reason = blockReason(snap);
                  return (
                    <Table.Tr key={snap.uuid}>
                      {canDelete && (
                        <Table.Td>
                          <Tooltip label={reason} disabled={!reason}>
                            <Checkbox
                              aria-label={`${snap.name} auswählen`}
                              checked={selected.has(snap.uuid)}
                              disabled={!!reason || busy}
                              onChange={() => toggle(snap.uuid)}
                            />
                          </Tooltip>
                        </Table.Td>
                      )}
                      <Table.Td>
                        <Text size="sm" ff="monospace">
                          {snap.name}
                        </Text>
                        {snap.comment && (
                          <Text size="xs" c="dimmed">
                            {snap.comment}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td style={{ whiteSpace: "nowrap" }}>{formatDateTime(snap.create_time)}</Table.Td>
                      <Table.Td>{snap.snapmirror_label ?? "–"}</Table.Td>
                      <Table.Td>
                        {snap.backup ? (
                          <Tooltip
                            multiline
                            w={320}
                            label={
                              snap.backup.vm_names.length > 0
                                ? `VMs: ${snap.backup.vm_names.join(", ")}`
                                : `CSVs: ${snap.backup.csv_names.join(", ") || "–"}`
                            }
                          >
                            <Group gap={4} wrap="nowrap">
                              <Badge variant="light" color="blue">
                                {snap.backup.policy_name ?? "Backup"}
                              </Badge>
                              {snap.backup.is_replica && (
                                <Badge variant="light" color="grape">
                                  SnapMirror-Kopie
                                </Badge>
                              )}
                              <Text size="xs" c="dimmed">
                                {snap.backup.vm_names.length} VM(s)
                              </Text>
                            </Group>
                          </Tooltip>
                        ) : (
                          <Text size="sm" c="dimmed">
                            –
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        {reason ? (
                          <Tooltip label={reason}>
                            <Badge
                              variant="light"
                              color={snap.owners.length > 0 ? "orange" : "gray"}
                              leftSection={<IconLock size={12} />}
                            >
                              {snap.owners.length > 0 ? "Belegt" : "Gesperrt"}
                            </Badge>
                          </Tooltip>
                        ) : (
                          <Badge variant="light" color="green">
                            Löschbar
                          </Badge>
                        )}
                      </Table.Td>
                      {canDelete && (
                        <Table.Td>
                          <Tooltip label={reason ?? "Löschen"}>
                            <Button
                              variant="subtle"
                              color="red"
                              size="compact-sm"
                              disabled={!!reason || busy}
                              onClick={() => confirmDelete([snap])}
                              aria-label={`${snap.name} löschen`}
                            >
                              <IconTrash size={16} />
                            </Button>
                          </Tooltip>
                        </Table.Td>
                      )}
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
            {filtered.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Kein Snapshot passt zur Suche „{search}".
              </Text>
            )}
          </Box>
        )}
      </Stack>
    </Modal>
  );
}
