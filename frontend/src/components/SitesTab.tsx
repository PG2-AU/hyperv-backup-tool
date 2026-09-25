import { useEffect, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  ColorSwatch,
  Group,
  Modal,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
  useMantineTheme,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconEdit, IconPlus, IconTrash } from "@tabler/icons-react";

import {
  useDeleteSite,
  useSaveSite,
  useSetCsvSiteOverride,
  useSetNetAppSite,
  useSetNodeSite,
  useSiteAssignments,
  useSites,
} from "@/api/hooks.sites";
import type { Site } from "@/api/types";
import { SiteBadgeView } from "@/components/SiteBadge";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";

// Mantine-Farbnamen fuer die Standort-Badges -- bewusst eine kleine,
// gut unterscheidbare Auswahl statt eines freien Farbwaehlers.
const SITE_COLORS = ["blue", "grape", "teal", "orange", "cyan", "pink", "lime", "indigo"];

function SiteSelect({
  sites,
  value,
  onChange,
  placeholder = "Kein Standort",
  disabled,
}: {
  sites: Site[];
  value: string | null | undefined;
  onChange: (siteId: string | null) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <Select
      size="xs"
      w={200}
      placeholder={placeholder}
      data={sites.map((s) => ({ value: s.id, label: s.name }))}
      value={value ?? null}
      onChange={onChange}
      clearable
      disabled={disabled}
    />
  );
}

function SiteFormModal({ opened, onClose, site }: { opened: boolean; onClose: () => void; site: Site | null }) {
  const saveSite = useSaveSite();
  const theme = useMantineTheme();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [color, setColor] = useState("blue");

  useEffect(() => {
    if (!opened) return;
    setName(site?.name ?? "");
    setDescription(site?.description ?? "");
    setColor(site?.color ?? "blue");
  }, [opened, site]);

  function handleSubmit() {
    saveSite
      .mutateAsync({ id: site?.id, payload: { name, description: description || null, color } })
      .then((saved) => {
        notifications.show({ title: site ? "Standort aktualisiert" : "Standort angelegt", message: saved.name, color: "green" });
        onClose();
      })
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Standort konnte nicht gespeichert werden."), color: "red" }),
      );
  }

  return (
    <Modal opened={opened} onClose={onClose} title={site ? "Standort bearbeiten" : "Standort anlegen"}>
      <Stack>
        <TextInput label="Name" placeholder="z.B. DC1" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput
          label="Beschreibung"
          placeholder="optional, z.B. Rechenzentrum Nord"
          value={description}
          onChange={(e) => setDescription(e.currentTarget.value)}
        />
        <Select
          label="Farbe"
          description="Farbe des Standort-Badges im Inventory"
          data={SITE_COLORS.map((c) => ({ value: c, label: c }))}
          value={color}
          onChange={(v) => v && setColor(v)}
          allowDeselect={false}
          leftSection={<ColorSwatch color={theme.colors[color]?.[6] ?? color} size={14} />}
          renderOption={({ option }) => (
            <Group gap="xs">
              <ColorSwatch color={theme.colors[option.value]?.[6] ?? option.value} size={14} />
              <Text size="sm">{option.label}</Text>
            </Group>
          )}
        />
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={onClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={saveSite.isPending} disabled={!name.trim()}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function SitesTab() {
  const { data: sites = [] } = useSites();
  const { data: assignments } = useSiteAssignments();
  const deleteSite = useDeleteSite();
  const setNodeSite = useSetNodeSite();
  const setNetAppSite = useSetNetAppSite();
  const setCsvOverride = useSetCsvSiteOverride();
  const [editing, setEditing] = useState<Site | null>(null);
  const [formOpen, setFormOpen] = useState(false);

  const siteById = new Map(sites.map((s) => [s.id, s]));
  const onError = (fallback: string) => (err: unknown) =>
    notifications.show({ title: "Fehler", message: apiErrorMessage(err, fallback), color: "red" });

  function handleDelete(site: Site) {
    confirmAction({
      title: "Standort löschen",
      message: `Standort '${site.name}' wirklich löschen? Alle Zuordnungen von Hyper-V-Knoten, NetApp-Systemen und CSVs zu diesem Standort werden entfernt.`,
      confirmLabel: "Löschen",
      color: "red",
      onConfirm: () => deleteSite.mutate(site.id, { onError: onError("Standort konnte nicht gelöscht werden.") }),
    });
  }

  const noSites = sites.length === 0;

  return (
    <Stack maw={1100}>
      <Paper p="md">
        <Group justify="space-between" mb={4}>
          <Title order={5}>Standorte</Title>
          <Button
            size="xs"
            leftSection={<IconPlus size={14} />}
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            Standort anlegen
          </Button>
        </Group>
        <Text size="xs" c="dimmed" mb="md">
          Kennzeichnet, in welchem Rechenzentrum Hyper-V-Knoten und Storage stehen. Eine VM, deren Host an einem anderen
          Standort steht als (mindestens eine) ihrer CSVs, wird im Inventory markiert und nach Ablauf der Karenzzeit (Settings
          &gt; Alarms) als Alarm gemeldet. Die App verschiebt dabei nichts selbst.
        </Text>
        {noSites ? (
          <Text size="sm" c="dimmed">
            Noch keine Standorte angelegt.
          </Text>
        ) : (
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Beschreibung</Table.Th>
                <Table.Th w={80} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sites.map((site) => (
                <Table.Tr key={site.id}>
                  <Table.Td>
                    <SiteBadgeView site={site} />
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{site.description ?? "—"}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Tooltip label="Bearbeiten">
                        <ActionIcon
                          variant="subtle"
                          onClick={() => {
                            setEditing(site);
                            setFormOpen(true);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Löschen">
                        <ActionIcon variant="subtle" color="red" onClick={() => handleDelete(site)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Paper>

      {assignments && assignments.switchover_clusters.length > 0 && (
        <Alert color="orange" icon={<IconAlertTriangle size={16} />} title="MetroCluster nicht im Normalbetrieb">
          {assignments.switchover_clusters.join(", ")} meldet einen Switchover. Die Standortprüfung ist ausgesetzt, bis wieder
          alle Systeme im Normalbetrieb laufen. Bereits aktive Standort-Alarme bleiben bis dahin unverändert stehen.
        </Alert>
      )}

      <Paper p="md">
        <Title order={5} mb={4}>
          Hyper-V-Knoten
        </Title>
        <Text size="xs" c="dimmed" mb="md">
          Knoten laut letztem Health-Check (Get-ClusterNode), ergänzt um alle Hosts, auf denen gerade VMs laufen.
        </Text>
        {(assignments?.hyperv_clusters ?? []).map((cluster) => (
          <Stack key={cluster.cluster_id} gap={4} mb="md">
            <Text size="sm" fw={500}>
              {cluster.cluster_name}
            </Text>
            <Table>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Knoten</Table.Th>
                  <Table.Th w={100}>VMs</Table.Th>
                  <Table.Th w={220}>Standort</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {cluster.nodes.map((node) => (
                  <Table.Tr key={node.node_name}>
                    <Table.Td>
                      <Text size="sm">{node.node_name}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{node.vm_count}</Text>
                    </Table.Td>
                    <Table.Td>
                      <SiteSelect
                        sites={sites}
                        value={node.site_id}
                        disabled={noSites}
                        onChange={(siteId) =>
                          setNodeSite.mutate(
                            { cluster_id: cluster.cluster_id, node_name: node.node_name, site_id: siteId },
                            { onError: onError("Zuordnung konnte nicht gespeichert werden.") },
                          )
                        }
                      />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Stack>
        ))}
        {assignments && assignments.hyperv_clusters.length === 0 && (
          <Text size="sm" c="dimmed">
            Keine Hyper-V-Cluster registriert.
          </Text>
        )}
      </Paper>

      <Paper p="md">
        <Title order={5} mb={4}>
          NetApp-Systeme
        </Title>
        <Text size="xs" c="dimmed" mb="md">
          Jede CSV erbt den Standort des NetApp-Systems, auf dem ihre LUN liegt. Bei MetroCluster ist jede Site ein eigenes
          System; die inaktive „-mc“-Spiegel-SVM der Gegenseite wird dabei ignoriert.
        </Text>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>System</Table.Th>
              <Table.Th>MetroCluster</Table.Th>
              <Table.Th w={220}>Standort</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(assignments?.netapp_clusters ?? []).map((cluster) => (
              <Table.Tr key={cluster.netapp_cluster_id}>
                <Table.Td>
                  <Text size="sm">{cluster.name}</Text>
                </Table.Td>
                <Table.Td>
                  {cluster.is_metrocluster ? (
                    <Badge size="sm" variant="light" color={!cluster.metrocluster_mode || cluster.metrocluster_mode === "normal" ? "green" : "orange"}>
                      {cluster.metrocluster_mode ?? "ja"}
                    </Badge>
                  ) : (
                    <Text size="sm" c="dimmed">
                      nein
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <SiteSelect
                    sites={sites}
                    value={cluster.site_id}
                    disabled={noSites}
                    onChange={(siteId) =>
                      setNetAppSite.mutate(
                        { netapp_cluster_id: cluster.netapp_cluster_id, site_id: siteId },
                        { onError: onError("Zuordnung konnte nicht gespeichert werden.") },
                      )
                    }
                  />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Paper>

      <Paper p="md">
        <Title order={5} mb={4}>
          CSVs
        </Title>
        <Text size="xs" c="dimmed" mb="md">
          Standort je CSV, normalerweise vom NetApp-System geerbt. Eine abweichende Auswahl überschreibt den geerbten Wert nur
          für diese CSV (gespeichert über die LUN-Seriennummer, übersteht also Umbenennungen).
        </Text>
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>CSV</Table.Th>
              <Table.Th>Hyper-V-Cluster</Table.Th>
              <Table.Th>NetApp-System</Table.Th>
              <Table.Th>Geerbt</Table.Th>
              <Table.Th w={220}>Abweichend festlegen</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(assignments?.csvs ?? []).map((csv) => {
              const inherited = csv.inherited_site_id ? siteById.get(csv.inherited_site_id) : undefined;
              return (
                <Table.Tr key={`${csv.cluster_id}:${csv.csv_name}`}>
                  <Table.Td>
                    <Text size="sm">{csv.csv_name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{csv.cluster_name}</Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm">{csv.netapp_cluster_name ?? "—"}</Text>
                  </Table.Td>
                  <Table.Td>
                    {inherited ? (
                      <SiteBadgeView site={inherited} />
                    ) : (
                      <Text size="sm" c="dimmed">
                        —
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {csv.disk_serial_number ? (
                      <SiteSelect
                        sites={sites}
                        value={csv.override_site_id}
                        placeholder="wie geerbt"
                        disabled={noSites}
                        onChange={(siteId) =>
                          setCsvOverride.mutate(
                            {
                              cluster_id: csv.cluster_id,
                              disk_serial_number: csv.disk_serial_number!,
                              csv_name: csv.csv_name,
                              site_id: siteId,
                            },
                            { onError: onError("Zuordnung konnte nicht gespeichert werden.") },
                          )
                        }
                      />
                    ) : (
                      <Text size="xs" c="dimmed">
                        keine Disk-Seriennummer bekannt
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Paper>

      <SiteFormModal opened={formOpen} onClose={() => setFormOpen(false)} site={editing} />
    </Stack>
  );
}
