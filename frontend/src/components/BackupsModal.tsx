import { useEffect, useState } from "react";
import { ActionIcon, Badge, Button, Checkbox, Group, Loader, Menu, Modal, Progress, ScrollArea, Stack, Table, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { IconDotsVertical, IconDatabaseImport, IconTrash, IconUnlink } from "@tabler/icons-react";

import {
  type BackupSecondaryStatus,
  useBackupSecondaryStatus,
  useBackupsForObject,
  useDeleteBackupSnapshot,
  useDetachVmFromBackupSnapshot,
} from "@/api/hooks";
import { apiClient } from "@/api/client";
import type { BackupScope, BackupSnapshot } from "@/api/types";
import { useAuthStore } from "@/store/authStore";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatSmbShareKey } from "@/utils/format";

interface BackupsModalProps {
  opened: boolean;
  onClose: () => void;
  scope: BackupScope;
  name: string | undefined;
  // Hyper-V-Cluster der VM/des CSVs -- macht die Aufloesung cluster-sicher
  // (siehe Backend _resolve_volume_keys_for_object). Ohne das kann bei
  // zwei Clustern mit gleichnamigem CSV/derselben VM das falsche Volume
  // getroffen werden (live als Bug beobachtet).
  clusterId: string | undefined | null;
  // Nur fuer scope "vm" relevant -- oeffnet den Restore-Wizard mit diesem
  // Snapshot bereits vorausgewaehlt (siehe RestoreWizardModal.initialSnapshotId).
  onOpenRestoreWizard?: (snapshotId: string) => void;
}

// Wiederherstellbar = Snapshot noch auf dem Primaersystem ODER auf einem
// SnapMirror-Ziel, fuer dessen SVM ein Restore-Setup existiert.
function isRestorable(b: BackupSnapshot): boolean {
  return b.restore_source === "primary" || b.destinations.some((d) => d.restorable);
}

// Wo der Snapshot tatsaechlich liegt: Primaer und/oder Sekundaer (SnapMirror-
// Ziel) -- frueher stand hier nur, woher ein Restore kaeme, die zusaetzliche
// sekundaere Kopie war unsichtbar (live gemeldet 2026-10-02).
function SystemBadges({ backup }: { backup: BackupSnapshot }) {
  const present = backup.destinations.filter((d) => d.present);
  return (
    <Group gap={4} wrap="nowrap">
      {backup.restore_source === "primary" && (
        <Badge color="blue" variant="light">
          Primär
        </Badge>
      )}
      {backup.restore_source === "primary" && backup.lock_level && backup.locked_until && new Date(backup.locked_until) > new Date() && (
        <Tooltip
          label={
            backup.lock_level === "tamperproof"
              ? `Manipulationssicher gesperrt bis ${new Date(backup.locked_until).toLocaleString("de-DE")} (SnapLock-Ablaufzeit) – bis dahin auch für Storage-Admins nicht löschbar`
              : `Löschschutz bis ${new Date(backup.locked_until).toLocaleString("de-DE")} – nicht manipulationssicher: das Volume hat kein Snapshot-Locking, ein Storage-Admin kann die Frist aufheben`
          }
        >
          <Badge color={backup.lock_level === "tamperproof" ? "indigo" : "gray"} variant="light">
            {backup.lock_level === "tamperproof" ? "Gesperrt" : "Löschschutz"}
          </Badge>
        </Tooltip>
      )}
      {present.map((d) => (
        <Tooltip
          key={`${d.svm_name}:${d.volume_name}`}
          label={`${d.cluster_name ? `${d.cluster_name} · ` : ""}${d.svm_name}:${d.volume_name} · geprüft ${new Date(d.last_checked_at).toLocaleString("de-DE")}${
            d.restorable ? "" : " · kein Restore-Setup für diese SVM (Restore > Setup), daher von hier nicht wiederherstellbar"
          }`}
        >
          <Badge color="orange" variant={d.restorable ? "light" : "outline"}>
            Sekundär
          </Badge>
        </Tooltip>
      ))}
      {backup.restore_source !== "primary" && present.length === 0 && (
        <Badge color="gray" variant="light">
          nicht mehr vorhanden
        </Badge>
      )}
    </Group>
  );
}

// Was die App ueber die SnapMirror-Beziehung der Volumes dieses Objekts
// weiss -- macht sichtbar, WARUM sekundaere Kopien (nicht) erscheinen.
function SecondaryStatus({ status }: { status: BackupSecondaryStatus[] }) {
  return (
    <Stack gap={2}>
      {status.map((s) =>
        s.relationships.length === 0 ? (
          <Text key={s.source_path} size="xs" c="orange.8">
            Sekundär: für {s.source_path} ist keine SnapMirror-Beziehung bekannt -- Beziehungen meldet nur das Ziel-System; ist es in
            der App registriert und discovert (Storage)?
          </Text>
        ) : (
          s.relationships.map((r) => {
            const problems = [
              !r.destination_system && `Ziel-System '${r.ontap_destination_cluster ?? "?"}' ist nicht in der App registriert`,
              r.destination_system && !r.destination_volume_known && "Ziel-Volume nicht discovert (Discovery des Ziel-Systems ausführen)",
              r.destination_system && r.destination_volume_known && r.tracked === 0 && "noch nie abgeglichen (Settings > Hintergrundjobs > Snapshot-Abgleich jetzt ausführen)",
            ].filter(Boolean);
            return (
              <Text key={`${s.source_path}-${r.destination_path}`} size="xs" c={problems.length ? "orange.8" : "dimmed"}>
                Sekundär: {s.source_path} → {r.destination_path}
                {r.destination_system ? ` auf ${r.destination_system}` : ""} · {r.present} von {s.backups} Backups mit bestätigter
                Kopie
                {r.last_checked_at ? ` · zuletzt geprüft ${new Date(r.last_checked_at).toLocaleString("de-DE")}` : ""}
                {r.restore_setup ? " · Restore-Setup vorhanden" : " · kein Restore-Setup (Anzeige ja, Restore von dort nein)"}
                {problems.length ? ` · ${problems.join("; ")}` : ""}
              </Text>
            );
          })
        ),
      )}
    </Stack>
  );
}

export function BackupsModal({ opened, onClose, scope, name, clusterId, onOpenRestoreWizard }: BackupsModalProps) {
  const { data: backups, isLoading } = useBackupsForObject(scope, name, clusterId, opened, true);
  const { data: secondaryStatus } = useBackupSecondaryStatus(scope, name, clusterId, opened);

  // CSV/SMB3 (Nutzerwunsch 2026-10-02): mehrere Snapshots auswaehlen und in
  // einem Zug loeschen statt einzeln ueber das Drei-Punkte-Menue. Bei VMs
  // bleibt das Menue (Restore-Wizard, VM-Informationen entfernen).
  // Gleicher Aufbau wie Storage > Volumes > Snapshots (VolumeSnapshotsModal).
  const canDelete = useAuthStore((s) => s.hasPermission("backup:delete"));
  const multiSelect = scope !== "vm" && canDelete;
  const showMenu = scope === "vm";
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);
  useEffect(() => {
    if (!opened) setSelected(new Set());
  }, [opened, name]);
  // Waehlbar: Snapshot auf dem Primaersystem (wird dort geloescht) oder nur
  // noch auf dem SnapMirror-Ziel (wird dort geloescht, Nutzerwunsch 2026-10-02).
  const deletable = (backups ?? []).filter((b) => b.restore_source === "primary" || b.destinations.some((d) => d.present));
  const selectedBackups = deletable.filter((b) => selected.has(b.id));

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function deleteSelected(items: BackupSnapshot[]) {
    setBulkProgress({ done: 0, total: items.length });
    const failed: string[] = [];
    for (const [index, b] of items.entries()) {
      try {
        await apiClient.delete(`/jobs/backups/${b.id}`);
      } catch (err) {
        failed.push(`${b.snapshot_name ?? b.id}: ${apiErrorMessage(err, "nicht löschbar")}`);
      }
      setBulkProgress({ done: index + 1, total: items.length });
    }
    setBulkProgress(null);
    setSelected(new Set());
    queryClient.invalidateQueries({ queryKey: ["backups", scope, name] });
    const ok = items.length - failed.length;
    if (ok > 0) {
      notifications.show({ title: "Snapshots gelöscht", message: `${ok} von ${items.length} Snapshot(s) gelöscht.`, color: "green" });
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

  function confirmDeleteSelected() {
    const items = selectedBackups;
    if (items.length === 0) return;
    const vms = Array.from(new Set(items.flatMap((b) => b.vm_names))).sort();
    const primary = items.filter((b) => b.restore_source === "primary");
    const secondaryOnly = items.length - primary.length;
    const withSecondary = primary.filter((b) => b.destinations.some((d) => d.present)).length;
    confirmAction({
      title: `${items.length} Snapshot(s) löschen`,
      message: (
        <Stack gap={6}>
          <Text size="sm">
            {items.length} Snapshot(s) unwiderruflich löschen
            {secondaryOnly > 0 ? ` -- ${primary.length} auf dem Primärsystem, ${secondaryOnly} auf dem SnapMirror-Ziel` : " (Primärsystem)"}
            ? Die Backups dieser VMs an diesen Zeitpunkten gehen dort verloren:
          </Text>
          <Text size="xs" c="dimmed">
            {vms.join(", ") || "–"}
          </Text>
          {withSecondary > 0 && (
            <Text size="sm">
              {withSecondary} der Primär-Snapshots haben eine Kopie auf dem SnapMirror-Ziel -- die bleibt bestehen und in der Liste als
              „Sekundär“ sichtbar; zum Löschen dort danach erneut auswählen.
            </Text>
          )}
          {secondaryOnly > 0 && (
            <Text size="sm">
              {secondaryOnly} liegen nur noch auf dem SnapMirror-Ziel und werden dort gelöscht -- danach gibt es von diesen Backups
              keine Kopie mehr.
            </Text>
          )}
        </Stack>
      ),
      confirmLabel: `${items.length} löschen`,
      onConfirm: () => void deleteSelected(items),
    });
  }
  const deleteSnapshot = useDeleteBackupSnapshot(scope, name);
  const detachVm = useDetachVmFromBackupSnapshot(scope, name);

  function handleDeleteSnapshot(b: BackupSnapshot) {
    const otherVms = b.vm_names.filter((v) => v !== name);
    confirmAction({
      title: "Snapshot löschen",
      message:
        otherVms.length > 0
          ? `Diesen Snapshot wirklich unwiderruflich löschen? Betrifft auch: ${otherVms.join(", ")} -- deren Backup an diesem Snapshot geht ebenfalls verloren.`
          : "Diesen Snapshot wirklich unwiderruflich löschen (auf der NetApp und in der Datenbank)?",
      confirmLabel: "Löschen",
      onConfirm: () =>
        deleteSnapshot.mutate(b.id, {
          onSuccess: () => notifications.show({ title: "Snapshot gelöscht", message: b.snapshot_name ?? b.id, color: "blue" }),
          onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Snapshot konnte nicht gelöscht werden."), color: "red" }),
        }),
    });
  }

  function handleDetachVm(b: BackupSnapshot) {
    if (!name) return;
    confirmAction({
      title: "VM-Informationen aus Backup entfernen",
      message: `'${name}' aus diesem Backup-Eintrag entfernen? Der Snapshot bleibt für andere VMs und das CSV unverändert erhalten -- es wird nur die Zuordnung in der Datenbank entfernt.`,
      confirmLabel: "Entfernen",
      color: "orange",
      onConfirm: () =>
        detachVm.mutate(
          { snapshotId: b.id, vmName: name },
          {
            onSuccess: () => notifications.show({ title: "Entfernt", message: `${name} aus Backup-Historie entfernt.`, color: "blue" }),
            onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Konnte nicht entfernt werden."), color: "red" }),
          },
        ),
    });
  }

  // Bei scope "smb_share" ist 'name' der rohe "server|share"-Schluessel
  // (siehe _smb_share_key in jobs.py) -- fuer die Query/Mutations-Hooks
  // oben unveraendert noetig, im Titel aber als UNC-Pfad angezeigt, wie
  // Inventory > SMB3-Freigaben es tut.
  const displayName = name && scope === "smb_share" ? formatSmbShareKey(name) : name;

  return (
    <Modal opened={opened} onClose={bulkProgress ? () => undefined : onClose} withCloseButton={!bulkProgress} title={`Vorhandene Backups: ${displayName ?? ""}`} size="min(1400px, 95vw)">
      <Stack>
        {isLoading && <Loader size="sm" />}
        {!isLoading && backups?.length === 0 && (
          <Text c="dimmed" size="sm">
            Keine vorhandenen Backups fuer dieses Objekt gefunden.
          </Text>
        )}
        {!isLoading && secondaryStatus && secondaryStatus.length > 0 && <SecondaryStatus status={secondaryStatus} />}
        {!isLoading && multiSelect && backups && backups.length > 0 && (
          <Group justify="space-between">
            <Text size="sm" c="dimmed">
              {backups.length} Backup(s)
              {selectedBackups.length > 0 ? ` · ${selectedBackups.length} ausgewählt` : ""}
            </Text>
            <Button
              color="red"
              size="xs"
              leftSection={<IconTrash size={16} />}
              disabled={selectedBackups.length === 0 || bulkProgress !== null}
              onClick={confirmDeleteSelected}
            >
              {selectedBackups.length > 0 ? `${selectedBackups.length} löschen` : "Ausgewählte löschen"}
            </Button>
          </Group>
        )}
        {bulkProgress && (
          <Stack gap={4}>
            <Text size="sm">
              Lösche Snapshot {Math.min(bulkProgress.done + 1, bulkProgress.total)} von {bulkProgress.total}...
            </Text>
            <Progress value={(bulkProgress.done / bulkProgress.total) * 100} animated />
          </Stack>
        )}
        {!isLoading && backups && backups.length > 0 && (
          <ScrollArea type="auto" offsetScrollbars>
            <Table striped highlightOnHover style={{ whiteSpace: "nowrap" }}>
              <Table.Thead>
                <Table.Tr>
                  {/* Bewusst kein "alle auswählen" (Nutzer-Vorgabe 2026-10-02) -- nur einzelne Zeilen. */}
                  {multiSelect && <Table.Th w={36} />}
                  <Table.Th>Erstellt</Table.Th>
                  <Table.Th>Konsistenz</Table.Th>
                  <Table.Th>System</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Policy</Table.Th>
                  <Table.Th>VMs</Table.Th>
                  <Table.Th>Snapshot</Table.Th>
                  {showMenu && <Table.Th />}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {backups.map((b) => (
                  <Table.Tr key={b.id}>
                    {multiSelect && (
                      <Table.Td>
                        {b.restore_source === "primary" || b.destinations.some((d) => d.present) ? (
                          <Checkbox
                            aria-label="Auswählen"
                            checked={selected.has(b.id)}
                            disabled={bulkProgress !== null}
                            onChange={() => toggle(b.id)}
                          />
                        ) : (
                          <Tooltip label="Auf keinem System mehr vorhanden">
                            <Checkbox aria-label="Nicht löschbar" disabled checked={false} readOnly />
                          </Tooltip>
                        )}
                      </Table.Td>
                    )}
                    <Table.Td>{new Date(b.created_at).toLocaleString("de-DE")}</Table.Td>
                    <Table.Td>
                      <Badge color={b.consistency === "ApplicationConsistent" ? "green" : "gray"} variant="light">
                        {b.consistency === "ApplicationConsistent" ? "App-konsistent" : "Crash-konsistent"}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <SystemBadges backup={b} />
                    </Table.Td>
                    <Table.Td>
                      {b.vhds.some((v) => v.is_avhdx) && (
                        <Badge color="orange" variant="light">
                          Enthält Checkpoint
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{b.policy_name}</Table.Td>
                    <Table.Td>{b.vm_names.join(", ") || "-"}</Table.Td>
                    <Table.Td ff="monospace" fz="xs">
                      {b.snapshot_name ?? "-"}
                    </Table.Td>
                    {showMenu && (
                      <Table.Td>
                        <Menu position="bottom-end" withinPortal>
                          <Menu.Target>
                            <ActionIcon variant="subtle" color="gray">
                              <IconDotsVertical size={16} />
                            </ActionIcon>
                          </Menu.Target>
                          <Menu.Dropdown>
                            {scope === "vm" && onOpenRestoreWizard && isRestorable(b) && (
                              <Menu.Item leftSection={<IconDatabaseImport size={14} />} onClick={() => onOpenRestoreWizard(b.id)}>
                                Im Restore-Wizard öffnen
                              </Menu.Item>
                            )}
                            {scope === "vm" && (
                              <Menu.Item leftSection={<IconUnlink size={14} />} onClick={() => handleDetachVm(b)}>
                                VM-Informationen aus diesem Backup entfernen
                              </Menu.Item>
                            )}
                            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => handleDeleteSnapshot(b)}>
                              Snapshot komplett löschen
                            </Menu.Item>
                          </Menu.Dropdown>
                        </Menu>
                      </Table.Td>
                    )}
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
        )}
      </Stack>
    </Modal>
  );
}
