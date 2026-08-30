import { useMemo, useState } from "react";
import {
  Badge,
  Button,
  Group,
  Menu,
  Modal,
  MultiSelect,
  Paper,
  PasswordInput,
  Progress,
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
  IconCertificate,
  IconEdit,
  IconInfoCircle,
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
  usePlatforms,
  useSnapMirrorRelationships,
  useSvmPeers,
  useSvms,
  useVerifyNetAppCluster,
  useVolumes,
} from "@/api/hooks";
import { ClusterPeerFormModal } from "@/components/ClusterPeerFormModal";
import { ContextMenuDropdown, useContextMenu } from "@/components/ContextMenu";
import { DiscoveryModal } from "@/components/DiscoveryModal";
import { IgroupFormModal } from "@/components/IgroupFormModal";
import { LunEditModal } from "@/components/LunEditModal";
import { LunFormModal } from "@/components/LunFormModal";
import { ProcessModal } from "@/components/ProcessModal";
import type { ProcessPlan } from "@/components/ProcessModal";
import { SnapmirrorEditModal } from "@/components/SnapmirrorEditModal";
import { SnapmirrorFormModal } from "@/components/SnapmirrorFormModal";
import { CapacityBarCard, DistributionCard, StatCard, StatRibbon, groupCount } from "@/components/StatRibbon";
import { SvmPeerFormModal } from "@/components/SvmPeerFormModal";
import { VolumeEditModal } from "@/components/VolumeEditModal";
import { VolumeFormModal } from "@/components/VolumeFormModal";
import type { NetAppCluster, NetAppClusterPeer, NetAppLun, NetAppVolume, SnapMirrorRelationship } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatLagTime } from "@/utils/format";
import {
  buildLunCreationSteps,
  buildLunDeleteSteps,
  buildLunEditSteps,
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

function AddClusterModal({ opened, onClose, onCreated }: { opened: boolean; onClose: () => void; onCreated: (cluster: NetAppCluster) => void }) {
  const createCluster = useCreateNetAppCluster();
  const [name, setName] = useState("");
  const [mgmtLif, setMgmtLif] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [verifySsl, setVerifySsl] = useState(true);

  function reset() {
    setName("");
    setMgmtLif("");
    setUsername("");
    setPassword("");
    setVerifySsl(true);
  }

  function handleClose() {
    reset();
    onClose();
  }

  function handleSubmit() {
    createCluster.mutate(
      { name, management_lif: mgmtLif, username, password, verify_ssl: verifySsl },
      {
        onSuccess: (cluster) => {
          notifications.show({
            title: "Cluster hinzugefügt",
            message: `'${cluster.name}' verbunden (ONTAP ${cluster.ontap_version ?? "?"}).`,
            color: "green",
          });
          handleClose();
          onCreated(cluster);
        },
        onError: (err) => {
          notifications.show({
            title: "Verbindung fehlgeschlagen",
            message: apiErrorMessage(err, "Cluster konnte nicht hinzugefügt werden."),
            color: "red",
          });
        },
      },
    );
  }

  return (
    <Modal opened={opened} onClose={handleClose} title="NetApp-Cluster hinzufügen">
      <Stack>
        <TextInput label="Cluster-Name" placeholder="z.B. NETAPP-PROD" required value={name} onChange={(e) => setName(e.currentTarget.value)} />
        <TextInput
          label="Cluster-Management-IP"
          placeholder="z.B. 10.0.0.10"
          required
          value={mgmtLif}
          onChange={(e) => setMgmtLif(e.currentTarget.value)}
        />
        <TextInput label="Benutzername" required value={username} onChange={(e) => setUsername(e.currentTarget.value)} />
        <PasswordInput label="Kennwort" required value={password} onChange={(e) => setPassword(e.currentTarget.value)} />
        <Switch
          label="TLS-Zertifikat des Clusters validieren"
          checked={verifySsl}
          onChange={(e) => setVerifySsl(e.currentTarget.checked)}
        />
        <Text size="xs" c="dimmed">
          Beim Hinzufügen wird die Verbindung sofort getestet, anschließend startet automatisch eine Discovery.
          Zertifikatsbasierte Authentifizierung kann danach über das Kontextmenü aktiviert werden.
        </Text>
        <Group justify="flex-end" mt="sm">
          <Button variant="default" onClick={handleClose}>
            Abbrechen
          </Button>
          <Button onClick={handleSubmit} loading={createCluster.isPending} disabled={!name || !mgmtLif || !username || !password}>
            Verbinden & hinzufügen
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

function ClusterTab() {
  const { data: clusters } = useNetAppClusters();
  const verifyCluster = useVerifyNetAppCluster();
  const enrollCert = useEnrollNetAppClusterCertificate();
  const deleteCluster = useDeleteNetAppCluster();
  const discoverCluster = useDiscoverNetAppCluster();
  const [addOpen, setAddOpen] = useState(false);
  const menu = useContextMenu<NetAppCluster>();

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
      title: "Cluster entfernen",
      message: `Cluster '${cluster.name}' wirklich entfernen? Die gespeicherten Zugangsdaten werden gelöscht.`,
      confirmLabel: "Entfernen",
      onConfirm: () =>
        deleteCluster.mutate(cluster.id, {
          onSuccess: () => notifications.show({ title: "Cluster entfernt", message: cluster.name, color: "blue" }),
          onError: (err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Cluster konnte nicht entfernt werden."), color: "red" }),
        }),
    });
  }

  const versionDistribution = groupCount(clusters, (c) => c.ontap_version);
  const healthDistribution = groupCount(clusters, (c) => HEALTH_LABEL[c.health]);

  return (
    <Stack>
      <StatRibbon>
        <StatCard label="Anzahl Cluster" value={clusters?.length ?? 0} />
        <DistributionCard label="ONTAP-Versionen" items={versionDistribution} />
        <DistributionCard
          label="Gesundheitszustand"
          items={healthDistribution.map((d) => ({ ...d, color: d.key === "Healthy" ? "green" : d.key === "Eingeschränkt" ? "yellow" : "gray" }))}
        />
      </StatRibbon>

      <Group justify="flex-end">
        <Button leftSection={<IconPlus size={16} />} onClick={() => setAddOpen(true)}>
          Cluster hinzufügen
        </Button>
      </Group>

      <Paper p="md">
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Cluster-Name</Table.Th>
              <Table.Th>Mgmt-IP</Table.Th>
              <Table.Th>Benutzer</Table.Th>
              <Table.Th>Auth</Table.Th>
              <Table.Th>ONTAP-Version</Table.Th>
              <Table.Th>Cluster Health</Table.Th>
              <Table.Th>MetroCluster</Table.Th>
              <Table.Th>Letzte Prüfung</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {clusters?.map((cluster) => (
              <Table.Tr key={cluster.id} onContextMenu={(e) => menu.open(e, cluster)} style={{ cursor: "context-menu" }}>
                <Table.Td>
                  {cluster.name}
                  {cluster.ontap_cluster_name && cluster.ontap_cluster_name !== cluster.name && (
                    <Text size="xs" c="dimmed">
                      ONTAP: {cluster.ontap_cluster_name}
                    </Text>
                  )}
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
                <Table.Td>{cluster.is_metrocluster ? "Ja" : "Nein"}</Table.Td>
                <Table.Td>{cluster.last_checked_at ? new Date(cluster.last_checked_at).toLocaleString("de-DE") : "nie"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {clusters?.length === 0 && (
          <Text c="dimmed" size="sm" ta="center" py="md">
            Noch keine NetApp-Systeme hinzugefügt.
          </Text>
        )}
      </Paper>

      <AddClusterModal opened={addOpen} onClose={() => setAddOpen(false)} onCreated={runDiscovery} />

      <DiscoveryModal
        opened={discoveryOpen}
        onClose={() => setDiscoveryOpen(false)}
        clusterName={discoveryCluster?.name}
        steps={discoverCluster.data}
        isLoading={discoverCluster.isPending}
      />

      <ContextMenuDropdown position={menu.state?.position ?? null} opened={!!menu.state} onClose={menu.close}>
        <Menu.Label>{menu.state?.data.name}</Menu.Label>
        <Menu.Item leftSection={<IconRefresh size={16} />} onClick={() => menu.state && handleVerify(menu.state.data)}>
          Verbindung erneut prüfen
        </Menu.Item>
        <Menu.Item leftSection={<IconRadar2 size={16} />} onClick={() => menu.state && runDiscovery(menu.state.data)}>
          Discovery erneut ausführen
        </Menu.Item>
        {menu.state?.data.auth_method === "password" && (
          <Menu.Item leftSection={<IconCertificate size={16} />} onClick={() => menu.state && handleEnrollCertificate(menu.state.data)}>
            Auf Zertifikat umstellen
          </Menu.Item>
        )}
        <Menu.Item leftSection={<IconTrash size={16} />} color="red" onClick={() => menu.state && handleDelete(menu.state.data)}>
          Entfernen
        </Menu.Item>
      </ContextMenuDropdown>
    </Stack>
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
  const relMenu = useContextMenu<SnapMirrorRelationship>();
  const volMenu = useContextMenu<NetAppVolume>();
  const lunMenu = useContextMenu<NetAppLun>();
  const [extraVolCols, setExtraVolCols] = useState<string[]>([]);
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
    const ratios = list.map((a) => a.efficiency_ratio_wo_snapshots).filter((r): r is number => r != null);
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

      <Tabs value={activeTab} onChange={(v) => setParams({ tab: v ?? "clusters" })}>
        <Tabs.List>
          <Tabs.Tab value="clusters">Cluster</Tabs.Tab>
          <Tabs.Tab value="platforms">Nodes</Tabs.Tab>
          <Tabs.Tab value="aggregates">Aggregate</Tabs.Tab>
          <Tabs.Tab value="svms">Storage Virtual Machines</Tabs.Tab>
          <Tabs.Tab value="volumes">Volumes</Tabs.Tab>
          <Tabs.Tab value="luns">LUNs</Tabs.Tab>
          <Tabs.Tab value="igroups">IGroups</Tabs.Tab>
          <Tabs.Tab value="cluster-peers">Cluster Peer</Tabs.Tab>
          <Tabs.Tab value="svm-peers">SVM Peer</Tabs.Tab>
          <Tabs.Tab value="snapmirror">SnapMirror-Beziehungen</Tabs.Tab>
          <Tabs.Tab value="metrocluster">MetroCluster</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="clusters" pt="md">
          <ClusterTab />
        </Tabs.Panel>

        <Tabs.Panel value="svms" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl SVMs" value={svms?.length ?? 0} />
          </StatRibbon>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Subtype</Table.Th>
                <Table.Th>Allowed Protocols</Table.Th>
                <Table.Th>Data-Services</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {svms?.map((svm) => (
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
              Noch keine SVMs erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="volumes" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl Volumes" value={volumes?.length ?? 0} />
            <CapacityBarCard label="Kapazität" used={volumeStats.totalUsed} total={volumeStats.totalSize} formatValue={formatBytes} />
            <DistributionCard label="Security Style" items={volumeStats.securityStyles} />
          </StatRibbon>
          <Group justify="space-between" mb="xs">
            <Button leftSection={<IconPlus size={16} />} onClick={() => setVolumeFormOpen(true)}>
              Volume anlegen
            </Button>
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
          <Text size="xs" c="dimmed" mb={4}>
            Rechtsklick auf ein Volume für Bearbeiten / Löschen / SnapMirror-Replikation erstellen.
          </Text>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
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
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {volumes?.map((vol) => (
                <Table.Tr key={vol.id} onContextMenu={(e) => volMenu.open(e, vol)} style={{ cursor: "context-menu" }}>
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
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {volumes?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Volumes erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="luns" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl LUNs" value={luns?.length ?? 0} />
            <StatCard label="Provisioniert" value={formatBytes(lunStats.totalSize)} />
            <DistributionCard label="OS-Type" items={lunStats.osTypes} />
          </StatRibbon>
          <Group justify="flex-end" mb="xs">
            <Button leftSection={<IconPlus size={16} />} onClick={() => setLunFormOpen(true)}>
              LUN anlegen
            </Button>
          </Group>
          <Text size="xs" c="dimmed" mb={4}>
            Rechtsklick auf eine LUN für Bearbeiten / Löschen.
          </Text>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>OS-Type</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>LUN-Mapping (IGroups)</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {luns?.map((lun) => (
                <Table.Tr key={lun.id} onContextMenu={(e) => lunMenu.open(e, lun)} style={{ cursor: "context-menu" }}>
                  <Table.Td>{lun.cluster_name}</Table.Td>
                  <Table.Td>{lun.svm_name ?? "-"}</Table.Td>
                  <Table.Td>{lun.volume_name ?? "-"}</Table.Td>
                  <Table.Td>{lun.name}</Table.Td>
                  <Table.Td>{lun.os_type ?? "-"}</Table.Td>
                  <Table.Td>{formatBytes(lun.size_bytes)}</Table.Td>
                  <Table.Td>
                    <Badge color={lun.state === "online" ? "green" : "gray"} variant="light">
                      {lun.state ?? "-"}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{lun.mapped_igroups ?? "-"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {luns?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine LUNs erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="igroups" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl IGroups" value={igroups?.length ?? 0} />
            <DistributionCard label="OS-Type" items={igroupStats.osTypes} />
            <DistributionCard label="Protocol" items={igroupStats.protocols} />
          </StatRibbon>
          <Group justify="flex-end" mb="xs">
            <Button leftSection={<IconPlus size={16} />} onClick={() => setIgroupFormOpen(true)}>
              IGroup anlegen
            </Button>
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>OS-Type</Table.Th>
                <Table.Th>Protocol</Table.Th>
                <Table.Th>Initiatoren</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {igroups?.map((ig) => (
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
              Noch keine Initiator-Gruppen erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="cluster-peers" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl Cluster Peer" value={clusterPeers?.length ?? 0} />
          </StatRibbon>
          <Group justify="flex-end" mb="xs">
            <Button leftSection={<IconLink size={16} />} onClick={() => setClusterPeerFormOpen(true)}>
              Cluster Peer erstellen
            </Button>
          </Group>
          {peerDetail && <ClusterPeerDetailHeader peer={peerDetail} onClose={() => setPeerDetail(null)} />}
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Remote-Name</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {clusterPeers?.map((peer) => (
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
              Noch keine Cluster-Peer-Beziehungen erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="svm-peers" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl SVM Peer" value={svmPeers?.length ?? 0} />
          </StatRibbon>
          <Group justify="flex-end" mb="xs">
            <Button leftSection={<IconLink size={16} />} onClick={() => setSvmPeerFormOpen(true)}>
              SVM Peer erstellen
            </Button>
          </Group>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>SVM</Table.Th>
                <Table.Th>Peer-SVM</Table.Th>
                <Table.Th>Peer-Cluster</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Applications</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {svmPeers?.map((peer) => (
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
              Noch keine SVM-Peer-Beziehungen erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="snapmirror" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl Beziehungen" value={relationships?.length ?? 0} />
            <DistributionCard label="Status" items={snapmirrorStats.states} />
            <DistributionCard
              label="Healthy"
              items={snapmirrorStats.healthy.map((d) => ({ ...d, color: d.key === "OK" ? "green" : "red" }))}
            />
          </StatRibbon>
          <Group justify="flex-end" mb="xs">
            <Button
              leftSection={<IconLink size={16} />}
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
                <Table.Th>Cluster</Table.Th>
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
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {relationships?.map((rel) => (
                <Table.Tr key={rel.id} onContextMenu={(e) => relMenu.open(e, rel)} style={{ cursor: "context-menu" }}>
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
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {relationships?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine SnapMirror-Beziehungen erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="platforms" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl Nodes" value={platforms?.length ?? 0} />
            <DistributionCard label="Modelle" items={nodeStats.models} />
            <DistributionCard label="ONTAP-Versionen" items={nodeStats.versions} />
            <StatCard label="Ø Uptime" value={nodeStats.avgUptimeDays != null ? `${Math.round(nodeStats.avgUptimeDays)} Tage` : "-"} />
          </StatRibbon>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>Node</Table.Th>
                <Table.Th>Modell</Table.Th>
                <Table.Th>Seriennummer</Table.Th>
                <Table.Th>ONTAP-Version</Table.Th>
                <Table.Th>Uptime</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {platforms?.map((p) => (
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
              Noch keine Plattform-Informationen erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="aggregates" pt="md">
          <StatRibbon>
            <StatCard label="Anzahl Aggregate" value={aggregates?.length ?? 0} />
            <CapacityBarCard label="Kapazität" used={aggregateStats.totalUsed} total={aggregateStats.totalSize} formatValue={formatBytes} />
            <StatCard
              label="Storage Efficiency"
              value={aggregateStats.avgEfficiency != null ? `${aggregateStats.avgEfficiency.toFixed(2)} : 1` : "-"}
            />
          </StatRibbon>
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>Node</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegt</Table.Th>
                <Table.Th>Storage Efficiency</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {aggregates?.map((agg) => (
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
                  <Table.Td>{agg.efficiency_ratio != null ? `${agg.efficiency_ratio.toFixed(2)} : 1` : "-"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {aggregates?.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Noch keine Aggregate erkannt. Führe eine Discovery unter Cluster aus.
            </Text>
          )}
        </Tabs.Panel>

        <Tabs.Panel value="metrocluster" pt="md">
          <Paper p="md" maw={480}>
            <Stack gap="xs">
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
          </Paper>
        </Tabs.Panel>
      </Tabs>

      <ContextMenuDropdown position={relMenu.state?.position ?? null} opened={!!relMenu.state} onClose={relMenu.close}>
        <Menu.Label>{relMenu.state?.data.source_path}</Menu.Label>
        <Menu.Item
          leftSection={<IconEdit size={16} />}
          onClick={() => {
            if (!relMenu.state) return;
            setSelectedRelationship(relMenu.state.data);
            setSnapmirrorEditOpen(true);
          }}
        >
          Bearbeiten
        </Menu.Item>
        <Menu.Item leftSection={<IconRefresh size={16} />} onClick={() => relMenu.state && triggerUpdate(relMenu.state.data)}>
          SnapMirror-Update erzwingen
        </Menu.Item>
        <Menu.Item leftSection={<IconInfoCircle size={16} />}>Details anzeigen</Menu.Item>
      </ContextMenuDropdown>

      <ContextMenuDropdown position={volMenu.state?.position ?? null} opened={!!volMenu.state} onClose={volMenu.close}>
        <Menu.Label>{volMenu.state?.data.name}</Menu.Label>
        <Menu.Item
          leftSection={<IconEdit size={16} />}
          onClick={() => {
            if (!volMenu.state) return;
            setSelectedVolume(volMenu.state.data);
            setVolumeEditOpen(true);
          }}
        >
          Bearbeiten
        </Menu.Item>
        <Menu.Item leftSection={<IconLink size={16} />} onClick={() => volMenu.state && openSnapmirrorForVolume(volMenu.state.data)}>
          SnapMirror-Replikation erstellen
        </Menu.Item>
        <Menu.Item leftSection={<IconTrash size={16} />} color="red" onClick={() => volMenu.state && handleDeleteVolume(volMenu.state.data)}>
          Löschen
        </Menu.Item>
      </ContextMenuDropdown>

      <ContextMenuDropdown position={lunMenu.state?.position ?? null} opened={!!lunMenu.state} onClose={lunMenu.close}>
        <Menu.Label>{lunMenu.state?.data.name}</Menu.Label>
        <Menu.Item
          leftSection={<IconEdit size={16} />}
          onClick={() => {
            if (!lunMenu.state) return;
            setSelectedLun(lunMenu.state.data);
            setLunEditOpen(true);
          }}
        >
          Bearbeiten
        </Menu.Item>
        <Menu.Item leftSection={<IconTrash size={16} />} color="red" onClick={() => lunMenu.state && handleDeleteLun(lunMenu.state.data)}>
          Löschen
        </Menu.Item>
      </ContextMenuDropdown>

      <IgroupFormModal opened={igroupFormOpen} onClose={() => setIgroupFormOpen(false)} clusters={clusters} svms={svms} />
      <LunFormModal
        opened={lunFormOpen}
        onClose={() => setLunFormOpen(false)}
        clusters={clusters}
        svms={svms}
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
        svms={svms}
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
