import { useEffect, useState } from "react";
import { Button, Group, NumberInput, Paper, Select, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useSchedulerConfig, useUpdateSchedulerConfig } from "@/api/hooks";
import type { SchedulerConfigWritePayload } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00 UTC` }));

export function SchedulerConfigTab() {
  const { data: config } = useSchedulerConfig();
  const updateConfig = useUpdateSchedulerConfig();

  const [healthcheckMinutes, setHealthcheckMinutes] = useState<number | string>(15);
  const [discoveryMinutes, setDiscoveryMinutes] = useState<number | string>(240);
  const [snapshotHour, setSnapshotHour] = useState("2");
  const [retentionHour, setRetentionHour] = useState("2");
  const [cancelForceTimeoutMinutes, setCancelForceTimeoutMinutes] = useState<number | string>(10);
  const [runMaxDurationMinutes, setRunMaxDurationMinutes] = useState<number | string>(0);
  const [checkpointParallelism, setCheckpointParallelism] = useState<number | string>(0);

  useEffect(() => {
    if (!config) return;
    setHealthcheckMinutes(config.healthcheck_interval_minutes);
    setDiscoveryMinutes(config.discovery_interval_minutes);
    setSnapshotHour(String(config.snapshot_reconcile_hour));
    setRetentionHour(String(config.retention_cleanup_hour));
    setCancelForceTimeoutMinutes(config.backup_cancel_force_timeout_minutes);
    setRunMaxDurationMinutes(config.backup_run_max_duration_minutes);
    setCheckpointParallelism(config.backup_checkpoint_parallelism);
  }, [config]);

  function handleSave() {
    const payload: SchedulerConfigWritePayload = {
      healthcheck_interval_minutes: Number(healthcheckMinutes),
      discovery_interval_minutes: Number(discoveryMinutes),
      snapshot_reconcile_hour: Number(snapshotHour),
      retention_cleanup_hour: Number(retentionHour),
      backup_cancel_force_timeout_minutes: Number(cancelForceTimeoutMinutes),
      backup_run_max_duration_minutes: Number(runMaxDurationMinutes),
      backup_checkpoint_parallelism: Number(checkpointParallelism),
    };
    updateConfig
      .mutateAsync(payload)
      .then(() => notifications.show({ title: "Gespeichert", message: "Zeitpläne wurden aktualisiert und sofort übernommen", color: "green" }))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zeitpläne konnten nicht gespeichert werden."), color: "red" }),
      );
  }

  return (
    <Paper p="md" maw={560}>
      <Title order={5} mb={4}>
        Hintergrundjobs
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Zeitpläne der periodischen Hintergrundjobs. Änderungen wirken sofort, ohne Neustart der Applikation. Snapshot-Abgleich und
        Retention-Cleanup laufen in UTC, nicht in der lokalen Zeitzone der Backup-Zeitpläne.
      </Text>
      <Stack gap="md">
        <NumberInput
          label="Health-Check-Intervall"
          description="Wie oft der Erreichbarkeits-Status aller Cluster geprüft wird, in Minuten"
          min={1}
          max={1440}
          value={healthcheckMinutes}
          onChange={setHealthcheckMinutes}
          suffix=" min"
        />
        <NumberInput
          label="Discovery-Intervall"
          description="Wie oft VMs/CSVs/Volumes/LUNs neu discovert werden, in Minuten"
          min={1}
          max={1440}
          value={discoveryMinutes}
          onChange={setDiscoveryMinutes}
          suffix=" min"
        />
        <Select
          label="Snapshot-Abgleich"
          description="Tägliche Uhrzeit, zu der Backup-Snapshots gegen den echten NetApp-Bestand abgeglichen werden"
          data={HOUR_OPTIONS}
          value={snapshotHour}
          onChange={(v) => v && setSnapshotHour(v)}
          allowDeselect={false}
        />
        <Select
          label="Retention-Cleanup"
          description="Tägliche Uhrzeit, zu der die Policy-Retention durchgesetzt wird (überfällige Snapshots löschen)"
          data={HOUR_OPTIONS}
          value={retentionHour}
          onChange={(v) => v && setRetentionHour(v)}
          allowDeselect={false}
        />
        <NumberInput
          label="Abbruch-Zeitlimit (Watchdog)"
          description={
            "Reagiert ein Backup-Lauf so lange nicht auf 'Job abbrechen' (ein Schritt hängt), wird er hart abgeschlossen, damit " +
            "die 'läuft'-Zeile nicht dauerhaft künftige Läufe derselben Gruppe blockiert. 0 = aus."
          }
          min={0}
          max={180}
          value={cancelForceTimeoutMinutes}
          onChange={setCancelForceTimeoutMinutes}
          suffix=" min"
        />
        <NumberInput
          label="Maximale Laufzeit je Backup-Lauf"
          description={
            "Harte Obergrenze für die Gesamtdauer JEDES Backup-Laufs, auch ohne manuellen Abbruch. Großzügig wählen, damit ein " +
            "echter Großlauf nicht abgeschnitten wird. 0 = keine Obergrenze."
          }
          min={0}
          max={1440}
          value={runMaxDurationMinutes}
          onChange={setRunMaxDurationMinutes}
          suffix=" min"
        />
        <NumberInput
          label="Checkpoint-Parallelität"
          description={
            "Wie viele Hyper-V-Hosts gleichzeitig einen Checkpoint erstellen dürfen (Phase vor den Storage-Snapshots). " +
            "Pro Host läuft immer nur einer. 0 = automatisch (alle beteiligten Hosts parallel) · 1 = nacheinander · " +
            "N = höchstens N Hosts gleichzeitig."
          }
          min={0}
          max={64}
          value={checkpointParallelism}
          onChange={setCheckpointParallelism}
        />
        <Group justify="flex-end">
          <Button onClick={handleSave} loading={updateConfig.isPending}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
