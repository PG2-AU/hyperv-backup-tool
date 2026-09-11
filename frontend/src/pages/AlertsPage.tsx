import { useState } from "react";
import { ActionIcon, Badge, Button, Group, Paper, SegmentedControl, Table, Text, Title, Tooltip } from "@mantine/core";
import { IconExternalLink, IconRefresh } from "@tabler/icons-react";
import { Link, useNavigate } from "react-router-dom";

import { notifications } from "@mantine/notifications";

import {
  useAlerts,
  useAllowScheduleCollision,
  useDeleteVmCheckpoint,
  useDiscoverVm,
  useDismissAlert,
  useDismissBackupFailedAlert,
  useTriggerJobRun,
} from "@/api/hooks";
import { SearchInput } from "@/components/SearchInput";
import type { Alert, AlertType } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { matchesAllColumns } from "@/utils/search";

const TYPE_LABEL: Record<AlertType, string> = {
  capacity_volume: "Kapazität (Volume)",
  capacity_lun: "Kapazität (LUN)",
  hyperv_cluster_unhealthy: "Hyper-V-Cluster",
  netapp_cluster_unhealthy: "NetApp-Cluster",
  snapmirror_unhealthy: "SnapMirror",
  snapmirror_lag_exceeded: "SnapMirror-Lag",
  hyperv_node_unreachable: "Hyper-V-Knoten",
  backup_missed: "Backup verpasst",
  schedule_collision: "Zeitplan-Kollision",
  hyperv_orphan_checkpoint: "Verwaister Checkpoint",
  hyperv_vm_multi_csv: "VM auf mehreren CSVs",
  hyperv_vm_avhdx_without_checkpoint: "AVHDX ohne Checkpoint",
  backup_failed: "Backup fehlgeschlagen",
};

const TYPE_COLOR: Record<AlertType, string> = {
  capacity_volume: "orange",
  capacity_lun: "orange",
  hyperv_cluster_unhealthy: "red",
  netapp_cluster_unhealthy: "red",
  snapmirror_unhealthy: "grape",
  snapmirror_lag_exceeded: "grape",
  hyperv_node_unreachable: "red",
  backup_missed: "red",
  schedule_collision: "yellow",
  hyperv_orphan_checkpoint: "orange",
  hyperv_vm_multi_csv: "orange",
  hyperv_vm_avhdx_without_checkpoint: "orange",
  backup_failed: "red",
};

function AlertAction({ alert }: { alert: Alert }) {
  const navigate = useNavigate();

  if (alert.alert_type === "capacity_volume" && alert.object_uuid) {
    return (
      <Button size="xs" variant="light" onClick={() => navigate(`/storage?tab=volumes&editUuid=${alert.object_uuid}`)}>
        Volume vergrößern
      </Button>
    );
  }
  if (alert.alert_type === "capacity_lun" && alert.object_uuid) {
    return (
      <Button size="xs" variant="light" onClick={() => navigate(`/storage?tab=luns&editUuid=${alert.object_uuid}`)}>
        LUN vergrößern
      </Button>
    );
  }
  if (alert.alert_type === "hyperv_cluster_unhealthy") {
    return (
      <Tooltip label="Zu Hyper-V-Hosts">
        <ActionIcon component={Link} to="/settings?tab=hyperv" variant="subtle">
          <IconExternalLink size={16} />
        </ActionIcon>
      </Tooltip>
    );
  }
  if (alert.alert_type === "hyperv_node_unreachable") {
    return (
      <Tooltip label="Zu Hyper-V-Hosts">
        <ActionIcon component={Link} to="/settings?tab=hyperv" variant="subtle">
          <IconExternalLink size={16} />
        </ActionIcon>
      </Tooltip>
    );
  }
  if (alert.alert_type === "netapp_cluster_unhealthy") {
    return (
      <Tooltip label="Zu Storage > Cluster">
        <ActionIcon component={Link} to="/storage?tab=clusters" variant="subtle">
          <IconExternalLink size={16} />
        </ActionIcon>
      </Tooltip>
    );
  }
  if (alert.alert_type === "snapmirror_unhealthy" || alert.alert_type === "snapmirror_lag_exceeded") {
    return (
      <Tooltip label="Zu SnapMirror-Beziehungen">
        <ActionIcon component={Link} to="/storage?tab=snapmirror" variant="subtle">
          <IconExternalLink size={16} />
        </ActionIcon>
      </Tooltip>
    );
  }
  if (alert.alert_type === "backup_failed") {
    return (
      <Group gap="xs" wrap="nowrap">
        <Tooltip label="Zum Job-Verlauf">
          <ActionIcon component={Link} to="/jobs?tab=runs" variant="subtle">
            <IconExternalLink size={16} />
          </ActionIcon>
        </Tooltip>
        {alert.run_id && <DismissBackupFailedButton runId={alert.run_id} />}
      </Group>
    );
  }
  if (alert.alert_type === "backup_missed") {
    return (
      <Group gap="xs" wrap="nowrap">
        {alert.resource_group_id && alert.policy_id && (
          <CatchUpMissedBackupButton alertId={alert.id} policyId={alert.policy_id} resourceGroupId={alert.resource_group_id} />
        )}
        <DismissAlertButton alertId={alert.id} />
      </Group>
    );
  }
  if (alert.alert_type === "schedule_collision") {
    // Bewusst OHNE den generischen "Quittieren"-Button: eine Kollision
    // loest sich nie von selbst aus der Konfiguration heraus, ein reines
    // Quittieren wuerde beim naechsten 15min-Check sofort wieder auftauchen
    // (irrefuehrend). "Erlauben" ist die einzig sinnvolle Aktion hier.
    return <AllowCollisionButton alertId={alert.id} />;
  }
  if (alert.alert_type === "hyperv_orphan_checkpoint") {
    return (
      <Group gap="xs" wrap="nowrap">
        {alert.hyperv_cluster_id && alert.vm_name && alert.checkpoint_id && (
          <DeleteOrphanCheckpointButton clusterId={alert.hyperv_cluster_id} vmName={alert.vm_name} checkpointId={alert.checkpoint_id} />
        )}
        <DismissAlertButton alertId={alert.id} />
      </Group>
    );
  }
  if (alert.alert_type === "hyperv_vm_multi_csv") {
    return (
      <Group gap="xs" wrap="nowrap">
        <Tooltip label="Zum Inventar">
          <ActionIcon component={Link} to="/vms?tab=vms" variant="subtle">
            <IconExternalLink size={16} />
          </ActionIcon>
        </Tooltip>
        <DismissAlertButton alertId={alert.id} />
      </Group>
    );
  }
  if (alert.alert_type === "hyperv_vm_avhdx_without_checkpoint") {
    return (
      <Group gap="xs" wrap="nowrap">
        {alert.hyperv_cluster_id && alert.vm_name && (
          <DiscoverVmButton clusterId={alert.hyperv_cluster_id} vmName={alert.vm_name} />
        )}
        <DismissAlertButton alertId={alert.id} />
      </Group>
    );
  }
  return null;
}

function DeleteOrphanCheckpointButton({
  clusterId,
  vmName,
  checkpointId,
}: {
  clusterId: string;
  vmName: string;
  checkpointId: string;
}) {
  const deleteCheckpoint = useDeleteVmCheckpoint();

  function handleDelete() {
    confirmAction({
      title: "Checkpoint löschen",
      message: `Den verwaisten Checkpoint auf "${vmName}" unwiderruflich löschen?`,
      confirmLabel: "Löschen",
      color: "red",
      onConfirm: () =>
        // Der Endpunkt loest den zugehoerigen Alarm serverseitig direkt mit
        // auf (siehe delete_vm_checkpoint) -- kein zusaetzlicher
        // Quittieren-Aufruf hier noetig, useDeleteVmCheckpoint invalidiert
        // bereits die Alarm-Liste.
        deleteCheckpoint.mutate(
          { clusterId, vmName, checkpointId },
          {
            onSuccess: () => notifications.show({ title: "Checkpoint gelöscht", message: vmName, color: "green" }),
            onError: (err) =>
              notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Checkpoint konnte nicht gelöscht werden."), color: "red" }),
          },
        ),
    });
  }

  return (
    <Button size="xs" variant="light" color="red" onClick={handleDelete} loading={deleteCheckpoint.isPending}>
      Checkpoint löschen
    </Button>
  );
}

function DiscoverVmButton({ clusterId, vmName }: { clusterId: string; vmName: string }) {
  const discoverVm = useDiscoverVm();

  function handleDiscover() {
    // Der Endpunkt loest den zugehoerigen Alarm serverseitig direkt mit auf
    // (sofern die VM danach tatsaechlich keinen AVHDX-Rest mehr zeigt, siehe
    // discover_vm) -- kein zusaetzlicher Quittieren-Aufruf hier noetig,
    // useDiscoverVm invalidiert bereits die Alarm-Liste. Keine Bestaetigung
    // noetig (rein lesende WinRM-Abfrage, keine Aenderung an der VM selbst).
    discoverVm.mutate(
      { clusterId, vmName },
      {
        onSuccess: () => notifications.show({ title: "VM aktualisiert", message: vmName, color: "green" }),
        onError: (err) =>
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "VM konnte nicht aktualisiert werden."), color: "red" }),
      },
    );
  }

  return (
    <Button size="xs" variant="light" onClick={handleDiscover} loading={discoverVm.isPending}>
      VM Discovery
    </Button>
  );
}

function AllowCollisionButton({ alertId }: { alertId: string }) {
  const allowCollision = useAllowScheduleCollision();

  function handleAllow() {
    confirmAction({
      title: "Kollision erlauben",
      message:
        "Diese Zeitplan-Kollision dauerhaft erlauben? Sie wird künftig nicht mehr gemeldet, solange sich die beteiligten Zeitpläne nicht ändern.",
      confirmLabel: "Dauerhaft erlauben",
      color: "blue",
      onConfirm: () =>
        allowCollision.mutate(alertId, {
          onSuccess: () => notifications.show({ title: "Kollision erlaubt", message: "Wird künftig nicht mehr gemeldet.", color: "green" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Kollision konnte nicht erlaubt werden."), color: "red" }),
        }),
    });
  }

  return (
    <Button size="xs" variant="light" onClick={handleAllow} loading={allowCollision.isPending}>
      Erlauben
    </Button>
  );
}

function CatchUpMissedBackupButton({ alertId, policyId, resourceGroupId }: { alertId: string; policyId: string; resourceGroupId: string }) {
  const triggerRun = useTriggerJobRun();
  const dismissAlert = useDismissAlert();

  function handleCatchUp() {
    confirmAction({
      title: "Backup jetzt nachholen",
      message: "Diesen verpassten Lauf jetzt für genau diese Protection Group starten? Läuft im Hintergrund, Fortschritt siehe Kopfzeile.",
      confirmLabel: "Jetzt nachholen",
      onConfirm: () =>
        triggerRun.mutate(
          { jobId: policyId, resourceGroupId },
          {
            onSuccess: () => {
              notifications.show({ title: "Backup gestartet", message: "Fortschritt siehe Kopfzeile.", color: "blue" });
              // Alarm gilt als erledigt, sobald der Nachhol-Lauf gestartet ist -- der
              // naechste Warnungs-Check wuerde ihn ohnehin nicht erneut melden (ein
              // BackupRun existiert jetzt), das Quittieren macht das nur sofort sichtbar.
              dismissAlert.mutate(alertId);
            },
            onError: (err) =>
              notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Backup konnte nicht gestartet werden."), color: "red" }),
          },
        ),
    });
  }

  return (
    <Button size="xs" variant="light" onClick={handleCatchUp} loading={triggerRun.isPending}>
      Jetzt nachholen
    </Button>
  );
}

function DismissAlertButton({ alertId }: { alertId: string }) {
  const dismissAlert = useDismissAlert();

  function handleDismiss() {
    confirmAction({
      title: "Alarm quittieren",
      message: "Diesen Alarm als erledigt markieren?",
      confirmLabel: "Quittieren",
      color: "blue",
      onConfirm: () =>
        dismissAlert.mutate(alertId, {
          onSuccess: () => notifications.show({ title: "Alarm quittiert", message: "", color: "green" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Alarm konnte nicht quittiert werden."), color: "red" }),
        }),
    });
  }

  return (
    <Button size="xs" variant="light" onClick={handleDismiss} loading={dismissAlert.isPending}>
      Quittieren
    </Button>
  );
}

function DismissBackupFailedButton({ runId }: { runId: string }) {
  const dismissAlert = useDismissBackupFailedAlert();

  function handleDismiss() {
    confirmAction({
      title: "Alarm quittieren",
      message: "Diesen 'Backup fehlgeschlagen'-Alarm als erledigt markieren? Der fehlgeschlagene Lauf selbst bleibt im Job-Verlauf sichtbar.",
      confirmLabel: "Quittieren",
      color: "blue",
      onConfirm: () =>
        dismissAlert.mutate(runId, {
          onSuccess: () => notifications.show({ title: "Alarm quittiert", message: "", color: "green" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Alarm konnte nicht quittiert werden."), color: "red" }),
        }),
    });
  }

  return (
    <Button size="xs" variant="light" onClick={handleDismiss} loading={dismissAlert.isPending}>
      Quittieren
    </Button>
  );
}

export function AlertsPage() {
  const { data: alerts, isFetching, refetch } = useAlerts();
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "resolved">("active");
  const [search, setSearch] = useState("");

  const filtered = (alerts ?? [])
    .filter((a) => statusFilter === "all" || a.status === statusFilter)
    .filter((a) => matchesAllColumns(a, search));

  return (
    <Paper p="md">
      <Group justify="space-between" mb="sm">
        <Title order={5}>Alarme</Title>
        <Tooltip label="Aktualisieren">
          <ActionIcon variant="default" onClick={() => refetch()} loading={isFetching}>
            <IconRefresh size={16} />
          </ActionIcon>
        </Tooltip>
      </Group>
      <Text size="xs" c="dimmed" mb="md">
        Kapazitäts-Schwellwerte (Volume/LUN), Cluster-/SnapMirror-Gesundheit, verpasste und fehlgeschlagene Backup-Läufe -- aktuelle
        und historische Warnungen an einer Stelle.
      </Text>

      <Group justify="space-between" mb="sm">
        <SearchInput value={search} onChange={setSearch} placeholder="Alarme durchsuchen…" />
        <SegmentedControl
          size="xs"
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as typeof statusFilter)}
          data={[
            { label: "Aktiv", value: "active" },
            { label: "Aufgelöst", value: "resolved" },
            { label: "Alle", value: "all" },
          ]}
        />
      </Group>

      <Table.ScrollContainer minWidth={900}>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th style={{ whiteSpace: "nowrap" }}>Typ</Table.Th>
              <Table.Th>Objekt</Table.Th>
              {/* Meldung nimmt die Breiten-Reserve auf und darf umbrechen,
                  damit die Badge-Spalten Typ/Status nicht gestaucht werden. */}
              <Table.Th style={{ minWidth: 320 }}>Meldung</Table.Th>
              <Table.Th style={{ whiteSpace: "nowrap" }}>Status</Table.Th>
              <Table.Th>Aufgetreten</Table.Th>
              <Table.Th>Behoben</Table.Th>
              <Table.Th>Aktion</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filtered.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={7}>
                  <Text size="sm" c="dimmed">
                    Keine Alarme für die aktuellen Filter.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
            {filtered.map((alert) => (
              <Table.Tr key={alert.id}>
                <Table.Td style={{ whiteSpace: "nowrap" }}>
                  <Badge
                    color={TYPE_COLOR[alert.alert_type]}
                    variant="light"
                    styles={{ label: { overflow: "visible", textOverflow: "unset" } }}
                  >
                    {TYPE_LABEL[alert.alert_type]}
                  </Badge>
                </Table.Td>
                <Table.Td>{alert.object_name}</Table.Td>
                <Table.Td style={{ minWidth: 320, whiteSpace: "normal" }}>{alert.message}</Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>
                  <Badge
                    color={alert.status === "active" ? "red" : "green"}
                    variant="light"
                    styles={{ label: { overflow: "visible", textOverflow: "unset" } }}
                  >
                    {alert.status === "active" ? "Aktiv" : "Aufgelöst"}
                  </Badge>
                </Table.Td>
                <Table.Td>{new Date(alert.triggered_at).toLocaleString("de-DE")}</Table.Td>
                <Table.Td>{alert.resolved_at ? new Date(alert.resolved_at).toLocaleString("de-DE") : "-"}</Table.Td>
                <Table.Td>{alert.status === "active" && <AlertAction alert={alert} />}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}
