import { useEffect, useState } from "react";
import { Button, Group, NumberInput, Paper, Select, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useAlertConfig, useSchedulerConfig, useUpdateAlertConfig, useUpdateSchedulerConfig } from "@/api/hooks";
import type { SchedulerConfigWritePayload } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00 UTC` }));

function AlertThresholdSection() {
  const { data: config } = useAlertConfig();
  const updateConfig = useUpdateAlertConfig();
  const [threshold, setThreshold] = useState<number | string>(90);

  useEffect(() => {
    if (config) setThreshold(config.capacity_threshold_percent);
  }, [config]);

  function handleSave() {
    updateConfig
      .mutateAsync({ capacity_threshold_percent: Number(threshold) })
      .then(() => notifications.show({ title: "Gespeichert", message: "Schwellwert wurde aktualisiert", color: "green" }))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Schwellwert konnte nicht gespeichert werden."), color: "red" }),
      );
  }

  return (
    <Paper p="md" maw={560}>
      <Title order={5} mb={4}>
        Kapazitäts-Warnungen
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Ab diesem Füllstand wird pro Volume/LUN eine Warnung erzeugt (Dashboard &gt; Warnungen, Seite Alarms). Alle 15 Minuten
        geprüft, gegen den zuletzt discoverten Füllstand.
      </Text>
      <Stack gap="md">
        <NumberInput label="Schwellwert" min={1} max={100} value={threshold} onChange={setThreshold} suffix=" %" />
        <Group justify="flex-end">
          <Button onClick={handleSave} loading={updateConfig.isPending}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}

export function SchedulerConfigTab() {
  const { data: config } = useSchedulerConfig();
  const updateConfig = useUpdateSchedulerConfig();

  const [healthcheckMinutes, setHealthcheckMinutes] = useState<number | string>(15);
  const [discoveryMinutes, setDiscoveryMinutes] = useState<number | string>(240);
  const [snapshotHour, setSnapshotHour] = useState("2");
  const [retentionHour, setRetentionHour] = useState("2");

  useEffect(() => {
    if (!config) return;
    setHealthcheckMinutes(config.healthcheck_interval_minutes);
    setDiscoveryMinutes(config.discovery_interval_minutes);
    setSnapshotHour(String(config.snapshot_reconcile_hour));
    setRetentionHour(String(config.retention_cleanup_hour));
  }, [config]);

  function handleSave() {
    const payload: SchedulerConfigWritePayload = {
      healthcheck_interval_minutes: Number(healthcheckMinutes),
      discovery_interval_minutes: Number(discoveryMinutes),
      snapshot_reconcile_hour: Number(snapshotHour),
      retention_cleanup_hour: Number(retentionHour),
    };
    updateConfig
      .mutateAsync(payload)
      .then(() => notifications.show({ title: "Gespeichert", message: "Zeitpläne wurden aktualisiert und sofort übernommen", color: "green" }))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zeitpläne konnten nicht gespeichert werden."), color: "red" }),
      );
  }

  return (
    <Stack gap="md">
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
        <Group justify="flex-end">
          <Button onClick={handleSave} loading={updateConfig.isPending}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Paper>
    <AlertThresholdSection />
    </Stack>
  );
}
