import { useEffect, useState } from "react";
import { Alert, Button, Group, Modal, MultiSelect, Select, Stack, TextInput } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { notifications } from "@mantine/notifications";

import {
  useCreateResourceGroup,
  useCsvs,
  usePolicies,
  useUpdateResourceGroup,
  useVms,
  type ResourceGroupWritePayload,
} from "@/api/hooks";
import { PolicyFormModal } from "@/components/PolicyFormModal";
import { SnapMirrorCheckPanel } from "@/components/SnapMirrorCheckPanel";
import type { BackupScope, ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";
import { makeMemberKey } from "@/utils/resourceGroupMember";

const SCOPE_OPTIONS: { value: BackupScope; label: string }[] = [
  { value: "vm", label: "Virtuelle Maschinen" },
  { value: "csv", label: "Cluster Shared Volumes" },
];

const NEW_POLICY_VALUE = "__new_policy__";

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
  const isEdit = !!group;

  const [name, setName] = useState("");
  const [scope, setScope] = useState<BackupScope>("vm");
  const [members, setMembers] = useState<string[]>([]);
  const [policyIds, setPolicyIds] = useState<string[]>([]);
  const [policyModalOpen, setPolicyModalOpen] = useState(false);

  useEffect(() => {
    if (!opened) return;
    const source = group ?? duplicateFrom;
    if (source) {
      // Beim Duplizieren (group nicht gesetzt, nur duplicateFrom) den Namen
      // mit einem Zusatz vorbelegen -- verhindert einen sofortigen
      // Name-Konflikt beim Speichern, macht aber weiterhin deutlich, dass
      // ein neuer, eigener Name gewaehlt werden sollte.
      setName(group ? source.name : `${source.name} (Kopie)`);
      setScope(source.scope);
      setMembers(source.members);
      setPolicyIds(source.policies.map((p) => p.id));
    } else {
      setName("");
      setScope("vm");
      setMembers([]);
      setPolicyIds([]);
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
    if (values.includes(NEW_POLICY_VALUE)) {
      setPolicyIds(values.filter((v) => v !== NEW_POLICY_VALUE));
      setPolicyModalOpen(true);
      return;
    }
    setPolicyIds(values);
  }

  function handleSubmit() {
    const payload: ResourceGroupWritePayload = { name, scope, members, policy_ids: policyIds };
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

  return (
    <>
      <Modal
        opened={opened}
        onClose={onClose}
        title={isEdit ? "Protection Group bearbeiten" : duplicateFrom ? "Protection Group duplizieren" : "Protection Group anlegen"}
        size="lg"
      >
        <Stack>
          <TextInput label="Name" placeholder="z.B. Bronze" required value={name} onChange={(e) => setName(e.currentTarget.value)} />

          <Select label="Typ" data={SCOPE_OPTIONS} value={scope} onChange={handleScopeChange} allowDeselect={false} disabled={isEdit} />

          {scope === "csv" && (
            <Alert icon={<IconInfoCircle size={16} />} color="blue" variant="light">
              Empfohlen: nur ein CSV pro Protection Group. Mehrere CSVs in derselben Gruppe lassen sich zwar sichern,
              erschweren aber eine spätere gezielte Wiederherstellung genau dieses einen CSVs.
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
            <Button onClick={handleSubmit} loading={isPending} disabled={!name}>
              {isEdit ? "Speichern" : "Anlegen"}
            </Button>
          </Group>
        </Stack>
      </Modal>

      <PolicyFormModal
        opened={policyModalOpen}
        onClose={() => setPolicyModalOpen(false)}
        onSaved={(saved) => setPolicyIds((prev) => [...prev, saved.id])}
      />
    </>
  );
}
