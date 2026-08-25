import { useEffect, useState } from "react";
import { Button, Group, Modal, MultiSelect, Select, Stack, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import {
  useCreateResourceGroup,
  useCsvs,
  usePolicies,
  useUpdateResourceGroup,
  useVms,
  type ResourceGroupWritePayload,
} from "@/api/hooks";
import type { BackupScope, ResourceGroup } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const SCOPE_OPTIONS: { value: BackupScope; label: string }[] = [
  { value: "vm", label: "Virtuelle Maschinen" },
  { value: "csv", label: "Cluster Shared Volumes" },
];

interface ResourceGroupFormModalProps {
  opened: boolean;
  onClose: () => void;
  group?: ResourceGroup | null;
}

export function ResourceGroupFormModal({ opened, onClose, group }: ResourceGroupFormModalProps) {
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

  useEffect(() => {
    if (!opened) return;
    if (group) {
      setName(group.name);
      setScope(group.scope);
      setMembers(group.members);
      setPolicyIds(group.policies.map((p) => p.id));
    } else {
      setName("");
      setScope("vm");
      setMembers([]);
      setPolicyIds([]);
    }
  }, [opened, group]);

  const memberOptions = scope === "vm" ? (vms?.map((v) => v.name) ?? []) : (csvs?.map((c) => c.name) ?? []);

  function handleScopeChange(value: string | null) {
    if (!value) return;
    setScope(value as BackupScope);
    setMembers([]);
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
    <Modal opened={opened} onClose={onClose} title={isEdit ? "Protection Group bearbeiten" : "Protection Group anlegen"} size="lg">
      <Stack>
        <TextInput label="Name" placeholder="z.B. Bronze" required value={name} onChange={(e) => setName(e.currentTarget.value)} />

        <Select label="Typ" data={SCOPE_OPTIONS} value={scope} onChange={handleScopeChange} allowDeselect={false} disabled={isEdit} />

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
          data={policies?.map((p) => ({ value: p.id, label: p.name })) ?? []}
          value={policyIds}
          onChange={setPolicyIds}
          searchable
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
  );
}
