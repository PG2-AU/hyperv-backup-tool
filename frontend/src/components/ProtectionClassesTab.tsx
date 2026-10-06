import { useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  ColorSwatch,
  Group,
  Modal,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
  useMantineTheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCheck, IconEdit, IconPlus, IconTrash, IconX } from "@tabler/icons-react";

import {
  useAssignProtectionClass,
  useDeleteProtectionClass,
  useProtectionClasses,
  useProtectionStatus,
  useSaveProtectionClass,
} from "@/api/hooks.protectionClasses";
import type { ProtectionClass, ProtectionClassWrite, ProtectionObjectStatus, ProtectionObjectType } from "@/api/hooks.protectionClasses";
import { ProtectionClassCell } from "@/components/ProtectionClassCell";
import { SearchInput } from "@/components/SearchInput";
import { useAuthStore } from "@/store/authStore";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";
import { matchesAllColumns } from "@/utils/search";

// Backup > Schutzklassen (Backlog #86): Klassen als Soll-Vorgabe pflegen,
// VMs/CSVs/SMB3-Freigaben zuweisen (einzeln oder mehrere auf einmal) und
// das Pruefergebnis sehen.

const COLORS = [
  { value: "yellow", label: "Gold" },
  { value: "gray", label: "Silber" },
  { value: "orange", label: "Bronze" },
  { value: "blue", label: "Blau" },
  { value: "green", label: "Grün" },
  { value: "red", label: "Rot" },
  { value: "grape", label: "Violett" },
  { value: "teal", label: "Türkis" },
];

// Nur ein Vorschlag -- Namen und Werte sind danach frei aenderbar.
const SUGGESTED: ProtectionClassWrite[] = [
  { name: "Gold", rank: 1, color: "yellow", max_backup_age_hours: 4, min_retention_days: 14, secondary_retention_days: 60, require_app_consistent: true },
  { name: "Silber", rank: 2, color: "gray", max_backup_age_hours: 26, min_retention_days: 7, secondary_retention_days: 30, require_app_consistent: true },
  { name: "Bronze", rank: 3, color: "orange", max_backup_age_hours: 168, min_retention_days: 7, secondary_retention_days: 0, require_app_consistent: false },
];

const TYPE_LABEL: Record<ProtectionObjectType, string> = { vm: "VMs", csv: "CSVs", smb_share: "SMB3-Freigaben" };

function ageText(hours: number): string {
  if (hours < 48) return `${hours} h`;
  return hours % 24 === 0 ? `${hours / 24} Tage` : `${hours} h`;
}

function ClassModal({ initial, editId, nextRank, onClose }: { initial: ProtectionClassWrite; editId?: string; nextRank: number; onClose: () => void }) {
  const theme = useMantineTheme();
  const [form, setForm] = useState<ProtectionClassWrite>({ ...initial, rank: initial.rank || nextRank });
  const save = useSaveProtectionClass();
  const set = <K extends keyof ProtectionClassWrite>(key: K, value: ProtectionClassWrite[K]) => setForm((f) => ({ ...f, [key]: value }));

  function submit() {
    save.mutate(
      { id: editId, payload: { ...form, name: form.name.trim() } },
      {
        onSuccess: onClose,
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Speichern fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened onClose={onClose} title={editId ? "Schutzklasse bearbeiten" : "Schutzklasse anlegen"} size="lg">
      <Stack gap="sm">
        <Group grow align="flex-start">
          <TextInput label="Name" required value={form.name} onChange={(e) => set("name", e.currentTarget.value)} placeholder="z.B. Gold" />
          <NumberInput
            label="Rang"
            description="1 = höchste Klasse. Speicher muss mindestens die Klasse der VM haben."
            inputWrapperOrder={["label", "input", "description", "error"]}
            min={1}
            max={99}
            value={form.rank}
            onChange={(v) => set("rank", typeof v === "number" ? v : 1)}
          />
          <Select
            label="Farbe"
            data={COLORS}
            value={form.color}
            onChange={(v) => v && set("color", v)}
            allowDeselect={false}
            leftSection={<ColorSwatch size={14} color={theme.colors[form.color]?.[6] ?? theme.colors.blue[6]} />}
          />
        </Group>
        <Group grow align="flex-start">
          <NumberInput
            label="Letztes Backup höchstens alt"
            description="in Stunden (26 = täglich mit Puffer, 168 = 7 Tage)"
            inputWrapperOrder={["label", "input", "description", "error"]}
            min={1}
            suffix=" h"
            value={form.max_backup_age_hours}
            onChange={(v) => set("max_backup_age_hours", typeof v === "number" ? v : 26)}
          />
          <NumberInput
            label="Aufbewahrung primär"
            description="so weit zurück muss lokal wiederherstellbar sein"
            inputWrapperOrder={["label", "input", "description", "error"]}
            min={0}
            suffix=" Tage"
            value={form.min_retention_days}
            onChange={(v) => set("min_retention_days", typeof v === "number" ? v : 7)}
          />
          <NumberInput
            label="Aufbewahrung sekundär"
            description="auf dem SnapMirror-Ziel; 0 = nicht verlangt"
            inputWrapperOrder={["label", "input", "description", "error"]}
            min={0}
            suffix=" Tage"
            value={form.secondary_retention_days}
            onChange={(v) => set("secondary_retention_days", typeof v === "number" ? v : 0)}
          />
        </Group>
        <Switch
          label="Applikationskonsistente Sicherung ist Pflicht"
          checked={form.require_app_consistent}
          onChange={(e) => set("require_app_consistent", e.currentTarget.checked)}
        />
        <TextInput label="Beschreibung" value={form.description ?? ""} onChange={(e) => set("description", e.currentTarget.value || null)} />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={submit} loading={save.isPending} disabled={!form.name.trim()}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ClassesTable({ classes, canManage }: { classes: ProtectionClass[]; canManage: boolean }) {
  const save = useSaveProtectionClass();
  const remove = useDeleteProtectionClass();
  const [modal, setModal] = useState<{ initial: ProtectionClassWrite; editId?: string } | null>(null);
  const nextRank = Math.max(0, ...classes.map((c) => c.rank)) + 1;
  const fail = (err: unknown) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Aktion fehlgeschlagen."), color: "red" });

  async function createSuggested() {
    try {
      for (const payload of SUGGESTED) await save.mutateAsync({ payload });
    } catch (err) {
      fail(err);
    }
  }

  return (
    <Paper p="md">
      <Group justify="space-between" mb="xs">
        <Title order={5}>Schutzklassen</Title>
        {canManage && (
          <Button
            leftSection={<IconPlus size={16} />}
            onClick={() =>
              setModal({
                initial: { name: "", rank: nextRank, color: "blue", max_backup_age_hours: 26, min_retention_days: 7, secondary_retention_days: 0, require_app_consistent: false },
              })
            }
          >
            Neue Schutzklasse
          </Button>
        )}
      </Group>
      <Text size="xs" c="dimmed" mb="sm">
        Eine Schutzklasse legt fest, wie ein Objekt mindestens gesichert sein muss. Jede VM und jede CSV/SMB3-Freigabe bekommt ihre Klasse
        von Hand; geprüft wird, ob die Sicherung zur Klasse passt und ob eine VM auf Speicher mindestens ihrer Klasse liegt.
      </Text>
      {classes.length === 0 ? (
        <Alert color="gray" variant="light">
          <Group justify="space-between">
            <Text size="sm">Noch keine Schutzklassen definiert.</Text>
            {canManage && (
              <Button size="compact-sm" variant="light" onClick={createSuggested} loading={save.isPending}>
                Gold / Silber / Bronze als Vorschlag anlegen
              </Button>
            )}
          </Group>
        </Alert>
      ) : (
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={60}>Rang</Table.Th>
              <Table.Th>Klasse</Table.Th>
              <Table.Th>Backup höchstens alt</Table.Th>
              <Table.Th>Aufbewahrung primär</Table.Th>
              <Table.Th>Aufbewahrung sekundär</Table.Th>
              <Table.Th>Applikationskonsistent</Table.Th>
              <Table.Th>Zugewiesen</Table.Th>
              <Table.Th w={90} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {classes.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.rank}</Table.Td>
                <Table.Td>
                  <Badge variant="light" color={c.color}>
                    {c.name}
                  </Badge>
                  {c.description && (
                    <Text size="xs" c="dimmed">
                      {c.description}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{ageText(c.max_backup_age_hours)}</Table.Td>
                <Table.Td>{c.min_retention_days} Tage</Table.Td>
                <Table.Td>{c.secondary_retention_days ? `${c.secondary_retention_days} Tage` : "–"}</Table.Td>
                <Table.Td>{c.require_app_consistent ? "Pflicht" : "–"}</Table.Td>
                <Table.Td>{c.assigned_count}</Table.Td>
                <Table.Td>
                  {canManage && (
                    <Group gap={4} wrap="nowrap">
                      <ActionIcon variant="subtle" onClick={() => setModal({ initial: c, editId: c.id })} aria-label="Bearbeiten">
                        <IconEdit size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        aria-label="Löschen"
                        onClick={() =>
                          confirmAction({
                            title: "Schutzklasse löschen",
                            message: `Schutzklasse "${c.name}" löschen? ${c.assigned_count} Zuordnung(en) werden entfernt.`,
                            confirmLabel: "Löschen",
                            onConfirm: () => remove.mutate(c.id, { onError: fail }),
                          })
                        }
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {modal && (
        <ClassModal
          initial={{
            name: modal.initial.name,
            rank: modal.initial.rank,
            color: modal.initial.color,
            description: modal.initial.description ?? null,
            max_backup_age_hours: modal.initial.max_backup_age_hours,
            min_retention_days: modal.initial.min_retention_days,
            secondary_retention_days: modal.initial.secondary_retention_days,
            require_app_consistent: modal.initial.require_app_consistent,
          }}
          editId={modal.editId}
          nextRank={nextRank}
          onClose={() => setModal(null)}
        />
      )}
    </Paper>
  );
}

type StatusFilter = "all" | "violation" | "unassigned";

function AssignmentTable({ classes, canManage }: { classes: ProtectionClass[]; canManage: boolean }) {
  const { data: statuses = [], isLoading } = useProtectionStatus();
  const assign = useAssignProtectionClass();
  const [type, setType] = useState<ProtectionObjectType>("vm");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkClass, setBulkClass] = useState<string | null>(null);

  const key = (s: ProtectionObjectStatus) => `${s.cluster_id}|${s.name}`;
  const ofType = useMemo(() => statuses.filter((s) => s.object_type === type), [statuses, type]);
  const rows = useMemo(
    () =>
      ofType
        .filter((s) => filter === "all" || s.status === filter)
        .filter((s) => matchesAllColumns({ name: s.display_name, cluster: s.cluster_name, cls: s.class_name, groups: s.resource_group_names.join(" ") }, search))
        .sort((a, b) => a.display_name.localeCompare(b.display_name, "de")),
    [ofType, filter, search],
  );
  const count = (f: StatusFilter) => (f === "all" ? ofType.length : ofType.filter((s) => s.status === f).length);
  const visibleSelected = rows.filter((r) => selected.has(key(r)));

  function applyBulk(classId: string | null) {
    const objects = visibleSelected.filter((r) => r.cluster_id).map((r) => ({ object_type: type, cluster_id: r.cluster_id as string, name: r.name }));
    if (!objects.length) return;
    assign.mutate(
      { class_id: classId, objects },
      {
        onSuccess: () => {
          setSelected(new Set());
          notifications.show({ message: `${objects.length} Objekt(e) ${classId ? "zugewiesen" : "ohne Schutzklasse"}.`, color: "green" });
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zuweisung fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Paper p="md">
      <Title order={5} mb="xs">
        Zuordnung und Prüfung
      </Title>
      <Group justify="space-between" mb="sm">
        <Group>
          <SegmentedControl
            size="xs"
            value={type}
            onChange={(v) => {
              setType(v as ProtectionObjectType);
              setSelected(new Set());
            }}
            data={(Object.keys(TYPE_LABEL) as ProtectionObjectType[]).map((t) => ({
              value: t,
              label: `${TYPE_LABEL[t]} (${statuses.filter((s) => s.object_type === t).length})`,
            }))}
          />
          <SegmentedControl
            size="xs"
            value={filter}
            onChange={(v) => setFilter(v as StatusFilter)}
            data={[
              { value: "all", label: `Alle (${count("all")})` },
              { value: "violation", label: `Nicht erfüllt (${count("violation")})` },
              { value: "unassigned", label: `Ohne Klasse (${count("unassigned")})` },
            ]}
          />
          <SearchInput value={search} onChange={setSearch} />
        </Group>
        {canManage && (
          <Group gap="xs">
            <Text size="xs" c="dimmed">
              {visibleSelected.length} ausgewählt
            </Text>
            <Select
              size="xs"
              w={170}
              placeholder="Klasse wählen"
              data={classes.map((c) => ({ value: c.id, label: c.name }))}
              value={bulkClass}
              onChange={setBulkClass}
              clearable
            />
            <Button size="xs" disabled={!visibleSelected.length || !bulkClass} loading={assign.isPending} onClick={() => applyBulk(bulkClass)}>
              Zuweisen
            </Button>
            <Tooltip label="Schutzklasse der ausgewählten Objekte entfernen">
              <ActionIcon variant="default" disabled={!visibleSelected.length} onClick={() => applyBulk(null)} aria-label="Klasse entfernen">
                <IconX size={14} />
              </ActionIcon>
            </Tooltip>
          </Group>
        )}
      </Group>
      <Box style={{ maxHeight: "calc(100vh - 520px)", minHeight: 260, overflowY: "auto" }}>
        <Table striped highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              {canManage && (
                <Table.Th w={36}>
                  <Checkbox
                    size="xs"
                    checked={rows.length > 0 && visibleSelected.length === rows.length}
                    indeterminate={visibleSelected.length > 0 && visibleSelected.length < rows.length}
                    onChange={(e) => setSelected(e.currentTarget.checked ? new Set(rows.map(key)) : new Set())}
                    aria-label="Alle auswählen"
                  />
                </Table.Th>
              )}
              <Table.Th>Name</Table.Th>
              <Table.Th>Cluster</Table.Th>
              <Table.Th>Schutzklasse</Table.Th>
              {type === "vm" && <Table.Th>Speicher</Table.Th>}
              <Table.Th>Protection Group</Table.Th>
              <Table.Th>Letztes Backup</Table.Th>
              <Table.Th>Prüfergebnis</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={key(r)}>
                {canManage && (
                  <Table.Td>
                    <Checkbox
                      size="xs"
                      checked={selected.has(key(r))}
                      onChange={(e) => {
                        const next = new Set(selected);
                        if (e.currentTarget.checked) next.add(key(r));
                        else next.delete(key(r));
                        setSelected(next);
                      }}
                      aria-label={`${r.display_name} auswählen`}
                    />
                  </Table.Td>
                )}
                <Table.Td>
                  <Text size="sm" fw={500}>
                    {r.display_name}
                  </Text>
                </Table.Td>
                <Table.Td>{r.cluster_name ?? "–"}</Table.Td>
                <Table.Td>
                  <ProtectionClassCell objectType={r.object_type} clusterId={r.cluster_id} name={r.name} />
                </Table.Td>
                {type === "vm" && (
                  <Table.Td>
                    {r.storage.length === 0
                      ? "–"
                      : r.storage.map((s) => (
                          <Group key={s.name} gap={6} wrap="nowrap">
                            <Text size="xs">{s.name}</Text>
                            <Badge size="xs" variant={s.class_name ? "light" : "outline"} color={s.class_color ?? "gray"}>
                              {s.class_name ?? "keine"}
                            </Badge>
                          </Group>
                        ))}
                  </Table.Td>
                )}
                <Table.Td>
                  <Text size="xs">{r.resource_group_names.join(", ") || "–"}</Text>
                </Table.Td>
                <Table.Td>
                  <Text size="xs">{formatDateTime(r.last_backup_at)}</Text>
                </Table.Td>
                <Table.Td>
                  {r.status === "unassigned" ? (
                    <Text size="xs" c="dimmed">
                      keine Klasse zugewiesen
                    </Text>
                  ) : r.status === "ok" ? (
                    <Group gap={4} wrap="nowrap">
                      <IconCheck size={14} color="var(--mantine-color-green-6)" />
                      <Text size="xs">erfüllt</Text>
                    </Group>
                  ) : (
                    <>
                      {r.violations.map((v) => (
                        <Text size="xs" c="red" key={v}>
                          • {v}
                        </Text>
                      ))}
                      {r.suggested_groups.length > 0 && (
                        <Text size="xs" c="dimmed">
                          Passende Protection Group: {r.suggested_groups.join(", ")}
                        </Text>
                      )}
                    </>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={8}>
                  <Text size="sm" c="dimmed" ta="center" py="md">
                    Keine Objekte.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Box>
    </Paper>
  );
}

export function ProtectionClassesTab() {
  const canManage = useAuthStore((s) => s.hasPermission)("backup:create");
  const { data: classes = [], error } = useProtectionClasses();
  return (
    <Stack>
      {error && <Alert color="red">{apiErrorMessage(error, "Schutzklassen konnten nicht geladen werden.")}</Alert>}
      <ClassesTable classes={classes} canManage={canManage} />
      <AssignmentTable classes={classes} canManage={canManage} />
    </Stack>
  );
}
