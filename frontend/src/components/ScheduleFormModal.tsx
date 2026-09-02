import { useEffect, useState } from "react";
import { ActionIcon, Button, Group, Modal, NumberInput, Select, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconPlus, IconTrash } from "@tabler/icons-react";

import { useCreateSchedule, useUpdateSchedule, type ScheduleWritePayload } from "@/api/hooks";
import type { Schedule, ScheduleType } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const WEEKDAY_OPTIONS = [
  { value: "0", label: "Montag" },
  { value: "1", label: "Dienstag" },
  { value: "2", label: "Mittwoch" },
  { value: "3", label: "Donnerstag" },
  { value: "4", label: "Freitag" },
  { value: "5", label: "Samstag" },
  { value: "6", label: "Sonntag" },
];

const TYPE_OPTIONS: { value: ScheduleType; label: string }[] = [
  { value: "hourly", label: "Mehrmals täglich (feste Uhrzeiten)" },
  { value: "daily", label: "Täglich" },
  { value: "weekly", label: "Wöchentlich" },
  { value: "monthly", label: "Monatlich" },
];

interface ScheduleFormModalProps {
  opened: boolean;
  onClose: () => void;
  schedule?: Schedule | null;
  /** Vorbelegung fuer "Duplizieren": oeffnet den Anlegen-Dialog (nicht
   * Bearbeiten -- isEdit bleibt false, Speichern legt einen neuen Zeitplan
   * an) mit den Werten dieses bestehenden Zeitplans vorausgefuellt. Wird
   * ignoriert, wenn `schedule` gesetzt ist (echtes Bearbeiten hat Vorrang). */
  duplicateFrom?: Schedule | null;
  onSaved?: (schedule: Schedule) => void;
}

export function ScheduleFormModal({ opened, onClose, schedule, duplicateFrom, onSaved }: ScheduleFormModalProps) {
  const createSchedule = useCreateSchedule();
  const updateSchedule = useUpdateSchedule();
  const isEdit = !!schedule;

  const [name, setName] = useState("");
  const [type, setType] = useState<ScheduleType>("daily");
  const [times, setTimes] = useState<string[]>(["02:00"]);
  const [weekday, setWeekday] = useState<string | null>("0");
  const [dayOfMonth, setDayOfMonth] = useState<number | string>(1);

  useEffect(() => {
    if (!opened) return;
    const source = schedule ?? duplicateFrom;
    if (source) {
      // Beim Duplizieren (schedule nicht gesetzt, nur duplicateFrom) den
      // Namen mit einem Zusatz vorbelegen -- verhindert einen sofortigen
      // Name-Konflikt beim Speichern, macht aber weiterhin deutlich, dass
      // ein neuer, eigener Name gewaehlt werden sollte.
      setName(schedule ? source.name : `${source.name} (Kopie)`);
      setType(source.schedule_type);
      setTimes(source.times.length ? source.times : ["02:00"]);
      setWeekday(source.weekday != null ? String(source.weekday) : "0");
      setDayOfMonth(source.day_of_month ?? 1);
    } else {
      setName("");
      setType("daily");
      setTimes(["02:00"]);
      setWeekday("0");
      setDayOfMonth(1);
    }
  }, [opened, schedule, duplicateFrom]);

  function updateTime(index: number, value: string) {
    setTimes((prev) => prev.map((t, i) => (i === index ? value : t)));
  }
  function addTime() {
    setTimes((prev) => [...prev, "12:00"]);
  }
  function removeTime(index: number) {
    setTimes((prev) => prev.filter((_, i) => i !== index));
  }

  function handleSubmit() {
    const payload: ScheduleWritePayload = {
      name,
      schedule_type: type,
      times: type === "hourly" ? times : [times[0] ?? "02:00"],
      weekday: type === "weekly" ? Number(weekday) : null,
      day_of_month: type === "monthly" ? Number(dayOfMonth) : null,
    };

    const mutation = isEdit ? updateSchedule.mutateAsync({ id: schedule!.id, payload }) : createSchedule.mutateAsync(payload);

    mutation
      .then((saved) => {
        notifications.show({ title: isEdit ? "Zeitplan aktualisiert" : "Zeitplan angelegt", message: saved.name, color: "green" });
        onSaved?.(saved);
        onClose();
      })
      .catch((err) => {
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Zeitplan konnte nicht gespeichert werden."),
          color: "red",
        });
      });
  }

  const isPending = createSchedule.isPending || updateSchedule.isPending;

  return (
    <Modal
      opened={opened}
      onClose={onClose}
      title={isEdit ? "Zeitplan bearbeiten" : duplicateFrom ? "Zeitplan duplizieren" : "Zeitplan erstellen"}
    >
      <Stack>
        <TextInput label="Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <Select label="Typ" data={TYPE_OPTIONS} value={type} onChange={(v) => v && setType(v as ScheduleType)} allowDeselect={false} />

        {type === "hourly" ? (
          <Stack gap="xs">
            <Text size="sm" fw={500}>
              Uhrzeiten
            </Text>
            {times.map((t, i) => (
              <Group key={i} gap="xs">
                <TextInput placeholder="HH:MM" value={t} onChange={(e) => updateTime(i, e.currentTarget.value)} style={{ flex: 1 }} />
                <ActionIcon color="red" variant="subtle" onClick={() => removeTime(i)} disabled={times.length <= 1}>
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>
            ))}
            <Button variant="light" leftSection={<IconPlus size={16} />} onClick={addTime}>
              Uhrzeit hinzufügen
            </Button>
          </Stack>
        ) : (
          <TextInput label="Uhrzeit" placeholder="HH:MM" value={times[0] ?? ""} onChange={(e) => setTimes([e.currentTarget.value])} />
        )}

        {type === "weekly" && (
          <Select label="Wochentag" data={WEEKDAY_OPTIONS} value={weekday} onChange={setWeekday} allowDeselect={false} />
        )}

        {type === "monthly" && <NumberInput label="Tag des Monats" min={1} max={31} value={dayOfMonth} onChange={setDayOfMonth} />}

        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={isPending} disabled={!name}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
