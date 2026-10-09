import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, NumberInput, Select, Stack, Switch, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import {
  useCreatePolicy,
  useResourceGroups,
  useSnapMirrorLabels,
  useSnapshotLockingStatus,
  useUpdatePolicy,
  type BackupPolicyWritePayload,
} from "@/api/hooks";
import { SnapMirrorCheckPanel } from "@/components/SnapMirrorCheckPanel";
import { SnapMirrorLabelFormModal } from "@/components/SnapMirrorLabelFormModal";
import type { BackupPolicy, RetentionType } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const NEW_LABEL_VALUE = "__new_label__";

interface PolicyFormModalProps {
  opened: boolean;
  onClose: () => void;
  policy?: BackupPolicy | null;
  /** Vorbelegung fuer "Duplizieren": oeffnet den Anlegen-Dialog (nicht
   * Bearbeiten -- isEdit bleibt false, Speichern legt eine neue Policy an)
   * mit den Werten dieser bestehenden Policy vorausgefuellt. Wird ignoriert,
   * wenn `policy` gesetzt ist (echtes Bearbeiten hat Vorrang). */
  duplicateFrom?: BackupPolicy | null;
  onSaved?: (policy: BackupPolicy) => void;
}

// Zeigt je NetApp-System, ob die manipulationssichere Sperre greift: Lizenz,
// ComplianceClock und welche Backup-Volumes Snapshot-Locking eingeschaltet haben.
function SnapshotLockingStatusPanel() {
  const { data, isLoading, isError } = useSnapshotLockingStatus(true);
  if (isLoading) {
    return (
      <Text size="xs" c="dimmed">
        Prüfe die Voraussetzungen der manipulationssicheren Sperre …
      </Text>
    );
  }
  if (isError || !data) return null;
  const yesNo = (value: boolean | null | undefined) => (value == null ? "nicht prüfbar" : value ? "ja" : "nein");
  return (
    <Stack gap={6}>
      {data.map((system) => {
        const locked = system.volumes.filter((v) => v.locking_enabled).length;
        const total = system.volumes.length;
        const ready = system.license !== false && system.compliance_clock !== false;
        const allLocked = total > 0 && locked === total && ready;
        const unlocked = system.volumes.filter((v) => !v.locking_enabled).map((v) => v.volume_name);
        return (
          <Alert key={system.cluster_id} color={allLocked ? "green" : locked > 0 && ready ? "yellow" : "gray"} variant="light" p="xs">
            <Text size="xs" fw={600}>
              {system.cluster_name}:{" "}
              {total === 0
                ? "noch keine Backup-Volumes bekannt"
                : `${locked} von ${total} Backup-Volumes mit Snapshot-Locking (manipulationssicher)`}
            </Text>
            <Text size="xs">
              SnapLock-Lizenz: {yesNo(system.license)} · ComplianceClock: {yesNo(system.compliance_clock)}
              {system.nodes_without_clock.length > 0 ? ` (fehlt auf ${system.nodes_without_clock.join(", ")})` : ""}
            </Text>
            {unlocked.length > 0 && (
              <Text size="xs" c="dimmed">
                Nur Löschschutz auf: {unlocked.slice(0, 8).join(", ")}
                {unlocked.length > 8 ? ` und ${unlocked.length - 8} weiteren` : ""}. Einschalten auf der NetApp mit{" "}
                <code>volume modify -snapshot-locking-enabled true</code> (lässt sich erst wieder abschalten, wenn alle gesperrten
                Snapshots abgelaufen sind).
              </Text>
            )}
          </Alert>
        );
      })}
    </Stack>
  );
}

export function PolicyFormModal({ opened, onClose, policy, duplicateFrom, onSaved }: PolicyFormModalProps) {
  const createPolicy = useCreatePolicy();
  const updatePolicy = useUpdatePolicy();
  const { data: labels } = useSnapMirrorLabels();
  const { data: resourceGroups } = useResourceGroups();
  const isEdit = !!policy;

  // Nur im Bearbeiten-Modus bekannt: die Protection Groups, die diese
  // Policy bereits verwenden -- eine neue, noch nicht verknuepfte Policy
  // hat noch keine Objekte/Volumes, die sich pruefen liessen (siehe
  // Nutzer-Entscheidung: Pruefung erst nach der Verknuepfung).
  const linkedGroups = isEdit ? (resourceGroups ?? []).filter((g) => g.policies.some((p) => p.id === policy!.id)) : [];

  const [name, setName] = useState("");
  const [appConsistent, setAppConsistent] = useState(true);
  const [snapmirrorUpdate, setSnapmirrorUpdate] = useState(true);
  const [labelId, setLabelId] = useState<string | null>(null);
  const [retentionType, setRetentionType] = useState<RetentionType>("count");
  const [retentionValue, setRetentionValue] = useState<number | string>(7);
  const [lockingEnabled, setLockingEnabled] = useState(false);
  const [lockingDays, setLockingDays] = useState<number | string>(30);
  const [emailAlertOnFailure, setEmailAlertOnFailure] = useState(false);

  const [labelModalOpen, setLabelModalOpen] = useState(false);

  useEffect(() => {
    if (!opened) return;
    const source = policy ?? duplicateFrom;
    if (source) {
      // Beim Duplizieren (policy nicht gesetzt, nur duplicateFrom) den Namen
      // mit einem Zusatz vorbelegen -- verhindert einen sofortigen
      // Name-Konflikt beim Speichern, macht aber weiterhin deutlich, dass
      // ein neuer, eigener Name gewaehlt werden sollte.
      setName(policy ? source.name : `${source.name} (Kopie)`);
      setAppConsistent(source.consistency === "ApplicationConsistent");
      setSnapmirrorUpdate(source.snapmirror_update);
      setLabelId(source.snapmirror_label_id ?? null);
      setRetentionType(source.retention_type);
      setRetentionValue(source.retention_value);
      setLockingEnabled(source.snapshot_locking_enabled);
      setLockingDays(source.snapshot_locking_days ?? 30);
      setEmailAlertOnFailure(source.email_alert_on_failure);
    } else {
      setName("");
      setAppConsistent(true);
      setSnapmirrorUpdate(true);
      setLabelId(null);
      setRetentionType("count");
      setRetentionValue(7);
      setLockingEnabled(false);
      setLockingDays(30);
      setEmailAlertOnFailure(false);
    }
  }, [opened, policy, duplicateFrom]);

  function handleSubmit() {
    const payload: BackupPolicyWritePayload = {
      name,
      app_consistent: appConsistent,
      snapmirror_update: snapmirrorUpdate,
      snapmirror_label_id: snapmirrorUpdate ? labelId : null,
      retention_type: retentionType,
      retention_value: Number(retentionValue),
      snapshot_locking_enabled: lockingEnabled,
      snapshot_locking_days: lockingEnabled ? Number(lockingDays) : null,
      email_alert_on_failure: emailAlertOnFailure,
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
      <Modal
        opened={opened}
        onClose={onClose}
        title={isEdit ? "Backup-Policy bearbeiten" : duplicateFrom ? "Backup-Policy duplizieren" : "Neue Backup-Policy erstellen"}
        size="lg"
      >
        <Stack>
          <TextInput label="Policy-Name" required value={name} onChange={(e) => setName(e.currentTarget.value)} />

          <Text size="xs" c="dimmed">
            Der Zeitplan wird bei der Protection Group festgelegt (nicht hier) — so lassen sich mehrere Protection
            Groups mit derselben Policy zeitversetzt statt gleichzeitig sichern.
          </Text>

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

          {snapmirrorUpdate && (
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
          )}

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

          <Switch
            label="Snapshots sperren"
            description="Der primäre Snapshot lässt sich bis zum Ablauf der Frist nicht löschen. Manipulationssicher (auch gegenüber Storage-Admins) nur auf Volumes mit eingeschaltetem Snapshot-Locking; sonst gilt ein einfacher Löschschutz."
            checked={lockingEnabled}
            onChange={(e) => setLockingEnabled(e.currentTarget.checked)}
          />
          {lockingEnabled && <NumberInput label="Sperre: Anzahl Tage" min={1} value={lockingDays} onChange={setLockingDays} />}
          {lockingEnabled && retentionType === "days" && Number(lockingDays) > Number(retentionValue) && (
            <Alert color="yellow" variant="light">
              Die Sperre ({Number(lockingDays)} Tage) ist länger als die Aufbewahrung ({Number(retentionValue)} Tage): Snapshots bleiben
              dann bis zum Ende der Sperre liegen und belegen so lange Platz.
            </Alert>
          )}
          {lockingEnabled && <SnapshotLockingStatusPanel />}

          <Switch
            label="Bei Fehlschlag per E-Mail benachrichtigen"
            description="Setzt eine konfigurierte, aktivierte SMTP-Verbindung unter Settings > E-Mail voraus"
            checked={emailAlertOnFailure}
            onChange={(e) => setEmailAlertOnFailure(e.currentTarget.checked)}
          />

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

      <SnapMirrorLabelFormModal opened={labelModalOpen} onClose={() => setLabelModalOpen(false)} onSaved={(l) => setLabelId(l.id)} />
    </>
  );
}
