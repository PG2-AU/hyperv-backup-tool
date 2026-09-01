import { useEffect, useState } from "react";
import { Button, Group, NumberInput, Paper, Select, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useAlertConfig, useUpdateAlertConfig } from "@/api/hooks";
import type { AlertConfigWritePayload, AlertScope } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

export function AlertSettingsTab() {
  const { data: config } = useAlertConfig();
  const updateConfig = useUpdateAlertConfig();

  const [volumeThreshold, setVolumeThreshold] = useState<number | string>(90);
  const [lunThreshold, setLunThreshold] = useState<number | string>(90);
  const [lagThreshold, setLagThreshold] = useState<number | string>(240);
  const [scope, setScope] = useState<AlertScope>("all");

  useEffect(() => {
    if (!config) return;
    setVolumeThreshold(config.volume_threshold_percent);
    setLunThreshold(config.lun_threshold_percent);
    setLagThreshold(config.snapmirror_lag_threshold_minutes);
    setScope(config.scope);
  }, [config]);

  function handleSave() {
    const payload: AlertConfigWritePayload = {
      volume_threshold_percent: Number(volumeThreshold),
      lun_threshold_percent: Number(lunThreshold),
      snapmirror_lag_threshold_minutes: Number(lagThreshold),
      scope,
    };
    updateConfig
      .mutateAsync(payload)
      .then(() => notifications.show({ title: "Gespeichert", message: "Alarm-Einstellungen wurden aktualisiert", color: "green" }))
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Einstellungen konnten nicht gespeichert werden."), color: "red" }),
      );
  }

  return (
    <Paper p="md" maw={560}>
      <Title order={5} mb={4}>
        Alarms
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        Schwellwerte pro Kategorie für die Alarme-Seite (Dashboard &gt; Warnungen). Alle 15 Minuten gegen den zuletzt discoverten
        Zustand geprüft, zusätzlich sofort nach einer Aktion auf der Alarme-Seite selbst.
      </Text>
      <Stack gap="md">
        <NumberInput
          label="Schwellwert Volume"
          description="Ab diesem Füllstand wird pro Volume eine Warnung erzeugt"
          min={1}
          max={100}
          value={volumeThreshold}
          onChange={setVolumeThreshold}
          suffix=" %"
        />
        <NumberInput
          label="Schwellwert LUN"
          description="Ab diesem Füllstand wird pro LUN eine Warnung erzeugt"
          min={1}
          max={100}
          value={lunThreshold}
          onChange={setLunThreshold}
          suffix=" %"
        />
        <NumberInput
          label="Schwellwert SnapMirror-Lag"
          description="Ab dieser Verzögerung wird pro SnapMirror-Beziehung eine Warnung erzeugt, unabhängig vom Gesundheitsstatus"
          min={1}
          max={100_000}
          value={lagThreshold}
          onChange={setLagThreshold}
          suffix=" min"
        />
        <Select
          label="Sichtbarkeit"
          description="Nur die tatsächlich vom Hyper-V-Cluster genutzten Volumes/LUNs/SnapMirror-Beziehungen berücksichtigen, oder alle im NetApp-Cluster vorhandenen (z.B. auch fremde Workloads auf demselben Storage)"
          data={[
            { value: "all", label: "Alle Storage-Objekte" },
            { value: "hyperv_referenced", label: "Nur im Kontext des Hyper-V-Clusters" },
          ]}
          value={scope}
          onChange={(v) => v && setScope(v as AlertScope)}
          allowDeselect={false}
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
