import { useEffect, useState } from "react";
import { Button, Group, Modal, NumberInput, Select, Stack, Switch, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import {
  useCreatePolicy,
  useResourceGroups,
  useSchedules,
  useSnapMirrorLabels,
  useUpdatePolicy,
  type BackupPolicyWritePayload,
} from "@/api/hooks";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import { SnapMirrorCheckPanel } from "@/components/SnapMirrorCheckPanel";
import { SnapMirrorLabelFormModal } from "@/components/SnapMirrorLabelFormModal";
import type { BackupPolicy, RetentionType } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatSchedule } from "@/utils/format";

const NEW_SCHEDULE_VALUE = "__new_schedule__";
const NEW_LABEL_VALUE = "__new_label__";

interface PolicyFormModalProps {
  opened: boolean;
  onClose: () => void;
  policy?: BackupPolicy | null;
  onSaved?: (policy: BackupPolicy) => void;
}

export function PolicyFormModal({ opened, onClose, policy, onSaved }: PolicyFormModalProps) {
  const createPolicy = useCreatePolicy();
  const updatePolicy = useUpdatePolicy();
  const { data: schedules } = useSchedules();
  const { data: labels } = useSnapMirrorLabels();
  const { data: resourceGroups } = useResourceGroups();
  const isEdit = !!policy;

  // Nur im Bearbeiten-Modus bekannt: die Protection Groups, die diese
  // Policy bereits verwenden -- eine neue, noch nicht verknuepfte Policy
  // hat noch keine Objekte/Volumes, die sich pruefen liessen (siehe
  // Nutzer-Entscheidung: Pruefung erst nach der Verknuepfung).
  const linkedGroups = isEdit ? (resourceGroups ?? []).filter((g) => g.policies.some((p) => p.id === policy!.id)) : [];

  const [name, setName] = useState("");
  const [scheduleId, setScheduleId] = useState<string | null>(null);
  const [appConsistent, setAppConsistent] = useState(true);
  const [snapmirrorUpdate, setSnapmirrorUpdate] = useState(true);
  const [labelId, setLabelId] = useState<string | null>(null);
  const [retentionType, setRetentionType] = useState<RetentionType>("count");
  const [retentionValue, setRetentionValue] = useState<number | string>(7);
  const [lockingEnabled, setLockingEnabled] = useState(false);
  const [lockingDays, setLockingDays] = useState<number | string>(30);

  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [labelModalOpen, setLabelModalOpen] = useState(false);

  useEffect(() => {
    if (!opened) return;
    if (policy) {
      setName(policy.name);
      setScheduleId(policy.schedule_id ?? null);
      setAppConsistent(policy.consistency === "ApplicationConsistent");
      setSnapmirrorUpdate(policy.snapmirror_update);
      setLabelId(policy.snapmirror_label_id ?? null);
      setRetentionType(policy.retention_type);
      setRetentionValue(policy.retention_value);
      setLockingEnabled(policy.snapshot_locking_enabled);
      setLockingDays(policy.snapshot_locking_days ?? 30);
    } else {
      setName("");
      setScheduleId(null);
      setAppConsistent(true);
      setSnapmirrorUpdate(true);
      setLabelId(null);
      setRetentionType("count");
      setRetentionValue(7);
      setLockingEnabled(false);
      setLockingDays(30);
    }
  }, [opened, policy]);

  function handleSubmit() {
    const payload: BackupPolicyWritePayload = {
      name,
      schedule_id: scheduleId,
      app_consistent: appConsistent,
      snapmirror_update: snapmirrorUpdate,
      snapmirror_label_id: labelId,
      retention_type: retentionType,
      retention_value: Number(retentionValue),
      snapshot_locking_enabled: lockingEnabled,
      snapshot_locking_days: lockingEnabled ? Number(lockingDays) : null,
    };

    const mutation = isEdit ? updatePolicy.mutateAsync({ id: policy!.id, payload }) : createPolicy.mutateAsync(payload);

    mutation
      .then((saved) => {
        notifications.show({ title: isEdit ? "Policy aktualisiert" : "Policy erstellt", message: saved.name, color: "green" });
        onSaved?.(saved);
        onClose();
      })
      .catch((err) => {
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Policy konnte nicht gespeichert werden."), color: "red" });
      });
  }

  const isPending = createPolicy.isPending || updatePolicy.isPending;

  return (
    <>
      <Modal opened={opened} onClose={onClose} title={isEdit ? "Backup-Policy bearbeiten" : "Neue Backup-Policy erstellen"} size="lg">
        <Stack>
          <TextInput label="Policy-Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} />

          <Select
            label="Zeitplan"
            placeholder="Kein Zeitplan (nur manuell)"
            data={[
              { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Zeitplan erstellen..." },
              ...(schedules?.map((s) => ({ value: s.id, label: `${s.name} (${formatSchedule(s)})` })) ?? []),
            ]}
            value={scheduleId}
            onChange={(v) => (v === NEW_SCHEDULE_VALUE ? setScheduleModalOpen(true) : setScheduleId(v))}
            clearable
          />

          <Switch
            label="Applikationskonsistent (VSS-Checkpoint)"
            description="Nein = crash-konsistent (Standard-Checkpoint)"
            checked={appConsistent}
            onChange={(e) => setAppConsistent(e.currentTarget.checked)}
          />

          <Switch
            label="SnapMirror-Update nach Snapshot"
            checked={snapmirrorUpdate}
            onChange={(e) => setSnapmirrorUpdate(e.currentTarget.checked)}
          />

          <SnapMirrorCheckPanel
            enabled={snapmirrorUpdate && isEdit}
            groups={linkedGroups.map((g) => ({ scope: g.scope, members: g.members }))}
          />

          <Select
            label="SnapMirror-Label"
            placeholder="Kein Label"
            data={[
              { value: NEW_LABEL_VALUE, label: "+ Neues Label erstellen..." },
              ...(labels?.map((l) => ({ value: l.id, label: l.name })) ?? []),
            ]}
            value={labelId}
            onChange={(v) => (v === NEW_LABEL_VALUE ? setLabelModalOpen(true) : setLabelId(v))}
            clearable
          />

          <Group grow>
            <Select
              label="Retention-Typ"
              data={[
                { value: "count", label: "Anzahl Snapshots" },
                { value: "days", label: "Anzahl Tage" },
              ]}
              value={retentionType}
              onChange={(v) => v && setRetentionType(v as RetentionType)}
              allowDeselect={false}
            />
            <NumberInput label="Retention-Wert" min={1} value={retentionValue} onChange={setRetentionValue} />
          </Group>

          <Switch label="Snapshot Locking (WORM)" checked={lockingEnabled} onChange={(e) => setLockingEnabled(e.currentTarget.checked)} />
          {lockingEnabled && <NumberInput label="Snapshot Locking: Anzahl Tage" min={1} value={lockingDays} onChange={setLockingDays} />}

          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={onClose}>
              Abbrechen
            </Button>
            <Button onClick={handleSubmit} loading={isPending} disabled={!name}>
              {isEdit ? "Speichern" : "Erstellen"}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ScheduleFormModal opened={scheduleModalOpen} onClose={() => setScheduleModalOpen(false)} onSaved={(s) => setScheduleId(s.id)} />
      <SnapMirrorLabelFormModal opened={labelModalOpen} onClose={() => setLabelModalOpen(false)} onSaved={(l) => setLabelId(l.id)} />
    </>
  );
}
