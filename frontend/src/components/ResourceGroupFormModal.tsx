import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, MultiSelect, Select, Stack, Stepper, Text, TextInput } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";

import {
  useCreateResourceGroup,
  useCsvs,
  usePolicies,
  useSchedules,
  useUpdateResourceGroup,
  useVms,
  type ResourceGroupWritePayload,
} from "@/api/hooks";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import { ScheduleFormModal } from "@/components/ScheduleFormModal";
import { SnapMirrorCheckPanel } from "@/components/SnapMirrorCheckPanel";
import type { BackupScope, ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { formatSchedule } from "@/utils/format";
import { makeMemberKey } from "@/utils/resourceGroupMember";

const SCOPE_OPTIONS: { value: BackupScope; label: string }[] = [
  { value: "vm", label: "Virtuelle Maschinen" },
  { value: "csv", label: "Cluster Shared Volumes" },
];

const NEW_POLICY_VALUE = "__new_policy__";
const NEW_SCHEDULE_VALUE = "__new_schedule__";

interface PolicyLinkState {
  policy_id: string;
  schedule_id: string | null;
}

interface ResourceGroupFormModalProps {
  opened: boolean;
  onClose: () => void;
  group?: ResourceGroup | null;
  /** Vorbelegung fuer "Duplizieren": oeffnet den Anlegen-Dialog (nicht
   * Bearbeiten -- isEdit bleibt false, Speichern legt eine neue Gruppe an)
   * mit den Werten dieser bestehenden Gruppe vorausgefuellt. Wird ignoriert,
   * wenn `group` gesetzt ist (echtes Bearbeiten hat Vorrang). */
  duplicateFrom?: ResourceGroup | null;
}

export function ResourceGroupFormModal({ opened, onClose, group, duplicateFrom }: ResourceGroupFormModalProps) {
  const createGroup = useCreateResourceGroup();
  const updateGroup = useUpdateResourceGroup();
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const { data: policies } = usePolicies();
  const { data: schedules } = useSchedules();
  const isEdit = !!group;

  const [active, setActive] = useState(0);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<BackupScope>("vm");
  const [members, setMembers] = useState<string[]>([]);
  // Der Zeitplan haengt an der einzelnen Policy-Verknuepfung, nicht an der
  // Protection Group als Ganzes -- dieselbe Gruppe kann so z.B. an eine
  // stuendliche UND eine woechentliche Policy gehaengt sein, je mit eigenem
  // Zeitplan, statt fuer jede Kadenz eine eigene Gruppe anlegen zu muessen
  // (siehe Backend app.models.resource_group.ResourceGroupPolicyLink).
  const [policyLinks, setPolicyLinks] = useState<PolicyLinkState[]>([]);
  const [policyModalOpen, setPolicyModalOpen] = useState(false);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  // Welche Verknuepfung gerade "+ Neuen Zeitplan erstellen..." ausgeloest
  // hat -- der neu angelegte Zeitplan wird nach dem Speichern genau dieser
  // Verknuepfung zugewiesen, nicht irgendeiner/der letzten.
  const [scheduleTargetPolicyId, setScheduleTargetPolicyId] = useState<string | null>(null);

  useEffect(() => {
    if (!opened) return;
    setActive(0);
    const source = group ?? duplicateFrom;
    if (source) {
      // Beim Duplizieren (group nicht gesetzt, nur duplicateFrom) den Namen
      // mit einem Zusatz vorbelegen -- verhindert einen sofortigen
      // Name-Konflikt beim Speichern, macht aber weiterhin deutlich, dass
      // ein neuer, eigener Name gewaehlt werden sollte.
      setName(group ? source.name : `${source.name} (Kopie)`);
      setScope(source.scope);
      setMembers(source.members);
      setPolicyLinks(source.policy_links.map((l) => ({ policy_id: l.policy_id, schedule_id: l.schedule_id ?? null })));
    } else {
      setName("");
      setScope("vm");
      setMembers([]);
      setPolicyLinks([]);
    }
  }, [opened, group, duplicateFrom]);

  // Cluster-qualifizierter Wert (siehe makeMemberKey), damit z.B. zwei
  // Cluster mit je einem CSV "CSV01" in der Auswahl unterscheidbar bleiben
  // und die Zuordnung nicht ueber den Namen mit dem falschen Cluster
  // kollidiert. Der Cluster-Name wird im Label aber nur dann ergaenzt, wenn
  // der Name tatsaechlich mehrdeutig ist (mehrere Cluster mit demselben
  // VM-/CSV-Namen) -- im Normalfall (eindeutiger Name) bleibt die Liste so
  // aufgeraeumt wie zuvor.
  function countByName<T extends { name: string }>(items: T[]): Map<string, number> {
    const counts = new Map<string, number>();
    for (const item of items) counts.set(item.name, (counts.get(item.name) ?? 0) + 1);
    return counts;
  }

  const memberOptions =
    scope === "vm"
      ? (() => {
          const nameCounts = countByName(vms ?? []);
          return (vms ?? [])
            .filter((v) => v.cluster_id)
            .map((v) => ({
              value: makeMemberKey(v.cluster_id!, v.name),
              label: (nameCounts.get(v.name) ?? 0) > 1 && v.cluster ? `${v.name} (${v.cluster})` : v.name,
            }));
        })()
      : (() => {
          const nameCounts = countByName(csvs ?? []);
          return (csvs ?? [])
            .filter((c) => c.cluster_id)
            .map((c) => ({
              value: makeMemberKey(c.cluster_id!, c.name),
              label: (nameCounts.get(c.name) ?? 0) > 1 && c.hyperv_cluster_name ? `${c.name} (${c.hyperv_cluster_name})` : c.name,
            }));
        })();

  function handleScopeChange(value: string | null) {
    if (!value) return;
    setScope(value as BackupScope);
    setMembers([]);
  }

  function handlePolicyIdsChange(values: string[]) {
    const selectedIds = values.filter((v) => v !== NEW_POLICY_VALUE);
    // Bestehende Zeitplan-Zuordnung je Policy beibehalten, nur neu
    // dazugekommene/entfernte Policies anpassen.
    setPolicyLinks((prev) => {
      const scheduleByPolicy = new Map(prev.map((l) => [l.policy_id, l.schedule_id]));
      return selectedIds.map((id) => ({ policy_id: id, schedule_id: scheduleByPolicy.get(id) ?? null }));
    });
    if (values.includes(NEW_POLICY_VALUE)) {
      setPolicyModalOpen(true);
    }
  }

  function setLinkSchedule(policyId: string, scheduleId: string | null) {
    setPolicyLinks((prev) => prev.map((l) => (l.policy_id === policyId ? { ...l, schedule_id: scheduleId } : l)));
  }

  function handleSubmit() {
    const payload: ResourceGroupWritePayload = { name, scope, members, policy_links: policyLinks };
    const mutation = isEdit ? updateGroup.mutateAsync({ id: group!.id, payload }) : createGroup.mutateAsync(payload);

    mutation
      .then((saved) => {
        notifications.show({ title: isEdit ? "Protection Group aktualisiert" : "Protection Group erstellt", message: saved.name, color: "green" });
        onClose();
      })
      .catch((err) => {
        notifications.show({
          title: "Fehler",
          message: apiErrorMessage(err, "Protection Group konnte nicht gespeichert werden."),
          color: "red",
        });
      });
  }

  const isPending = createGroup.isPending || updateGroup.isPending;
  const policyIds = policyLinks.map((l) => l.policy_id);

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={isEdit ? "Protection Group bearbeiten" : duplicateFrom ? "Protection Group duplizieren" : "Protection Group anlegen"}
        size="lg"
      >
        <Stepper active={active} onStepClick={setActive} size="sm">
          <Stepper.Step label="Objekte & Policies" description="Was wird wie gesichert">
            <Stack mt="md">
              <TextInput label="Name" placeholder="z.B. Bronze" required value={name} onChange={(e) => setName(e.currentTarget.value)} />

              <Select label="Typ" data={SCOPE_OPTIONS} value={scope} onChange={handleScopeChange} allowDeselect={false} disabled={isEdit} />

              {scope === "csv" && (
                <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
                  Empfohlen: nur ein CSV pro Protection Group. Mehrere CSVs in derselben Gruppe lassen sich zwar
                  sichern, erschweren aber eine spätere gezielte Wiederherstellung genau dieses einen CSVs.
                </Alert>
              )}

              <MultiSelect
                label={scope === "vm" ? "Virtuelle Maschinen" : "Cluster Shared Volumes"}
                placeholder="Objekte auswaehlen"
                data={memberOptions}
                value={members}
                onChange={setMembers}
                searchable
              />

              <MultiSelect
                label="Verknuepfte Backup-Policies"
                placeholder="Policies auswaehlen"
                data={[
                  { value: NEW_POLICY_VALUE, label: "+ Neue Policy erstellen..." },
                  ...(policies?.map((p) => ({ value: p.id, label: p.name })) ?? []),
                ]}
                value={policyIds}
                onChange={handlePolicyIdsChange}
                searchable
              />

              <SnapMirrorCheckPanel
                enabled={(policies ?? []).some((p) => policyIds.includes(p.id) && p.snapmirror_update)}
                groups={[{ scope, members }]}
              />

              <Group justify="flex-end" mt="sm">
                <Button variant="default" onClick={onClose}>
                  Abbrechen
                </Button>
                <Button onClick={() => setActive(1)} disabled={!name}>
                  Weiter
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>

          <Stepper.Step label="Zeitplan" description="Pro Policy">
            <Stack mt="md">
              <Text size="sm" c="dimmed">
                Jede verknüpfte Policy bekommt ihren eigenen Zeitplan — so lässt sich z.B. dieselbe Protection Group
                stündlich UND wöchentlich sichern (über zwei Policies), oder mehrere Protection Groups mit derselben
                Policy zeitversetzt statt gleichzeitig.
              </Text>

              {policyLinks.length === 0 && (
                <Text size="sm" c="dimmed">
                  Keine Policy verknüpft — auf der vorherigen Seite mindestens eine auswählen, um einen Zeitplan
                  festzulegen. Ohne Policy-Verknüpfung wird diese Protection Group von keinem Backup erfasst.
                </Text>
              )}

              {policyLinks.map((link) => {
                const policy = policies?.find((p) => p.id === link.policy_id);
                return (
                  <Select
                    key={link.policy_id}
                    label={policy?.name ?? link.policy_id}
                    placeholder="Kein Zeitplan (nur manuell)"
                    data={[
                      { value: NEW_SCHEDULE_VALUE, label: "+ Neuen Zeitplan erstellen..." },
                      ...(schedules?.map((s) => ({ value: s.id, label: `${s.name} (${formatSchedule(s)})` })) ?? []),
                    ]}
                    value={link.schedule_id}
                    onChange={(v) => {
                      if (v === NEW_SCHEDULE_VALUE) {
                        setScheduleTargetPolicyId(link.policy_id);
                        setScheduleModalOpen(true);
                        return;
                      }
                      setLinkSchedule(link.policy_id, v);
                    }}
                    clearable
                  />
                );
              })}

              <Group justify="flex-end" mt="sm">
                <Button variant="default" onClick={() => setActive(0)}>
                  Zurück
                </Button>
                <Button onClick={handleSubmit} loading={isPending} disabled={!name}>
                  {isEdit ? "Speichern" : "Anlegen"}
                </Button>
              </Group>
            </Stack>
          </Stepper.Step>
        </Stepper>
      </Modal>

      <PolicyFormModal
        opened={policyModalOpen}
        onClose={() => setPolicyModalOpen(false)}
        onSaved={(saved) => setPolicyLinks((prev) => [...prev, { policy_id: saved.id, schedule_id: null }])}
      />
      <ScheduleFormModal
        opened={scheduleModalOpen}
        onClose={() => setScheduleModalOpen(false)}
        onSaved={(s) => {
          if (scheduleTargetPolicyId) setLinkSchedule(scheduleTargetPolicyId, s.id);
        }}
      />
    </>
  );
}
