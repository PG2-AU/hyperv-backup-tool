import { useEffect, useState } from "react";
import { ActionIcon, Button, Group, NumberInput, Paper, Select, Stack, Table, Text, Title, Tooltip } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";

import { useAlertConfig, useAllowedCollisions, useRevokeAllowedCollision, useUpdateAlertConfig } from "@/api/hooks";
import type { AlertConfigWritePayload, AlertScope } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

export function AlertSettingsTab() {
  const { data: config } = useAlertConfig();
  const updateConfig = useUpdateAlertConfig();

  const [volumeThreshold, setVolumeThreshold] = useState<number | string>(90);
  const [lunThreshold, setLunThreshold] = useState<number | string>(90);
  const [lagThreshold, setLagThreshold] = useState<number | string>(4);
  const [missedGraceMinutes, setMissedGraceMinutes] = useState<number | string>(30);
  const [collisionWindowMinutes, setCollisionWindowMinutes] = useState<number | string>(15);
  const [scope, setScope] = useState<AlertScope>("all");

  useEffect(() => {
    if (!config) return;
    setVolumeThreshold(config.volume_threshold_percent);
    setLunThreshold(config.lun_threshold_percent);
    setLagThreshold(config.snapmirror_lag_threshold_hours);
    setMissedGraceMinutes(config.backup_missed_grace_minutes);
    setCollisionWindowMinutes(config.schedule_collision_window_minutes);
    setScope(config.scope);
  }, [config]);

  function handleSave() {
    const payload: AlertConfigWritePayload = {
      volume_threshold_percent: Number(volumeThreshold),
      lun_threshold_percent: Number(lunThreshold),
      snapmirror_lag_threshold_hours: Number(lagThreshold),
      backup_missed_grace_minutes: Number(missedGraceMinutes),
      schedule_collision_window_minutes: Number(collisionWindowMinutes),
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
          max={8760}
          value={lagThreshold}
          onChange={setLagThreshold}
          suffix=" h"
        />
        <NumberInput
          label="Karenzzeit verpasste Backups"
          description="So lange nach dem geplanten Zeitpunkt wird gewartet, bevor ein Lauf als verpasst gemeldet wird -- muss größer sein als die normale Verzögerung durch sequenzielle Abarbeitung (üblicherweise wenige Minuten)"
          min={5}
          max={1440}
          value={missedGraceMinutes}
          onChange={setMissedGraceMinutes}
          suffix=" min"
        />
        <NumberInput
          label="Kollisions-Schwellwert"
          description="Job-Starts, die innerhalb dieser Spanne liegen (an einem Tag, an dem beide Zeitpläne feuern können), gelten als Kollision"
          min={1}
          max={240}
          value={collisionWindowMinutes}
          onChange={setCollisionWindowMinutes}
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
      <AllowedCollisionsSection />
    </Paper>
  );
}

function AllowedCollisionsSection() {
  const { data: allowed } = useAllowedCollisions();
  const revoke = useRevokeAllowedCollision();

  if (!allowed || allowed.length === 0) return null;

  function handleRevoke(id: string) {
    confirmAction({
      title: "Erlaubnis zurücknehmen",
      message: "Diese Kollision wird beim nächsten Check wieder gemeldet, falls sie weiterhin besteht.",
      confirmLabel: "Zurücknehmen",
      color: "red",
      onConfirm: () =>
        revoke.mutate(id, {
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Erlaubnis konnte nicht zurückgenommen werden."), color: "red" }),
        }),
    });
  }

  return (
    <>
      <Title order={6} mt="xl" mb={4}>
        Erlaubte Kollisionen
      </Title>
      <Text size="xs" c="dimmed" mb="sm">
        Zeitplan-Kollisionen, die einmal über "Erlauben" bestätigt wurden -- werden nicht mehr gemeldet, solange sich die
        beteiligten Zeitpläne nicht ändern.
      </Text>
      <Table>
        <Table.Tbody>
          {allowed.map((row) => (
            <Table.Tr key={row.id}>
              <Table.Td>
                <Text size="sm">{row.summary}</Text>
              </Table.Td>
              <Table.Td w={40}>
                <Tooltip label="Erlaubnis zurücknehmen">
                  <ActionIcon color="red" variant="subtle" onClick={() => handleRevoke(row.id)}>
                    <IconTrash size={16} />
                  </ActionIcon>
                </Tooltip>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </>
  );
}
