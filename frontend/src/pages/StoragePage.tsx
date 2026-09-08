import { useEffect, useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  MultiSelect,
  Paper,
  PasswordInput,
  Progress,
  SegmentedControl,
  Stack,
  Switch,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconCertificate,
  IconEdit,
  IconLink,
  IconPlus,
  IconRadar2,
  IconRefresh,
  IconShieldCheck,
  IconShieldOff,
  IconTrash,
} from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import {
  useAggregates,
  useClusterPeers,
  useCreateNetAppCluster,
  useDeleteNetAppCluster,
  useDiscoverNetAppCluster,
  useEnrollNetAppClusterCertificate,
  useIgroups,
  useLuns,
  useMetroClusterStatus,
  useNetAppClusters,
  useNetAppSchedules,
  usePlatforms,
  useSnapmirrorPolicies,
  useSnapMirrorRelationships,
  useStorageAccess,
  useSvmPeers,
  useSvms,
  useUpdateStorageAccess,
  useUpdateNetAppCluster,
  useVerifyNetAppCluster,
  useVolumes,
} from "@/api/hooks";
import { ClusterPeerFormModal } from "@/components/ClusterPeerFormModal";
import { DiscoveryModal } from "@/components/DiscoveryModal";
import { IgroupFormModal } from "@/components/IgroupFormModal";
import { LunEditModal } from "@/components/LunEditModal";
import { LunFormModal } from "@/components/LunFormModal";
import { NetAppScheduleFormModal } from "@/components/NetAppScheduleFormModal";
import { ProcessModal } from "@/components/ProcessModal";
import type { ProcessPlan } from "@/components/ProcessModal";
import { SearchInput } from "@/components/SearchInput";
import { SnapMirrorPolicyEditModal } from "@/components/SnapMirrorPolicyEditModal";
import { SnapMirrorPolicyFormModal } from "@/components/SnapMirrorPolicyFormModal";
import { SnapmirrorEditModal } from "@/components/SnapmirrorEditModal";
import { SnapmirrorFormModal } from "@/components/SnapmirrorFormModal";
import { CapacityBarCard, DistributionCard, StatCard, StatRibbon, groupCount } from "@/components/StatRibbon";
import { SvmPeerFormModal } from "@/components/SvmPeerFormModal";
import { VolumeEditModal } from "@/components/VolumeEditModal";
import { VolumeFormModal } from "@/components/VolumeFormModal";
import type {
  NetAppCluster,
  NetAppClusterPeer,
  NetAppLun,
  NetAppSnapMirrorPolicy,
  NetAppSystemType,
  NetAppVolume,
  SnapMirrorRelationship,
} from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatLagTime } from "@/utils/format";
import { matchesAllColumns } from "@/utils/search";
import {
  buildLunCreationSteps,
  buildLunDeleteSteps,
  buildLunEditSteps,
  buildPolicyCreationSteps,
  buildPolicyEditSteps,
  buildScheduleCreationSteps,
  buildSnapmirrorCreationSteps,
  buildSnapmirrorEditSteps,
  buildVolumeCreationSteps,
  buildVolumeDeleteSteps,
  buildVolumeEditSteps,
} from "@/utils/netappSteps";

const HEALTH_COLOR: Record<string, string> = { healthy: "green", degraded: "yellow", unreachable: "red", unknown: "gray" };
const HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy",
  degraded: "Eingeschränkt",
  unreachable: "Nicht erreichbar",
  unknown: "Unbekannt",
};

// ONTAP-REST kennt auf Policy-Ebene nur type=async/sync/continuous; die
// feinere Kategorie, die die ONTAP-CLI als "Type" zeigt (vault,
// mirror-vault, async-mirror, sync-mirror, strict-sync-mirror), liefert
// das Backend bereits abgeleitet im Feld display_type -- hier nur noch
// die Anzeigebeschriftung.
const SNAPMIRROR_POLICY_TYPE_LABEL: Record<string, string> = {
  vault: "Vault",
  mirror_vault: "Mirror-Vault",
  async_mirror: "Async-Mirror",
  sync_mirror: "Sync-Mirror",
  strict_sync_mirror: "Strict-Sync-Mirror",
  automated_failover_sync: "Automated-FailOver-Sync",
  continuous: "Continuous",
};

function AddClusterModal({
  opened,
  onClose,
  onCreated,
  cluster,
}: {
  opened: boolean;
  onClose: () => void;
  onCreated: (cluster: NetAppCluster) => void;
  // Gesetzt = Bearbeiten-Modus (z.B. fuer eine Passwort-Rotation) statt
  // Neuanlage -- aendert den bestehenden Cluster IN PLACE (gleiche
  // cluster.id bleibt erhalten), siehe update_cluster in netapp_clusters.py.
  cluster?: NetAppCluster | null;
}) {
  const isEdit = !!cluster;
  const createCluster = useCreateNetAppCluster();
  const updateCluster = useUpdateNetAppCluster();
  const [name, setName] = useState("");
  const [systemType, setSystemType] = useState<NetAppSystemType>("cluster");
  const [mgmtLif, setMgmtLif] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [verifySsl, setVerifySsl] = useState(true);

  useEffect(() => {
    if (!opened) return;
    setName(cluster?.name ?? "");
    setSystemType(cluster?.system_type ?? "cluster");
    setMgmtLif(cluster?.management_lif ?? "");
    setUsername(cluster?.username ?? "");
    // Passwort bleibt beim Bearbeiten bewusst leer, siehe Hinweistext unten.
    setPassword("");
    setVerifySsl(cluster?.verify_ssl ?? true);
  }, [opened, cluster]);

  function handleClose() {
    onClose();
  }

  function handleSubmit() {
    const onSuccess = (updated: NetAppCluster) => {
      notifications.show({
        title: isEdit ? "System aktualisiert" : "System hinzugefügt",
        message: `'${updated.name}' verbunden (ONTAP ${updated.ontap_version ?? "?"}).`,
        color: "green",
      });
      handleClose();
      onCreated(updated);
    };
    const onError = (err: unknown) => {
      notifications.show({
        title: "Verbindung fehlgeschlagen",
        message: apiErrorMessage(err, isEdit ? "System konnte nicht aktualisiert werden." : "System konnte nicht hinzugefügt werden."),
        color: "red",
      });
    };
    if (isEdit) {
      updateCluster.mutate(
        { id: cluster!.id, payload: { name, management_lif: mgmtLif, username, password: password || undefined, verify_ssl: verifySsl } },
        { onSuccess, onError },
      );
      return;
    }
    createCluster.mutate({ name, system_type: systemType, management_lif: mgmtLif, username, password, verify_ssl: verifySsl }, { onSuccess, onError });
  }

  const isPending = createCluster.isPending || updateCluster.isPending;
  const canSubmit = !!name && !!mgmtLif && !!username && (isEdit || !!password);

  return (
    <Modal opened={opened} onClose={handleClose} title={isEdit ? `NetApp-System bearbeiten: ${cluster!.name}` : "NetApp-System hinzufügen"}>
      <Stack>
        {!isEdit && (
          <SegmentedControl
            fullWidth
            value={systemType}
            onChange={(v) => setSystemType(v as NetAppSystemType)}
            data={[
              { label: "Ganzer Cluster", value: "cluster" },
              { label: "Einzelne SVM", value: "svm" },
            ]}
          />
        )}
        {isEdit && (
          <Text size="xs" c="dimmed">
            Typ ({systemType === "svm" ? "Einzelne SVM" : "Ganzer Cluster"}) ist nach dem Hinzufügen nicht mehr änderbar.
          </Text>
        )}
        <TextInput label="System-Name" placeholder="z.B. NETAPP-PROD" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput
          label={systemType === "svm" ? "SVM-Management-IP" : "Cluster-Management-IP"}
          description={
            systemType === "svm"
              ? "Eigene Management-LIF der SVM, falls vorhanden -- sonst die Cluster-Management-IP, die Zugangsdaten unten entscheiden über den Umfang."
              : undefined
          }
          placeholder="z.B. 10.0.0.10"
          required
          value={mgmtLif}
          onChange={(e) => setMgmtLif(e.currentTarget.value)}
        />
        <TextInput
          label="Benutzername"
          description={systemType === "svm" ? "Ein an genau diese SVM gebundener Benutzer (ONTAP-Rolle vsadmin)" : undefined}
          required
          value={username}
          onChange={(e) => setUsername(e.currentTarget.value)}
        />
        <PasswordInput
          label="Kennwort"
          placeholder={isEdit ? "Leer lassen, um das bestehende Kennwort beizubehalten" : undefined}
          required={!isEdit}
          value={password}
          onChange={(e) => setPassword(e.currentTarget.value)}
        />
        <Switch
          label="TLS-Zertifikat des Systems validieren"
          checked={verifySsl}
          onChange={(e) => setVerifySsl(e.currentTarget.checked)}
        />
        <Text size="xs" c="dimmed">
          {isEdit
            ? "Die Verbindung wird mit den neuen Angaben sofort getestet, bevor sie gespeichert werden. Leeres Kennwort behält das bisherige bei."
            : systemType === "svm"
              ? "Bei 'Einzelne SVM' werden Nodes/Aggregate/Cluster Peer/MetroCluster bei der Discovery bewusst nicht abgefragt -- ONTAPs " +
                "eigenes Rechtemodell beschränkt den vsadmin-Zugang ohnehin automatisch auf diese eine SVM. Die Verbindung wird sofort " +
                "getestet, anschließend startet automatisch eine Discovery."
              : "Beim Hinzufügen wird die Verbindung sofort getestet, anschließend startet automatisch eine Discovery. " +
                "Zertifikatsbasierte Authentifizierung kann danach über das Kontextmenü aktiviert werden."}
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={isPending} disabled={!canSubmit}>
            {isEdit ? "Speichern" : "Verbinden & hinzufügen"}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ClusterPeerDetailHeader({ peer, onClose }: { peer: NetAppClusterPeer; onClose: () => void }) {
  return (
    <Paper withBorder p="sm" mb="sm">
      <Group justify="space-between" mb="xs">
        <Text fw={600}>{peer.name ?? "Cluster-Peer"}</Text>
        <Button variant="subtle" size="xs" onClick={onClose}>
          Schließen
        </Button>
      </Group>
      <Group gap="xl" align="flex-start">
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Remote-Name
          </Text>
          <Text size="sm">{peer.remote_name ?? "-"}</Text>
        </Stack>
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Status
          </Text>
          <Text size="sm">{peer.state ?? "-"}</Text>
        </Stack>
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Peer-Adressen (peer-addrs)
          </Text>
          <Text size="sm">{peer.peer_ip_addresses ?? "-"}</Text>
        </Stack>
        <Stack gap={2}>
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            Lokale Intercluster-Adressen (ip-addrs)
          </Text>
          <Text size="sm">{peer.local_ip_addresses ?? "-"}</Text>
        </Stack>
      </Group>
    </Paper>
  );
}

function ClusterTab({ locked }: { locked: boolean }) {
  const { data: clusters } = useNetAppClusters();
  const verifyCluster = useVerifyNetAppCluster();
  const enrollCert = useEnrollNetAppClusterCertificate();
  const deleteCluster = useDeleteNetAppCluster();
  const discoverCluster = useDiscoverNetAppCluster();
  const [addOpen, setAddOpen] = useState(false);
  const [editingCluster, setEditingCluster] = useState<NetAppCluster | null>(null);
  const [clusterSearch, setClusterSearch] = useState("");
  const filteredClusters = (clusters ?? []).filter((c) => matchesAllColumns(c, clusterSearch));

  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  const [discoveryCluster, setDiscoveryCluster] = useState<NetAppCluster | null>(null);

  function runDiscovery(cluster: NetAppCluster) {
    setDiscoveryCluster(cluster);
    setDiscoveryOpen(true);
    discoverCluster.mutate(cluster.id, {
      onError: (err) =>
        notifications.show({ title: "Discovery fehlgeschlagen", message: apiErrorMessage(err, "Unbekannter Fehler."), color: "red" }),
    });
  }

  function handleVerify(cluster: NetAppCluster) {
    verifyCluster.mutate(cluster.id, {
      onSuccess: (c) =>
        notifications.show({
          title: "Verbindung geprüft",
          message: `${c.name}: ${HEALTH_LABEL[c.health]}`,
          color: c.health === "healthy" ? "green" : "orange",
        }),
      onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Prüfung fehlgeschlagen."), color: "red" }),
    });
  }

  function handleEnrollCertificate(cluster: NetAppCluster) {
    enrollCert.mutate(cluster.id, {
      onSuccess: (c) =>
        notifications.show({
          title: "Zertifikat aktiviert",
          message: `${c.name} nutzt jetzt Zertifikats-Authentifizierung.`,
          color: "green",
        }),
      onError: (err) =>
        notifications.show({
          title: "Zertifikats-Umschaltung fehlgeschlagen",
          message: apiErrorMessage(err, "Unbekannter Fehler."),
          color: "red",
        }),
    });
  }

  function handleDelete(cluster: NetAppCluster) {
    confirmAction({
      title: "System entfernen",
      message: `System '${cluster.name}' wirklich entfernen? Die gespeicherten Zugangsdaten werden gelöscht.`,
      confirmLabel: "Entfernen",
      onConfirm: () =>
        deleteCluster.mutate(cluster.id, {
          onSuccess: () => notifications.show({ title: "System entfernt", message: cluster.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "System konnte nicht entfernt werden."), color: "red" }),
        }),
    });
  }

  const versionDistribution = groupCount(clusters, (c) => c.ontap_version);
  const healthDistribution = groupCount(clusters, (c) => HEALTH_LABEL[c.health]);

  return (
    <>
    <Paper p="md">
      <Title order={5} mb="sm">Systeme</Title>

      <StatRibbon>
        <StatCard label="Anzahl Systeme" value={clusters?.length ?? 0} />
        <DistributionCard label="ONTAP-Versionen" items={versionDistribution} />
        <DistributionCard
          label="Gesundheitszustand"
          items={healthDistribution.map((d) => ({ ...d, color: d.key === "Healthy" ? "green" : d.key === "Eingeschränkt" ? "yellow" : "gray" }))}
        />
      </StatRibbon>

      <Group justify="space-between" mb="xs" mt="md">
        <SearchInput value={clusterSearch} onChange={setClusterSearch} />
        <Button leftSection={<IconPlus size={16} />} onClick={() => setAddOpen(true)}>
          System hinzufügen
        </Button>
      </Group>

      <div>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>System-Name</Table.Th>
              <Table.Th>Typ</Table.Th>
              <Table.Th>Mgmt-IP</Table.Th>
              <Table.Th>Benutzer</Table.Th>
              <Table.Th>Auth</Table.Th>
              <Table.Th>ONTAP-Version</Table.Th>
              <Table.Th>Health</Table.Th>
              <Table.Th>MetroCluster</Table.Th>
              <Table.Th>Letzte Prüfung</Table.Th>
              <Table.Th>Aktionen</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {filteredClusters.map((cluster) => (
              <Table.Tr key={cluster.id}>
                <Table.Td>
                  {cluster.name}
                  {cluster.ontap_cluster_name && cluster.ontap_cluster_name !== cluster.name && (
                    <Text size="xs" c="dimmed">
                      ONTAP: {cluster.ontap_cluster_name}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <Badge variant="light" color={cluster.system_type === "svm" ? "teal" : "blue"}>
                    {cluster.system_type === "svm" ? "SVM" : "Cluster"}
                  </Badge>
                </Table.Td>
                <Table.Td>{cluster.management_lif}</Table.Td>
                <Table.Td>{cluster.username}</Table.Td>
                <Table.Td>
                  <Badge variant="light" color={cluster.auth_method === "certificate" ? "indigo" : "gray"}>
                    {cluster.auth_method === "certificate" ? "Zertifikat" : "Kennwort"}
                  </Badge>
                </Table.Td>
                <Table.Td>{cluster.ontap_version ?? "-"}</Table.Td>
                <Table.Td>
                  <Tooltip label={cluster.last_check_error ?? ""} disabled={!cluster.last_check_error}>
                    <Badge color={HEALTH_COLOR[cluster.health]} variant="light">
                      {HEALTH_LABEL[cluster.health]}
                      {cluster.node_count > 0 ? ` (${cluster.healthy_node_count}/${cluster.node_count} Nodes)` : ""}
                    </Badge>
                  </Tooltip>
                </Table.Td>
                <Table.Td>{cluster.system_type === "svm" ? "-" : cluster.is_metrocluster ? "Ja" : "Nein"}</Table.Td>
                <Table.Td>{cluster.last_checked_at ? new Date(cluster.last_checked_at).toLocaleString("de-DE") : "nie"}</Table.Td>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Tooltip label="Verbindung erneut prüfen">
                      <ActionIcon variant="light" disabled={locked} onClick={() => handleVerify(cluster)}>
                        <IconRefresh size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Discovery erneut ausführen">
                      <ActionIcon variant="light" disabled={locked} onClick={() => runDiscovery(cluster)}>
                        <IconRadar2 size={16} />
                      </ActionIcon>
                    </Tooltip>
                    {cluster.auth_method === "password" && (
                      <Tooltip label="Auf Zertifikat umstellen">
                        <ActionIcon variant="light" disabled={locked} onClick={() => handleEnrollCertificate(cluster)}>
                          <IconCertificate size={16} />
                        </ActionIcon>
                      </Tooltip>
                    )}
                    <Tooltip label="Bearbeiten (z.B. Kennwort-Rotation)">
                      {/* Bewusst NICHT disabled={locked} -- wie beim "hinzufuegen"-Button
                          gilt dieselbe Ausnahme (siehe Hinweistext unten): das Aendern der
                          Verbindungsdaten ist keine Storage-Mutation im eigentlichen Sinn,
                          sondern noetig um die Verbindung selbst herzustellen/zu warten
                          (z.B. eine Passwort-Rotation), auch waehrend Storage-Aktionen
                          gesperrt sind. Backend-seitig ebenso ohne require_storage_unlocked. */}
                      <ActionIcon variant="light" onClick={() => setEditingCluster(cluster)}>
                        <IconEdit size={16} />
                      </ActionIcon>
                    </Tooltip>
                    <Tooltip label="Entfernen">
                      <ActionIcon variant="light" color="red" disabled={locked} onClick={() => handleDelete(cluster)}>
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {clusters?.length === 0 && (
          <Text c="dimmed" size="sm" ta="center" py="md">
            Noch keine NetApp-Systeme hinzugefügt.
          </Text>
        )}
        {(clusters?.length ?? 0) > 0 && filteredClusters.length === 0 && (
          <Text c="dimmed" size="sm" ta="center" py="md">
            Kein System passt zur Suche „{clusterSearch}".
          </Text>
        )}
      </div>
    </Paper>

      <AddClusterModal
        opened={addOpen || !!editingCluster}
        cluster={editingCluster}
        onClose={() => {
          setAddOpen(false);
          setEditingCluster(null);
        }}
        onCreated={runDiscovery}
      />

      <DiscoveryModal
        opened={discoveryOpen}
        onClose={() => setDiscoveryOpen(false)}
        clusterName={discoveryCluster?.name}
        steps={discoverCluster.data}
        isLoading={discoverCluster.isPending}
      />
    </>
  );
}

export function StoragePage() {
  const [params, setParams] = useSearchParams();
  const activeTab = params.get("tab") ?? "clusters";
  const { data: svms } = useSvms();
  const { data: volumes } = useVolumes();
  const { data: luns } = useLuns();
  const { data: igroups } = useIgroups();
  const { data: clusterPeers } = useClusterPeers();
  const { data: svmPeers } = useSvmPeers();
  const { data: relationships } = useSnapMirrorRelationships();
  const { data: platforms } = usePlatforms();
  const { data: aggregates } = useAggregates();
  const { data: mcc } = useMetroClusterStatus();
  const { data: clusters } = useNetAppClusters();
  // Siehe Tabs.List unten: Nodes/Aggregate/Cluster-Peer/MetroCluster nur
  // anzeigen, wenn mindestens ein registriertes System ein ganzer Cluster
  // ist (nicht nur einzelne SVMs).
  const hasClusterTypeSystem = (clusters ?? []).some((c) => c.system_type !== "svm");
  const { data: netappPolicies } = useSnapmirrorPolicies();
  const { data: netappSchedules } = useNetAppSchedules();
  // Globaler Sicherheits-Schalter (Settings > Storage) -- siehe
  // require_storage_unlocked in netapp_clusters.py fuer die serverseitige
  // Durchsetzung, hier nur das Ausgrauen der Buttons. actions_enabled
  // fehlt (undefined) waehrend des ersten Ladens -- bewusst NICHT gesperrt
  // in diesem kurzen Zwischenzustand, erst eine explizite false-Antwort
  // sperrt.
  const { data: storageAccess } = useStorageAccess();
  const updateStorageAccess = useUpdateStorageAccess();
  const locked = storageAccess?.actions_enabled === false;
  const [policyFormOpen, setPolicyFormOpen] = useState(false);
  const [policyEditOpen, setPolicyEditOpen] = useState(false);
  const [editingPolicy, setEditingPolicy] = useState<NetAppSnapMirrorPolicy | null>(null);
  const [netappScheduleFormOpen, setNetappScheduleFormOpen] = useState(false);
  const [extraVolCols, setExtraVolCols] = useState<string[]>([]);
  const [svmSearch, setSvmSearch] = useState("");
  const [volumeSearch, setVolumeSearch] = useState("");
  const [lunSearch, setLunSearch] = useState("");
  const [igroupSearch, setIgroupSearch] = useState("");
  const [clusterPeerSearch, setClusterPeerSearch] = useState("");
  const [svmPeerSearch, setSvmPeerSearch] = useState("");
  const [snapmirrorSearch, setSnapmirrorSearch] = useState("");
  const [platformSearch, setPlatformSearch] = useState("");
  const [aggregateSearch, setAggregateSearch] = useState("");
  // MetroCluster legt automatisch eine Metadaten-/Sync-SVM je Cluster an
  // (Namenskonvention '*-mc', Status nicht 'running' -- reine Konfigurations-
  // replikation ohne echte Nutzdaten). Bei aktiviertem Schalter (Storage >
  // MetroCluster) werden diese SVMs samt aller ihrer Objekte aus JEDER
  // Storage-Tabelle ausgeblendet -- rein clientseitiger Anzeige-Filter,
  // siehe StorageAccessConfig.hide_metrocluster_mirrors. SnapMirror-
  // Beziehungen tragen keine eigene svm_name-Spalte, ONTAP-Pfade haben aber
  // immer das Format 'svm:volume' -- SVM-Name daher aus dem Pfad-Praefix
  // extrahiert.
  // Erkennung selbst ist IMMER aktiv (unabhaengig vom Anzeige-Schalter
  // unten) -- Anlage-Dialoge (IgroupFormModal/LunFormModal/VolumeFormModal)
  // sollen diese SVMs grundsaetzlich nie zur Auswahl anbieten, das ergibt
  // nie Sinn (reine Konfigurationsreplikation, keine Nutzdaten-SVM).
  const mcMirrorSvmNames = new Set(
    (svms ?? [])
      .filter((s) => s.name.endsWith("-mc") && (s.state ?? "").toLowerCase() !== "running")
      .map((s) => s.name),
  );
  const selectableSvms = (svms ?? []).filter((s) => !mcMirrorSvmNames.has(s.name));
  // Anzeige-Filter fuer die Storage-Tabellen -- nur aktiv, wenn der Schalter
  // in Storage > MetroCluster eingeschaltet ist (siehe
  // StorageAccessConfig.hide_metrocluster_mirrors), im Gegensatz zu
  // mcMirrorSvmNames oben.
  const hiddenSvmNames = storageAccess?.hide_metrocluster_mirrors ? mcMirrorSvmNames : new Set<string>();
  const svmOfPath = (path?: string | null) => (path ? path.split(":")[0] : undefined);
  const visibleSvms = (svms ?? []).filter((s) => !hiddenSvmNames.has(s.name));
  const visibleVolumes = (volumes ?? []).filter((v) => !v.svm_name || !hiddenSvmNames.has(v.svm_name));
  const visibleLuns = (luns ?? []).filter((l) => !l.svm_name || !hiddenSvmNames.has(l.svm_name));
  const visibleIgroups = (igroups ?? []).filter((ig) => !ig.svm_name || !hiddenSvmNames.has(ig.svm_name));
  const visibleSvmPeers = (svmPeers ?? []).filter(
    (p) => !(p.svm_name && hiddenSvmNames.has(p.svm_name)) && !(p.peer_svm_name && hiddenSvmNames.has(p.peer_svm_name)),
  );
  const visibleRelationships = (relationships ?? []).filter((r) => {
    const src = svmOfPath(r.source_path);
    const dst = svmOfPath(r.destination_path);
    return !(src && hiddenSvmNames.has(src)) && !(dst && hiddenSvmNames.has(dst));
  });
  const visibleNetappPolicies = (netappPolicies ?? []).filter((p) => !p.svm_name || !hiddenSvmNames.has(p.svm_name));
  const visibleNetappSchedules = (netappSchedules ?? []).filter((s) => !s.svm_name || !hiddenSvmNames.has(s.svm_name));
  // Nutzer-Vorgabe: bei mehreren registrierten Systemen sollen alle
  // Storage-Tabellen zuerst nach System, erst innerhalb eines Systems nach
  // dem Objekt selbst sortiert sein -- sonst mischen sich Objekte
  // verschiedener Systeme in der Anzeige (z.B. Volumes von System A und B
  // durcheinander statt geblockt). Gilt fuer JEDE Tabelle im Storage-
  // Bereich, nicht nur einzelne.
  // Variadic: beliebig viele nachgeordnete Sortierschluessel, z.B. bei
  // Volumes/LUNs zusaetzlich nach SVM VOR dem Objektnamen selbst (Nutzer-
  // Praezisierung).
  const byClusterThen = <T extends { cluster_name: string }>(items: T[], ...keys: ((item: T) => string)[]): T[] =>
    [...items].sort((a, b) => {
      const cmp = a.cluster_name.localeCompare(b.cluster_name);
      if (cmp !== 0) return cmp;
      for (const key of keys) {
        const c = key(a).localeCompare(key(b));
        if (c !== 0) return c;
      }
      return 0;
    });
  const filteredSvms = byClusterThen(visibleSvms.filter((s) => matchesAllColumns(s, svmSearch)), (s) => s.name);
  const filteredVolumes = byClusterThen(
    visibleVolumes.filter((v) => matchesAllColumns(v, volumeSearch)),
    (v) => v.svm_name ?? "",
    (v) => v.name,
  );
  const filteredLuns = byClusterThen(
    visibleLuns.filter((l) => matchesAllColumns(l, lunSearch)),
    (l) => l.svm_name ?? "",
    (l) => l.name,
  );
  const filteredIgroups = byClusterThen(
    visibleIgroups.filter((ig) => matchesAllColumns(ig, igroupSearch)),
    (ig) => ig.svm_name ?? "",
    (ig) => ig.name,
  );
  const filteredClusterPeers = byClusterThen(
    (clusterPeers ?? []).filter((p) => matchesAllColumns(p, clusterPeerSearch)),
    (p) => p.name ?? "",
  );
  const filteredSvmPeers = byClusterThen(visibleSvmPeers.filter((p) => matchesAllColumns(p, svmPeerSearch)), (p) => p.svm_name ?? "");
  const filteredRelationships = byClusterThen(
    visibleRelationships.filter((r) => matchesAllColumns(r, snapmirrorSearch)),
    (r) => r.source_path ?? "",
  );
  const filteredPlatforms = byClusterThen((platforms ?? []).filter((p) => matchesAllColumns(p, platformSearch)), (p) => p.node_name);
  const filteredAggregates = byClusterThen((aggregates ?? []).filter((a) => matchesAllColumns(a, aggregateSearch)), (a) => a.name);
  const sortedNetappPolicies = byClusterThen(visibleNetappPolicies, (p) => p.svm_name ?? "", (p) => p.name);
  const sortedNetappSchedules = byClusterThen(visibleNetappSchedules, (s) => s.svm_name ?? "", (s) => s.name);
  const [peerDetail, setPeerDetail] = useState<NetAppClusterPeer | null>(null);
  const [igroupFormOpen, setIgroupFormOpen] = useState(false);
  const [lunFormOpen, setLunFormOpen] = useState(false);
  const [volumeFormOpen, setVolumeFormOpen] = useState(false);
  const [clusterPeerFormOpen, setClusterPeerFormOpen] = useState(false);
  const [svmPeerFormOpen, setSvmPeerFormOpen] = useState(false);
  const [process, setProcess] = useState<ProcessPlan | null>(null);
  const [selectedLun, setSelectedLun] = useState<NetAppLun | null>(null);
  const [selectedVolume, setSelectedVolume] = useState<NetAppVolume | null>(null);
  const [selectedRelationship, setSelectedRelationship] = useState<SnapMirrorRelationship | null>(null);
  const [lunEditOpen, setLunEditOpen] = useState(false);
  const [volumeEditOpen, setVolumeEditOpen] = useState(false);
  const [snapmirrorEditOpen, setSnapmirrorEditOpen] = useState(false);
  const [snapmirrorFormOpen, setSnapmirrorFormOpen] = useState(false);

  // Direkteinstieg aus der Alarme-Seite ('Volume vergrößern'/'LUN
  // vergrößern' bei einer Kapazitäts-Warnung, siehe AlertsPage.tsx) --
  // ?editUuid=<uuid> zusammen mit ?tab=volumes/luns öffnet das jeweilige
  // Bearbeiten-Modal direkt, ohne dass das Objekt erst manuell in der
  // Tabelle gesucht werden muss. Der Parameter wird danach aus der URL
  // entfernt, damit ein Schließen+Neuladen das Modal nicht erneut öffnet.
  useEffect(() => {
    const editUuid = params.get("editUuid");
    if (!editUuid) return;
    if (activeTab === "volumes" && volumes) {
      const vol = volumes.find((v) => v.uuid === editUuid);
      if (vol) {
        setSelectedVolume(vol);
        setVolumeEditOpen(true);
      }
      const next = new URLSearchParams(params);
      next.delete("editUuid");
      setParams(next, { replace: true });
    } else if (activeTab === "luns" && luns) {
      const lun = luns.find((l) => l.uuid === editUuid);
      if (lun) {
        setSelectedLun(lun);
        setLunEditOpen(true);
      }
      const next = new URLSearchParams(params);
      next.delete("editUuid");
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, volumes, luns]);
  const [snapmirrorInitialSource, setSnapmirrorInitialSource] = useState<{
    clusterId: string;
    svmName: string;
    volumeName: string;
    sizeBytes: number;
  } | null>(null);

  const nodeStats = useMemo(() => {
    const uptimes = (platforms ?? []).map((p) => p.uptime_seconds).filter((u): u is number => u != null);
    const avgUptimeDays = uptimes.length ? uptimes.reduce((sum, u) => sum + u, 0) / uptimes.length / 86400 : null;
    return {
      models: groupCount(platforms, (p) => p.model),
      versions: groupCount(platforms, (p) => p.ontap_version),
      avgUptimeDays,
    };
  }, [platforms]);

  const aggregateStats = useMemo(() => {
    const list = aggregates ?? [];
    const totalSize = list.reduce((sum, a) => sum + (a.size_bytes ?? 0), 0);
    const totalUsed = list.reduce((sum, a) => sum + (a.used_bytes ?? 0), 0);
    const ratios = list.map((a) => a.efficiency_ratio_wo_snapshots_flexclones).filter((r): r is number => r != null);
    const avgEfficiency = ratios.length ? ratios.reduce((sum, r) => sum + r, 0) / ratios.length : null;
    return { totalSize, totalUsed, avgEfficiency };
  }, [aggregates]);

  const volumeStats = useMemo(() => {
    const list = volumes ?? [];
    return {
      totalSize: list.reduce((sum, v) => sum + (v.size_bytes ?? 0), 0),
      totalUsed: list.reduce((sum, v) => sum + (v.used_bytes ?? 0), 0),
      securityStyles: groupCount(volumes, (v) => v.security_style),
    };
  }, [volumes]);

  const lunStats = useMemo(() => {
    const list = luns ?? [];
    return {
      totalSize: list.reduce((sum, l) => sum + (l.size_bytes ?? 0), 0),
      osTypes: groupCount(luns, (l) => l.os_type),
    };
  }, [luns]);

  const snapmirrorStats = useMemo(
    () => ({
      states: groupCount(relationships, (r) => r.state),
      healthy: groupCount(relationships, (r) => (r.healthy ? "OK" : "Fehler")),
    }),
    [relationships],
  );


  const igroupStats = useMemo(
    () => ({ osTypes: groupCount(igroups, (ig) => ig.os_type), protocols: groupCount(igroups, (ig) => ig.protocol) }),
    [igroups],
  );

  function triggerUpdate(rel: SnapMirrorRelationship) {
    notifications.show({
      title: "SnapMirror-Update ausgeloest",
      message: `${rel.source_path} -> ${rel.destination_path}`,
      color: "blue",
    });
  }

  function handleDeleteVolume(vol: NetAppVolume) {
    confirmAction({
      title: "Volume löschen",
      message: `Volume '${vol.name}' wirklich löschen? Dies kann nicht rückgängig gemacht werden.`,
      confirmLabel: "Löschen",
      onConfirm: () => setProcess({ title: "Volume löschen", steps: buildVolumeDeleteSteps(vol.cluster_id, vol.uuid ?? "") }),
    });
  }

  function handleDeleteLun(lun: NetAppLun) {
    confirmAction({
      title: "LUN löschen",
      message: `LUN '${lun.name}' wirklich löschen? Dies kann nicht rückgängig gemacht werden.`,
      confirmLabel: "Löschen",
      onConfirm: () => setProcess({ title: "LUN löschen", steps: buildLunDeleteSteps(lun.cluster_id, lun.uuid ?? "") }),
    });
  }

  function openSnapmirrorForVolume(vol: NetAppVolume) {
    setSnapmirrorInitialSource({
      clusterId: vol.cluster_id,
      svmName: vol.svm_name ?? "",
      volumeName: vol.name,
      sizeBytes: vol.size_bytes ?? 1073741824,
    });
    setSnapmirrorFormOpen(true);
  }

  return (
    <Stack>
      <Title order={3}>Storage</Title>

      {locked && (
        <Alert color="orange" icon={<IconAlertTriangle size={18} />} title="Storage-Aktionen gesperrt">
          Ändernde Aktionen sind aktuell global deaktiviert (Settings &gt; Storage) -- Ansicht bleibt möglich, alle
          Buttons zum Anlegen/Ändern/Löschen sind ausgegraut. Ausnahme: ein NetApp-System hinzuzufügen oder seine
          Verbindungsdaten zu bearbeiten (z.B. für eine Passwort-Rotation) bleibt weiterhin möglich.
        </Alert>
      )}

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "clusters" })}>
        <Tabs.List>
          <Tabs.Tab value="clusters">Systeme</Tabs.Tab>
          {/* Nodes/Aggregate/Cluster Peer/MetroCluster sind reine
              Cluster-Konzepte -- fuer ein SVM-System nicht abfragbar (siehe
              NetAppOntapService.run_discovery), Zeilen bleiben dafuer dort
              einfach leer. Nur wenn AUSSCHLIESSLICH SVM-Systeme registriert
              sind, waeren diese Reiter dauerhaft leer -- dann kosmetisch
              ganz ausgeblendet statt verwirrend leer stehenzulassen. */}
          {hasClusterTypeSystem && (
            <>
              <Tabs.Tab value="platforms">Nodes</Tabs.Tab>
              <Tabs.Tab value="aggregates">Aggregate</Tabs.Tab>
            </>
          )}
          <Tabs.Tab value="svms">Storage Virtual Machines</Tabs.Tab>
          <Tabs.Tab value="volumes">Volumes</Tabs.Tab>
          <Tabs.Tab value="luns">LUNs</Tabs.Tab>
          <Tabs.Tab value="igroups">IGroups</Tabs.Tab>
          {hasClusterTypeSystem && <Tabs.Tab value="cluster-peers">Cluster Peer</Tabs.Tab>}
          <Tabs.Tab value="svm-peers">SVM Peer</Tabs.Tab>
          <Tabs.Tab value="snapmirror">SnapMirror-Beziehungen</Tabs.Tab>
          <Tabs.Tab value="snapmirror-policies">SnapMirror-Policies</Tabs.Tab>
          <Tabs.Tab value="schedules">Schedules</Tabs.Tab>
          {hasClusterTypeSystem && <Tabs.Tab value="metrocluster">MetroCluster</Tabs.Tab>}
        </Tabs.List>

        <Tabs.Panel value="clusters" pt="md">
          <ClusterTab locked={locked} />
        </Tabs.Panel>

        <Tabs.Panel value="svms" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">Storage Virtual Machines</Title>
          <StatRibbon>
            <StatCard label="Anzahl SVMs" value={visibleSvms.length} />
          </StatRibbon>
          <Group justify="flex-start" mb="xs">
            <SearchInput value={svmSearch} onChange={setSvmSearch} />
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Subtype</Table.Th>
                <Table.Th>Allowed Protocols</Table.Th>
                <Table.Th>Data-Services</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredSvms.map((svm) => (
                <Table.Tr key={svm.id}>
                  <Table.Td>{svm.cluster_name}</Table.Td>
                  <Table.Td>{svm.name}</Table.Td>
                  <Table.Td>
                    <Badge color={svm.state === "running" ? "green" : "gray"} variant="light">
                      {svm.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{svm.subtype ?? "-"}</Table.Td>
                  <Table.Td>{svm.allowed_protocols ?? "-"}</Table.Td>
                  <Table.Td>{svm.data_services ?? "-"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {svms?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine SVMs erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleSvms.length > 0 && filteredSvms.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Keine SVM passt zur Suche „{svmSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="volumes" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">Volumes</Title>
          <StatRibbon>
            <StatCard label="Anzahl Volumes" value={visibleVolumes.length} />
            <CapacityBarCard label="Kapazität" used={volumeStats.totalUsed} total={volumeStats.totalSize} formatValue={formatBytes} />
            <DistributionCard label="Security Style" items={volumeStats.securityStyles} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={volumeSearch} onChange={setVolumeSearch} />
            <Button leftSection={<IconPlus size={16} />} disabled={locked} onClick={() => setVolumeFormOpen(true)}>
              Volume anlegen
            </Button>
          </Group>
          <Group justify="flex-end" mb="xs">
            <MultiSelect
              placeholder="Weitere Attribute anzeigen..."
              data={[
                { value: "autodelete", label: "Snapshot Autodelete" },
                { value: "autogrow", label: "Autogrow" },
                { value: "snapshot_policy", label: "Snapshot Policy" },
                { value: "encryption", label: "Verschlüsselung" },
              ]}
              value={extraVolCols}
              onChange={setExtraVolCols}
              clearable
              w={360}
            />
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Security Style</Table.Th>
                <Table.Th>Language</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>SnapMirror</Table.Th>
                {extraVolCols.includes("autodelete") && <Table.Th>Snapshot Autodelete</Table.Th>}
                {extraVolCols.includes("autogrow") && <Table.Th>Autogrow</Table.Th>}
                {extraVolCols.includes("snapshot_policy") && <Table.Th>Snapshot Policy</Table.Th>}
                {extraVolCols.includes("encryption") && <Table.Th>Verschlüsselung</Table.Th>}
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredVolumes.map((vol) => (
                <Table.Tr key={vol.id}>
                  <Table.Td>{vol.cluster_name}</Table.Td>
                  <Table.Td>{vol.svm_name ?? "-"}</Table.Td>
                  <Table.Td>{vol.name}</Table.Td>
                  <Table.Td>
                    <Badge color={vol.state === "online" ? "green" : "gray"} variant="light">
                      {vol.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{vol.security_style ?? "-"}</Table.Td>
                  <Table.Td>{vol.language ?? "-"}</Table.Td>
                  <Table.Td>{formatBytes(vol.size_bytes)}</Table.Td>
                  <Table.Td miw={140}>
                    <Text size="xs" c="dimmed">
                      {formatBytes(vol.used_bytes)} {vol.percent_used != null ? `(${vol.percent_used}%)` : ""}
                    </Text>
                    {vol.percent_used != null && (
                      <Progress
                        value={vol.percent_used}
                        size={6}
                        mt={2}
                        color={vol.percent_used >= 90 ? "red" : vol.percent_used >= 75 ? "yellow" : "blue"}
                      />
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label={vol.snapmirror_protected ? "Per SnapMirror gesichert" : "Nicht per SnapMirror gesichert"}>
                      {vol.snapmirror_protected ? (
                        <IconShieldCheck size={20} color="var(--mantine-color-green-6)" />
                      ) : (
                        <IconShieldOff size={20} color="var(--mantine-color-gray-5)" />
                      )}
                    </Tooltip>
                  </Table.Td>
                  {extraVolCols.includes("autodelete") && (
                    <Table.Td>
                      {vol.snapshot_autodelete_enabled == null ? (
                        "-"
                      ) : (
                        <Badge color={vol.snapshot_autodelete_enabled ? "green" : "gray"} variant="light">
                          {vol.snapshot_autodelete_enabled ? "Aktiv" : "Inaktiv"}
                        </Badge>
                      )}
                    </Table.Td>
                  )}
                  {extraVolCols.includes("autogrow") && (
                    <Table.Td>{vol.autosize_mode && vol.autosize_mode !== "off" ? vol.autosize_mode : "Aus"}</Table.Td>
                  )}
                  {extraVolCols.includes("snapshot_policy") && <Table.Td>{vol.snapshot_policy_name ?? "-"}</Table.Td>}
                  {extraVolCols.includes("encryption") && (
                    <Table.Td>
                      {vol.encryption_enabled == null ? (
                        "-"
                      ) : (
                        <Badge color={vol.encryption_enabled ? "green" : "gray"} variant="light">
                          {vol.encryption_enabled ? "Aktiv" : "Inaktiv"}
                        </Badge>
                      )}
                    </Table.Td>
                  )}
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Tooltip label="Bearbeiten">
                        <ActionIcon
                          variant="light"
                          disabled={locked}
                          onClick={() => {
                            setSelectedVolume(vol);
                            setVolumeEditOpen(true);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="SnapMirror-Replikation erstellen">
                        <ActionIcon variant="light" disabled={locked} onClick={() => openSnapmirrorForVolume(vol)}>
                          <IconLink size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Löschen">
                        <ActionIcon variant="light" color="red" disabled={locked} onClick={() => handleDeleteVolume(vol)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {volumes?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Volumes erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleVolumes.length > 0 && filteredVolumes.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein Volume passt zur Suche „{volumeSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="luns" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">LUNs</Title>
          <StatRibbon>
            <StatCard label="Anzahl LUNs" value={visibleLuns.length} />
            <StatCard label="Provisioniert" value={formatBytes(lunStats.totalSize)} />
            <DistributionCard label="OS-Type" items={lunStats.osTypes} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={lunSearch} onChange={setLunSearch} />
            <Button leftSection={<IconPlus size={16} />} disabled={locked} onClick={() => setLunFormOpen(true)}>
              LUN anlegen
            </Button>
          </Group>
          <Table striped highlightOnHover horizontalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>OS-Type</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>IGroups</Table.Th>
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredLuns.map((lun) => (
                <Table.Tr key={lun.id}>
                  <Table.Td>{lun.cluster_name}</Table.Td>
                  <Table.Td>{lun.svm_name ?? "-"}</Table.Td>
                  <Table.Td>
                    <Tooltip label={lun.volume_name ?? ""} openDelay={300} disabled={!lun.volume_name}>
                      <Text size="sm" truncate maw={160} style={{ cursor: "default" }}>
                        {lun.volume_name ?? "-"}
                      </Text>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    {/* Voller Pfad enthaelt den Volume-Namen bereits als Praefix
                        (/vol/{volume}/{name}.lun, bis zu ~100 Zeichen lang, live
                        gemessen) -- redundant zur Volume-Spalte daneben und war der
                        Haupttreiber fuer den horizontalen Tabellen-Overflow. Nur der
                        Dateiname wird angezeigt, voller Pfad per Tooltip verfuegbar. */}
                    <Tooltip label={lun.name} openDelay={300}>
                      <Text size="sm" truncate maw={200} style={{ cursor: "default" }}>
                        {lun.name.split("/").pop()}
                      </Text>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>{lun.os_type ?? "-"}</Table.Td>
                  <Table.Td>{formatBytes(lun.size_bytes)}</Table.Td>
                  <Table.Td miw={140}>
                    <Text size="xs" c="dimmed">
                      {formatBytes(lun.used_bytes)} {lun.percent_used != null ? `(${lun.percent_used}%)` : ""}
                    </Text>
                    {lun.percent_used != null && (
                      <Progress
                        value={lun.percent_used}
                        size={6}
                        mt={2}
                        color={lun.percent_used >= 90 ? "red" : lun.percent_used >= 75 ? "yellow" : "blue"}
                      />
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge color={lun.state === "online" ? "green" : "gray"} variant="light">
                      {lun.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label={lun.mapped_igroups ?? ""} openDelay={300} disabled={!lun.mapped_igroups}>
                      <Text size="sm" truncate maw={180} style={{ cursor: "default" }}>
                        {lun.mapped_igroups ?? "-"}
                      </Text>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Tooltip label="Bearbeiten">
                        <ActionIcon
                          variant="light"
                          disabled={locked}
                          onClick={() => {
                            setSelectedLun(lun);
                            setLunEditOpen(true);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Löschen">
                        <ActionIcon variant="light" color="red" disabled={locked} onClick={() => handleDeleteLun(lun)}>
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {luns?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine LUNs erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleLuns.length > 0 && filteredLuns.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Keine LUN passt zur Suche „{lunSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="igroups" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">IGroups</Title>
          <StatRibbon>
            <StatCard label="Anzahl IGroups" value={visibleIgroups.length} />
            <DistributionCard label="OS-Type" items={igroupStats.osTypes} />
            <DistributionCard label="Protocol" items={igroupStats.protocols} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={igroupSearch} onChange={setIgroupSearch} />
            <Button leftSection={<IconPlus size={16} />} disabled={locked} onClick={() => setIgroupFormOpen(true)}>
              IGroup anlegen
            </Button>
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>OS-Type</Table.Th>
                <Table.Th>Protocol</Table.Th>
                <Table.Th>Initiatoren</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredIgroups.map((ig) => (
                <Table.Tr key={ig.id}>
                  <Table.Td>{ig.cluster_name}</Table.Td>
                  <Table.Td>{ig.svm_name ?? "-"}</Table.Td>
                  <Table.Td>{ig.name}</Table.Td>
                  <Table.Td>{ig.os_type ?? "-"}</Table.Td>
                  <Table.Td>{ig.protocol ?? "-"}</Table.Td>
                  <Table.Td>{ig.initiator_count}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {igroups?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Initiator-Gruppen erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleIgroups.length > 0 && filteredIgroups.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Keine IGroup passt zur Suche „{igroupSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="cluster-peers" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">Cluster Peer</Title>
          <StatRibbon>
            <StatCard label="Anzahl Cluster Peer" value={clusterPeers?.length ?? 0} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={clusterPeerSearch} onChange={setClusterPeerSearch} />
            <Button leftSection={<IconLink size={16} />} disabled={locked} onClick={() => setClusterPeerFormOpen(true)}>
              Cluster Peer erstellen
            </Button>
          </Group>
          {peerDetail && <ClusterPeerDetailHeader peer={peerDetail} onClose={() => setPeerDetail(null)} />}
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Remote-Name</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredClusterPeers.map((peer) => (
                <Table.Tr
                  key={peer.id}
                  onClick={() => setPeerDetail(peer)}
                  style={{
                    cursor: "pointer",
                    backgroundColor: peerDetail?.id === peer.id ? "var(--mantine-color-blue-light)" : undefined,
                  }}
                >
                  <Table.Td>{peer.cluster_name}</Table.Td>
                  <Table.Td>{peer.name ?? "-"}</Table.Td>
                  <Table.Td>{peer.remote_name ?? "-"}</Table.Td>
                  <Table.Td>
                    <Badge color={peer.state === "available" ? "green" : "gray"} variant="light">
                      {peer.state ?? "-"}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {clusterPeers?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Cluster-Peer-Beziehungen erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {(clusterPeers?.length ?? 0) > 0 && filteredClusterPeers.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein Cluster Peer passt zur Suche „{clusterPeerSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="svm-peers" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">SVM Peer</Title>
          <StatRibbon>
            <StatCard label="Anzahl SVM Peer" value={visibleSvmPeers.length} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={svmPeerSearch} onChange={setSvmPeerSearch} />
            <Button leftSection={<IconLink size={16} />} disabled={locked} onClick={() => setSvmPeerFormOpen(true)}>
              SVM Peer erstellen
            </Button>
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Peer-SVM</Table.Th>
                <Table.Th>Peer-Cluster</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Applications</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredSvmPeers.map((peer) => (
                <Table.Tr key={peer.id}>
                  <Table.Td>{peer.cluster_name}</Table.Td>
                  <Table.Td>{peer.svm_name ?? "-"}</Table.Td>
                  <Table.Td>{peer.peer_svm_name ?? "-"}</Table.Td>
                  <Table.Td>{peer.peer_cluster_name ?? "-"}</Table.Td>
                  <Table.Td>
                    <Badge color={peer.state === "peered" ? "green" : "gray"} variant="light">
                      {peer.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{peer.applications ?? "-"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {svmPeers?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine SVM-Peer-Beziehungen erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleSvmPeers.length > 0 && filteredSvmPeers.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein SVM Peer passt zur Suche „{svmPeerSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="snapmirror" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">SnapMirror-Beziehungen</Title>
          <StatRibbon>
            <StatCard label="Anzahl Beziehungen" value={visibleRelationships.length} />
            <DistributionCard label="Status" items={snapmirrorStats.states} />
            <DistributionCard
              label="Healthy"
              items={snapmirrorStats.healthy.map((d) => ({ ...d, color: d.key === "OK" ? "green" : "red" }))}
            />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <SearchInput value={snapmirrorSearch} onChange={setSnapmirrorSearch} />
            <Button
              leftSection={<IconLink size={16} />}
              disabled={locked}
              onClick={() => {
                setSnapmirrorInitialSource(null);
                setSnapmirrorFormOpen(true);
              }}
            >
              Neue SnapMirror-Beziehung
            </Button>
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>Quelle</Table.Th>
                <Table.Th>Ziel</Table.Th>
                <Table.Th>Ziel-Cluster</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Healthy</Table.Th>
                <Table.Th>Lag Time</Table.Th>
                <Table.Th>Last Transfer Size</Table.Th>
                <Table.Th>Last Transfer Error</Table.Th>
                <Table.Th>Schedule</Table.Th>
                <Table.Th>Policy</Table.Th>
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredRelationships.map((rel) => (
                <Table.Tr key={rel.id}>
                  <Table.Td>{rel.cluster_name}</Table.Td>
                  <Table.Td>{rel.source_path}</Table.Td>
                  <Table.Td>{rel.destination_path}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Text size="sm">{rel.destination_cluster_name ?? "-"}</Text>
                      {rel.destination_cluster_name && !clusters?.some((c) => c.ontap_cluster_name === rel.destination_cluster_name) && (
                        <Tooltip label="Nicht in dieser App registriert -- Policy/Schedule hier nicht editierbar">
                          <Badge size="xs" color="gray" variant="light">
                            extern
                          </Badge>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>{rel.state}</Table.Td>
                  <Table.Td>
                    <Tooltip label={rel.last_transfer_error ?? ""} disabled={!rel.last_transfer_error}>
                      <Badge color={rel.healthy ? "green" : "red"} variant="light">
                        {rel.healthy ? "OK" : "Fehler"}
                      </Badge>
                    </Tooltip>
                  </Table.Td>
                  <Table.Td>{formatLagTime(rel.lag_time)}</Table.Td>
                  <Table.Td>{formatBytes(rel.last_transfer_size_bytes)}</Table.Td>
                  <Table.Td maw={220}>
                    {rel.last_transfer_error ? (
                      <Text size="xs" c="red" lineClamp={2}>
                        {rel.last_transfer_error}
                      </Text>
                    ) : (
                      "-"
                    )}
                  </Table.Td>
                  <Table.Td>{rel.schedule_name ?? "-"}</Table.Td>
                  <Table.Td>{rel.policy_name ?? "-"}</Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      <Tooltip label="Bearbeiten">
                        <ActionIcon
                          variant="light"
                          disabled={locked}
                          onClick={() => {
                            setSelectedRelationship(rel);
                            setSnapmirrorEditOpen(true);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="SnapMirror-Update erzwingen">
                        <ActionIcon variant="light" disabled={locked} onClick={() => triggerUpdate(rel)}>
                          <IconRefresh size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {relationships?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine SnapMirror-Beziehungen erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {visibleRelationships.length > 0 && filteredRelationships.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Keine Beziehung passt zur Suche „{snapmirrorSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="platforms" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">Nodes</Title>
          <StatRibbon>
            <StatCard label="Anzahl Nodes" value={platforms?.length ?? 0} />
            <DistributionCard label="Modelle" items={nodeStats.models} />
            <DistributionCard label="ONTAP-Versionen" items={nodeStats.versions} />
            <StatCard label="Ø Uptime" value={nodeStats.avgUptimeDays != null ? `${Math.round(nodeStats.avgUptimeDays)} Tage` : "-"} />
          </StatRibbon>
          <Group justify="flex-start" mb="xs">
            <SearchInput value={platformSearch} onChange={setPlatformSearch} />
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>Node</Table.Th>
                <Table.Th>Modell</Table.Th>
                <Table.Th>Seriennummer</Table.Th>
                <Table.Th>ONTAP-Version</Table.Th>
                <Table.Th>Uptime</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredPlatforms.map((p) => (
                <Table.Tr key={p.id}>
                  <Table.Td>{p.cluster_name}</Table.Td>
                  <Table.Td>{p.node_name}</Table.Td>
                  <Table.Td>{p.model ?? "-"}</Table.Td>
                  <Table.Td>{p.serial_number ?? "-"}</Table.Td>
                  <Table.Td>{p.ontap_version ?? "-"}</Table.Td>
                  <Table.Td>{p.uptime_seconds ? `${Math.floor(p.uptime_seconds / 86400)} Tage` : "-"}</Table.Td>
                  <Table.Td>
                    <Badge color={p.state === "up" ? "green" : "gray"} variant="light">
                      {p.state ?? "-"}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {platforms?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Plattform-Informationen erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {(platforms?.length ?? 0) > 0 && filteredPlatforms.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein Node passt zur Suche „{platformSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="aggregates" pt="md">
          <Paper p="md">
          <Title order={5} mb="sm">Aggregate</Title>
          <StatRibbon>
            <StatCard label="Anzahl Aggregate" value={aggregates?.length ?? 0} />
            <CapacityBarCard label="Kapazität" used={aggregateStats.totalUsed} total={aggregateStats.totalSize} formatValue={formatBytes} />
            <StatCard
              label="Storage Efficiency (ohne Snapshots/FlexClones)"
              value={aggregateStats.avgEfficiency != null ? `${aggregateStats.avgEfficiency.toFixed(2)} : 1` : "-"}
            />
          </StatRibbon>
          <Group justify="flex-start" mb="xs">
            <SearchInput value={aggregateSearch} onChange={setAggregateSearch} />
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>System</Table.Th>
                <Table.Th>Node</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegt</Table.Th>
                <Table.Th>Storage Efficiency (ohne Snapshots/FlexClones)</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredAggregates.map((agg) => (
                <Table.Tr key={agg.id}>
                  <Table.Td>{agg.cluster_name}</Table.Td>
                  <Table.Td>{agg.node_name ?? "-"}</Table.Td>
                  <Table.Td>{agg.name}</Table.Td>
                  <Table.Td>
                    <Badge color={agg.state === "online" ? "green" : "gray"} variant="light">
                      {agg.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{formatBytes(agg.size_bytes)}</Table.Td>
                  <Table.Td miw={160}>
                    <Text size="xs" c="dimmed">
                      {formatBytes(agg.used_bytes)} {agg.used_percent != null ? `(${agg.used_percent}%)` : ""}
                    </Text>
                    {agg.used_percent != null && (
                      <Progress
                        value={agg.used_percent}
                        size={6}
                        mt={2}
                        color={agg.used_percent >= 90 ? "red" : agg.used_percent >= 75 ? "yellow" : "blue"}
                      />
                    )}
                  </Table.Td>
                  <Table.Td>
                    {agg.efficiency_ratio_wo_snapshots_flexclones != null
                      ? `${agg.efficiency_ratio_wo_snapshots_flexclones.toFixed(2)} : 1`
                      : "-"}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {aggregates?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Aggregate erkannt. Führe eine Discovery unter System aus.
            </Text>
          )}
          {(aggregates?.length ?? 0) > 0 && filteredAggregates.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein Aggregate passt zur Suche „{aggregateSearch}".
            </Text>
          )}
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="snapmirror-policies" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>SnapMirror-Policies</Title>
              <Button leftSection={<IconPlus size={16} />} disabled={locked} onClick={() => setPolicyFormOpen(true)}>
                Policy anlegen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>System</Table.Th>
                  <Table.Th>SVM</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Typ</Table.Th>
                  <Table.Th>Regeln</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sortedNetappPolicies.map((p) => (
                  <Table.Tr key={p.id}>
                    <Table.Td>{p.cluster_name}</Table.Td>
                    <Table.Td>{p.svm_name ?? "-"}</Table.Td>
                    <Table.Td>{p.name}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color="blue">
                        {p.display_type ? (SNAPMIRROR_POLICY_TYPE_LABEL[p.display_type] ?? p.display_type) : (p.type ?? "-")}
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      {p.rules.length ? p.rules.map((r) => `${r.label}: ${r.count}`).join(", ") : "-"}
                    </Table.Td>
                    <Table.Td>
                      <Tooltip label="Bearbeiten">
                        <ActionIcon
                          variant="light"
                          disabled={locked}
                          onClick={() => {
                            setEditingPolicy(p);
                            setPolicyEditOpen(true);
                          }}
                        >
                          <IconEdit size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {sortedNetappPolicies.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine SnapMirror-Policies erkannt. Führe eine Discovery unter System aus.
              </Text>
            )}
          </Paper>

          <SnapMirrorPolicyFormModal
            opened={policyFormOpen}
            onClose={() => setPolicyFormOpen(false)}
            clusters={clusters}
            svms={svms}
            onSubmitPlan={(plan) => {
              setPolicyFormOpen(false);
              setProcess({ title: "SnapMirror-Policy anlegen", steps: buildPolicyCreationSteps(plan) });
            }}
          />
          <SnapMirrorPolicyEditModal
            opened={policyEditOpen}
            onClose={() => setPolicyEditOpen(false)}
            policy={editingPolicy}
            onSubmitPlan={(plan) => {
              setPolicyEditOpen(false);
              setProcess({ title: "SnapMirror-Policy bearbeiten", steps: buildPolicyEditSteps(plan) });
            }}
          />
        </Tabs.Panel>

        <Tabs.Panel value="schedules" pt="md">
          <Paper p="md">
            <Group justify="space-between" mb="sm">
              <Title order={5}>Schedules</Title>
              <Button leftSection={<IconPlus size={16} />} disabled={locked} onClick={() => setNetappScheduleFormOpen(true)}>
                Schedule anlegen
              </Button>
            </Group>
            <Table striped highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>System</Table.Th>
                  <Table.Th>SVM</Table.Th>
                  <Table.Th>Name</Table.Th>
                  <Table.Th>Minuten</Table.Th>
                  <Table.Th>Stunden</Table.Th>
                  <Table.Th>Wochentage</Table.Th>
                  <Table.Th>Tage</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {sortedNetappSchedules.map((s) => (
                  <Table.Tr key={s.id}>
                    <Table.Td>{s.cluster_name}</Table.Td>
                    <Table.Td>{s.svm_name ?? "cluster-weit"}</Table.Td>
                    <Table.Td>{s.name}</Table.Td>
                    <Table.Td>{s.minutes.join(", ") || "-"}</Table.Td>
                    <Table.Td>{s.hours.join(", ") || "jede"}</Table.Td>
                    <Table.Td>{s.weekdays.join(", ") || "jeder"}</Table.Td>
                    <Table.Td>{s.days.join(", ") || "jeder"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
            {sortedNetappSchedules.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Noch keine Schedules erkannt. Führe eine Discovery unter System aus.
              </Text>
            )}
          </Paper>

          <NetAppScheduleFormModal
            opened={netappScheduleFormOpen}
            onClose={() => setNetappScheduleFormOpen(false)}
            clusters={clusters}
            svms={svms}
            onSubmitPlan={(plan) => {
              setNetappScheduleFormOpen(false);
              setProcess({ title: "Schedule anlegen", steps: buildScheduleCreationSteps(plan) });
            }}
          />
        </Tabs.Panel>

        <Tabs.Panel value="metrocluster" pt="md">
          <Paper p="md">
            <Title order={5} mb="sm">MetroCluster</Title>
            <Stack gap="xs" maw={480}>
              <Group justify="space-between">
                <Text c="dimmed">Konfiguriert</Text>
                <Text fw={600}>{mcc?.configured ? "Ja" : "Nein"}</Text>
              </Group>
              <Group justify="space-between">
                <Text c="dimmed">Modus</Text>
                <Text fw={600}>{mcc?.mode ?? "-"}</Text>
              </Group>
              <Group justify="space-between">
                <Text c="dimmed">Switchover aktiv</Text>
                <Badge color={mcc?.switchover_in_progress ? "orange" : "green"}>
                  {mcc?.switchover_in_progress ? "Ja" : "Nein"}
                </Badge>
              </Group>
            </Stack>
            <Switch
              mt="lg"
              label="MetroCluster-Spiegelobjekte (*-mc) in der gesamten Storage-Ansicht ausblenden"
              description="Blendet die von ONTAP automatisch angelegte(n) Metadaten-SVM(s) (Name endet auf '-mc', Status nicht 'running') samt aller ihrer Volumes/LUNs/IGroups/Policies/Schedules/SnapMirror-Beziehungen aus -- reine Konfigurationsreplikation ohne echte Nutzdaten."
              checked={storageAccess?.hide_metrocluster_mirrors ?? false}
              onChange={(e) =>
                updateStorageAccess.mutate({
                  actions_enabled: storageAccess?.actions_enabled ?? true,
                  hide_metrocluster_mirrors: e.currentTarget.checked,
                })
              }
              disabled={updateStorageAccess.isPending}
            />
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <IgroupFormModal opened={igroupFormOpen} onClose={() => setIgroupFormOpen(false)} clusters={clusters} svms={selectableSvms} />
      <LunFormModal
        opened={lunFormOpen}
        onClose={() => setLunFormOpen(false)}
        clusters={clusters}
        svms={selectableSvms}
        volumes={volumes}
        aggregates={aggregates}
        igroups={igroups}
        onSubmitPlan={(plan) => {
          setLunFormOpen(false);
          setProcess({ title: "LUN anlegen", steps: buildLunCreationSteps(plan) });
        }}
      />
      <VolumeFormModal
        opened={volumeFormOpen}
        onClose={() => setVolumeFormOpen(false)}
        clusters={clusters}
        svms={selectableSvms}
        aggregates={aggregates}
        onSubmitPlan={(plan) => {
          setVolumeFormOpen(false);
          setProcess({ title: "Volume anlegen", steps: buildVolumeCreationSteps(plan) });
        }}
      />
      <LunEditModal
        opened={lunEditOpen}
        onClose={() => setLunEditOpen(false)}
        lun={selectedLun}
        igroups={igroups}
        onSubmitPlan={(plan) => {
          setLunEditOpen(false);
          setProcess({ title: "LUN bearbeiten", steps: buildLunEditSteps(plan) });
        }}
      />
      <VolumeEditModal
        opened={volumeEditOpen}
        onClose={() => setVolumeEditOpen(false)}
        volume={selectedVolume}
        onSubmitPlan={(plan) => {
          setVolumeEditOpen(false);
          setProcess({ title: "Volume bearbeiten", steps: buildVolumeEditSteps(plan) });
        }}
      />
      <SnapmirrorEditModal
        opened={snapmirrorEditOpen}
        onClose={() => setSnapmirrorEditOpen(false)}
        relationship={selectedRelationship}
        clusters={clusters}
        onSubmitPlan={(plan) => {
          setSnapmirrorEditOpen(false);
          setProcess({ title: "SnapMirror-Beziehung bearbeiten", steps: buildSnapmirrorEditSteps(plan) });
        }}
      />
      <SnapmirrorFormModal
        opened={snapmirrorFormOpen}
        onClose={() => setSnapmirrorFormOpen(false)}
        clusters={clusters}
        svms={svms}
        volumes={volumes}
        aggregates={aggregates}
        initialSource={snapmirrorInitialSource}
        onSubmitPlan={(plan) => {
          setSnapmirrorFormOpen(false);
          setProcess({ title: "SnapMirror-Beziehung erstellen", steps: buildSnapmirrorCreationSteps(plan) });
        }}
      />
      <ClusterPeerFormModal opened={clusterPeerFormOpen} onClose={() => setClusterPeerFormOpen(false)} clusters={clusters} />
      <SvmPeerFormModal opened={svmPeerFormOpen} onClose={() => setSvmPeerFormOpen(false)} clusters={clusters} svms={svms} />
      <ProcessModal opened={!!process} onClose={() => setProcess(null)} plan={process} />
    </Stack>
  );
}
