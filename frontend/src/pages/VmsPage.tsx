import { useEffect, useMemo, useState } from "react";
import { ActionIcon, Badge, Box, Group, Paper, Progress, SegmentedControl, Stack, Table, Tabs, Text, Title, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconBolt,
  IconChartLine,
  IconChevronsRight,
  IconCpu,
  IconDatabase,
  IconFileText,
  IconFolder,
  IconHistory,
  IconInfoCircle,
  IconNetwork,
  IconRefresh,
  IconServer,
  IconServer2,
  IconServerCog,
  IconStack2,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useCsvs, useDeleteVmCheckpoint, useDiscoverVm, useResourceGroups, useRunningJobRuns, useSmbShares, useVms } from "@/api/hooks";
import { useSites } from "@/api/hooks.sites";
import { BackupsModal } from "@/components/BackupsModal";
import { CapacityHistoryPanel } from "@/components/CapacityHistoryPanel";
import { PolicyPickerModal } from "@/components/PolicyPickerModal";
import { RestoreWizardModal } from "@/components/RestoreWizardModal";
import { SearchInput } from "@/components/SearchInput";
import { SiteBadgeView } from "@/components/SiteBadge";
import type { BackupScope, Csv, ResourceGroup, SmbShare, Vm } from "@/api/types";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, lunShortName, VM_STATE_COLOR as STATE_COLOR } from "@/utils/format";
import { useAuthStore } from "@/store/authStore";
import { useRunPolicy } from "@/utils/runPolicy";
import { matchesAllColumns } from "@/utils/search";

// Grobe, aber ausreichende Alters-Anzeige fuer einen Checkpoint-Zeitstempel
// (ISO-8601 mit Offset, siehe HyperVService.list_vms) -- dieselbe
// Aufloesung wie im Backend-Alarmtext (run_alert_check), nur clientseitig
// fuer die Inventory-Tabelle nachgebildet, da hier kein Alarm noetig ist,
// nur eine Anzeige.
// Ein normaler, vom Tool selbst erstellter Backup-Checkpoint besteht nur
// Sekunden bis wenige Minuten -- aelter als das gilt als "vermutlich haengen
// geblieben" (Backlog-Punkt 49, siehe visibleCheckpointsOf unten).
const STUCK_CHECKPOINT_MINUTES = 10;

function formatCheckpointAge(creationTime: string): string {
  const created = new Date(creationTime);
  if (Number.isNaN(created.getTime())) return "unbekanntes Alter";
  const minutes = Math.max(0, Math.round((Date.now() - created.getTime()) / 60000));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} Tage`;
}

// CsvRead hat (anders als VmRead.id) keine eigene stabile Zeilen-ID -- der
// Name allein ist NICHT eindeutig, sobald zwei Hyper-V-Cluster ein CSV mit
// identischem Namen haben (in der Praxis haeufig, z.B. beide "CSV01").
// cluster_id+Name ist dagegen stabil (bleibt ueber Discovery-Laeufe hinweg
// gleich, anders als eine rohe Zeilen-ID, die bei jeder Discovery neu
// vergeben wird) -- dieselbe Ueberlegung wie beim Backend-Fix fuer
// ResourceGroup.members (siehe app.models.resource_group).
function csvIdentity(csv: Csv): string {
  return `${csv.cluster_id ?? ""}::${csv.name}`;
}

// Analog zu csvIdentity, fuer SMB3-Freigaben (Backlog #22).
function smbShareIdentity(share: SmbShare): string {
  return `${share.cluster_id ?? ""}::${share.server}::${share.share}`;
}

function ResourceGroupCell({ groups, policies }: { groups: string[]; policies: string[] }) {
  if (!groups.length) {
    return (
      <Text c="dimmed" size="sm">
        keine
      </Text>
    );
  }
  return (
    <Stack gap={4}>
      <Group gap={4}>
        {groups.map((g) => (
          <Badge key={g} color="blue" variant="light">
            {g}
          </Badge>
        ))}
      </Group>
      {policies.length > 0 && (
        <Text size="xs" c="dimmed">
          Policy: {policies.join(", ")}
        </Text>
      )}
    </Stack>
  );
}

function ProtectedBadge({ protected: isProtected }: { protected: boolean }) {
  return (
    <Badge color={isProtected ? "green" : "red"} variant="light">
      {isProtected ? "Protected" : "Ungeschützt"}
    </Badge>
  );
}

function ChainNode({
  icon,
  label,
  title,
  // Optionaler Tooltip fuer den vollen Wert, falls "title" bereits eine
  // gekuerzte Anzeigeform ist (z.B. LUN-Kurzname statt vollem ONTAP-Pfad,
  // siehe lunShortName in utils/format.ts) -- ansonsten ohne Tooltip.
  fullTitle,
  usedBytes,
  capacityBytes,
}: {
  icon: React.ReactNode;
  label: string;
  title: string;
  fullTitle?: string;
  usedBytes?: number | null;
  capacityBytes?: number | null;
}) {
  const hasUsage = usedBytes != null && capacityBytes != null && capacityBytes > 0;
  const pct = hasUsage ? Math.round((usedBytes! / capacityBytes!) * 100) : null;
  const titleText = (
    <Text size="sm" fw={600} truncate maw={170}>
      {title}
    </Text>
  );
  return (
    <Paper withBorder p="xs" miw={150}>
      <Group gap={6} mb={2} wrap="nowrap">
        {icon}
        <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
          {label}
        </Text>
      </Group>
      {fullTitle && fullTitle !== title ? (
        <Tooltip label={fullTitle} openDelay={300}>
          {titleText}
        </Tooltip>
      ) : (
        titleText
      )}
      {capacityBytes != null && (
        <Text size="xs" c="dimmed">
          {hasUsage ? `${formatBytes(usedBytes)} / ${formatBytes(capacityBytes)}` : formatBytes(capacityBytes)}
        </Text>
      )}
      {pct !== null && <Progress value={pct} size={4} mt={4} color={pct >= 90 ? "red" : pct >= 75 ? "yellow" : "blue"} />}
    </Paper>
  );
}

// Backlog #22: '\\server\share\...' -> {server, share}, sonst null (lokaler/
// ClusterStorage-Pfad). Gemeinsam von VmChainHeader (Speicherkette) und
// vmsOnSmbShare (SMB3-Freigaben-Tab) genutzt.
function parseSmbShare(path: string): { server: string; share: string } | null {
  const match = /^\\\\([^\\]+)\\([^\\]+)\\/.exec(path);
  return match ? { server: match[1], share: match[2] } : null;
}

function VmChainHeader({
  vm,
  csvs,
  smbShares,
  onClose,
}: {
  vm: Vm;
  csvs: Csv[] | undefined;
  smbShares: SmbShare[] | undefined;
  onClose: () => void;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const vhds = vm.vhds.length
    ? vm.vhds
    : [...vm.csv_paths, ...vm.smb_share_paths].map((p) => ({
        name: `${vm.name}.vhdx`,
        size_bytes: vm.vhdx_size_bytes ?? 0,
        used_bytes: vm.vhdx_used_bytes,
        csv_path: p,
        full_path: p,
      }));

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>
          Speicherkette: {vm.name}
        </Text>
        <Group gap={4}>
          <Tooltip label="Kapazitätsverlauf (je VHD eine Linie)">
            <ActionIcon variant={historyOpen ? "light" : "subtle"} size="sm" onClick={() => setHistoryOpen((v) => !v)}>
              <IconChartLine size={14} />
            </ActionIcon>
          </Tooltip>
          <ActionIcon variant="subtle" size="sm" onClick={onClose}>
            <IconX size={14} />
          </ActionIcon>
        </Group>
      </Group>

      {historyOpen && (
        <Box mb="sm">
          <CapacityHistoryPanel objectType="vhd" clusterId={vm.cluster_id} vmUuid={vm.id} />
        </Box>
      )}

      <Group gap="lg" mb="sm">
        <Group gap={4}>
          <IconCpu size={14} />
          <Text size="xs" c="dimmed">
            {vm.cpu_count ?? "-"} vCPU
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          RAM: {formatBytes(vm.memory_startup_bytes)}
          {vm.dynamic_memory_enabled && ` (dynamisch: ${formatBytes(vm.memory_minimum_bytes)} – ${formatBytes(vm.memory_maximum_bytes)})`}
        </Text>
        <Text size="xs" c="dimmed">
          Generation {vm.generation ?? "-"}
        </Text>
      </Group>
      {vm.network_adapters.length > 0 && (
        <Group gap="xs" mb="xs">
          {vm.network_adapters.map((n, i) => (
            <Badge key={i} variant="light" color="grape" leftSection={<IconNetwork size={12} />}>
              {n.name}: {n.mac_address ?? "-"} @ {n.switch_name ?? "-"}
              {n.vlan_id ? ` (VLAN ${n.vlan_id})` : ""}
            </Badge>
          ))}
        </Group>
      )}
      {vm.pci_devices.length > 0 && (
        <Text size="xs" c="dimmed" mb="sm">
          PCI-Devices: {vm.pci_devices.join(", ")}
        </Text>
      )}

      <Stack gap="sm">
        {vhds.map((vhd, i) => {
          // Backlog #22: eine VHD liegt entweder auf einer CSV ODER einem
          // SMB3-Export -- full_path ist der einzige verlaesslich fuer beide
          // Faelle vorhandene Pfad (csv_path faellt fuer SMB auf den vollen
          // Dateipfad zurueck, ungeeignet fuer eine Freigaben-Aufloesung).
          const smb = parseSmbShare(vhd.full_path);
          const csvName = !smb ? vhd.csv_path.split(/[\\/]/).pop() : null;
          const csv = csvName ? csvs?.find((c) => c.name === csvName && c.cluster_id === vm.cluster_id) : undefined;
          const share = smb
            ? smbShares?.find((s) => s.server === smb.server && s.share === smb.share && s.cluster_id === vm.cluster_id)
            : undefined;
          return (
            <Box key={i} style={{ overflowX: "auto" }}>
              <Group gap={6} wrap="nowrap">
                <ChainNode icon={<IconServer2 size={14} />} label="VM" title={vm.name} />
                <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                <ChainNode
                  icon={<IconFileText size={14} />}
                  label="VHD"
                  title={vhd.name}
                  usedBytes={vhd.used_bytes}
                  capacityBytes={vhd.size_bytes}
                />
                <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                {csv ? (
                  <>
                    <ChainNode
                      icon={<IconFolder size={14} />}
                      label="CSV"
                      title={csv.name}
                      usedBytes={csv.used_bytes}
                      capacityBytes={csv.capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode
                      icon={<IconStack2 size={14} />}
                      label="LUN"
                      title={lunShortName(csv.lun_name)}
                      fullTitle={csv.lun_name ?? undefined}
                      usedBytes={csv.lun_used_bytes}
                      capacityBytes={csv.lun_capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode
                      icon={<IconDatabase size={14} />}
                      label="Volume"
                      title={csv.volume_name ?? "-"}
                      usedBytes={csv.volume_used_bytes}
                      capacityBytes={csv.volume_capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServerCog size={14} />} label="SVM" title={csv.svm_name ?? "-"} />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServer size={14} />} label="Cluster" title={csv.netapp_cluster_name ?? "-"} />
                  </>
                ) : share ? (
                  <>
                    <ChainNode
                      icon={<IconFolder size={14} />}
                      label="SMB3-Freigabe"
                      title={`\\\\${share.server}\\${share.share}`}
                      usedBytes={share.used_bytes}
                      capacityBytes={share.capacity_bytes}
                    />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconDatabase size={14} />} label="Volume" title={share.volume_name ?? "-"} />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServerCog size={14} />} label="SVM" title={share.svm_name ?? "-"} />
                    <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
                    <ChainNode icon={<IconServer size={14} />} label="Cluster" title={share.netapp_cluster_name ?? "-"} />
                  </>
                ) : (
                  <Text c="dimmed" size="sm">
                    {smb ? "SMB3-Freigaben-Details nicht verfügbar" : "CSV-Details nicht verfügbar"}
                  </Text>
                )}
              </Group>
            </Box>
          );
        })}
      </Stack>
    </Paper>
  );
}

// Gemeinsam von CsvChainHeader (Detailansicht) und der CSV-Tabellenspalte
// "Anzahl VMs" genutzt -- ein CSV traegt selbst keine VM-Liste, daher ueber
// vm.csv_paths (Ordnername je VHD-Pfad) rueckwaerts aufgeloest.
function vmsOnCsv(csv: Csv, vms: Vm[] | undefined): Vm[] {
  return vms?.filter((vm) => vm.cluster_id === csv.cluster_id && vm.csv_paths.some((p) => p.split(/[\\/]/).pop() === csv.name)) ?? [];
}

// Analog zu vmsOnCsv, fuer SMB3-Freigaben (Backlog #22) -- eine Freigabe
// traegt ebenfalls keine eigene VM-Liste.
function vmsOnSmbShare(share: SmbShare, vms: Vm[] | undefined): Vm[] {
  return (
    vms?.filter(
      (vm) =>
        vm.cluster_id === share.cluster_id &&
        vm.smb_share_paths.some((p) => {
          const parsed = parseSmbShare(p + "\\");
          return parsed?.server === share.server && parsed?.share === share.share;
        }),
    ) ?? []
  );
}

function CsvChainHeader({
  csv,
  vms,
  onClose,
  onVmClick,
}: {
  csv: Csv;
  vms: Vm[] | undefined;
  onClose: () => void;
  onVmClick: (vm: Vm) => void;
}) {
  const vmsOnThisCsv = vmsOnCsv(csv, vms);
  const [historyOpen, setHistoryOpen] = useState(false);

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>
          Speicherkette: {csv.name}
        </Text>
        <Group gap={4}>
          <Tooltip label="Kapazitätsverlauf">
            <ActionIcon variant={historyOpen ? "light" : "subtle"} size="sm" onClick={() => setHistoryOpen((v) => !v)}>
              <IconChartLine size={14} />
            </ActionIcon>
          </Tooltip>
          <ActionIcon variant="subtle" size="sm" onClick={onClose}>
            <IconX size={14} />
          </ActionIcon>
        </Group>
      </Group>

      {historyOpen && (
        <Box mb="sm">
          <CapacityHistoryPanel objectType="csv" clusterId={csv.cluster_id} name={csv.name} />
        </Box>
      )}

      <Stack gap="sm">
        <Group gap={6} wrap="nowrap">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            VMs auf diesem CSV:
          </Text>
          {vmsOnThisCsv.length ? (
            vmsOnThisCsv.map((vm) => (
              <Badge
                key={vm.id}
                component="button"
                color="teal"
                variant="light"
                style={{ cursor: "pointer", border: "none" }}
                onClick={() => onVmClick(vm)}
              >
                {vm.name}
              </Badge>
            ))
          ) : (
            <Text size="xs" c="dimmed">
              keine
            </Text>
          )}
        </Group>
        <Box style={{ overflowX: "auto" }}>
          <Group gap={6} wrap="nowrap">
            <ChainNode
              icon={<IconFolder size={14} />}
              label="CSV"
              title={csv.name}
              usedBytes={csv.used_bytes}
              capacityBytes={csv.capacity_bytes}
            />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode
              icon={<IconStack2 size={14} />}
              label="LUN"
              title={lunShortName(csv.lun_name)}
              fullTitle={csv.lun_name ?? undefined}
              usedBytes={csv.lun_used_bytes}
              capacityBytes={csv.lun_capacity_bytes}
            />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode
              icon={<IconDatabase size={14} />}
              label="Volume"
              title={csv.volume_name ?? "-"}
              usedBytes={csv.volume_used_bytes}
              capacityBytes={csv.volume_capacity_bytes}
            />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode icon={<IconServerCog size={14} />} label="SVM" title={csv.svm_name ?? "-"} />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode icon={<IconServer size={14} />} label="Cluster" title={csv.netapp_cluster_name ?? "-"} />
          </Group>
        </Box>
      </Stack>
    </Paper>
  );
}

// SMB3-Pendant zu CsvChainHeader (Backlog #22) -- gleiches Muster, aber ohne
// LUN-Knoten (eine SMB3-Freigabe liegt direkt auf einem NetApp-Volume, kein
// Block-LUN-Umweg).
function SmbShareChainHeader({
  share,
  vms,
  onClose,
  onVmClick,
}: {
  share: SmbShare;
  vms: Vm[] | undefined;
  onClose: () => void;
  onVmClick: (vm: Vm) => void;
}) {
  const vmsOnThisShare = vmsOnSmbShare(share, vms);
  const [historyOpen, setHistoryOpen] = useState(false);
  const uncPath = `\\\\${share.server}\\${share.share}`;

  return (
    <Paper withBorder p="md">
      <Group justify="space-between" mb="xs">
        <Text size="sm" fw={600}>
          Speicherkette: {uncPath}
        </Text>
        <Group gap={4}>
          <Tooltip label="Kapazitätsverlauf">
            <ActionIcon variant={historyOpen ? "light" : "subtle"} size="sm" onClick={() => setHistoryOpen((v) => !v)}>
              <IconChartLine size={14} />
            </ActionIcon>
          </Tooltip>
          <ActionIcon variant="subtle" size="sm" onClick={onClose}>
            <IconX size={14} />
          </ActionIcon>
        </Group>
      </Group>

      {historyOpen && (
        <Box mb="sm">
          <CapacityHistoryPanel objectType="smb_share" clusterId={share.cluster_id} name={uncPath} />
        </Box>
      )}

      <Stack gap="sm">
        <Group gap={6} wrap="nowrap">
          <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
            VMs auf dieser Freigabe:
          </Text>
          {vmsOnThisShare.length ? (
            vmsOnThisShare.map((vm) => (
              <Badge
                key={vm.id}
                component="button"
                color="teal"
                variant="light"
                style={{ cursor: "pointer", border: "none" }}
                onClick={() => onVmClick(vm)}
              >
                {vm.name}
              </Badge>
            ))
          ) : (
            <Text size="xs" c="dimmed">
              keine
            </Text>
          )}
        </Group>
        <Box style={{ overflowX: "auto" }}>
          <Group gap={6} wrap="nowrap">
            <ChainNode
              icon={<IconFolder size={14} />}
              label="SMB3-Freigabe"
              title={uncPath}
              usedBytes={share.used_bytes}
              capacityBytes={share.capacity_bytes}
            />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode icon={<IconDatabase size={14} />} label="Volume" title={share.volume_name ?? "-"} />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode icon={<IconServerCog size={14} />} label="SVM" title={share.svm_name ?? "-"} />
            <IconChevronsRight size={16} style={{ flexShrink: 0 }} />
            <ChainNode icon={<IconServer size={14} />} label="Cluster" title={share.netapp_cluster_name ?? "-"} />
          </Group>
        </Box>
      </Stack>
    </Paper>
  );
}

export function VmsPage() {
  const hasPermission = useAuthStore((s) => s.hasPermission);
  // RBAC (Backlog #12, 2026-09-17): "Backup jetzt" braucht backup:run,
  // Checkpoint-Loeschen/VM-Discovery braucht hyperv:manage.
  const canRunBackup = hasPermission("backup:run");
  const canManageHyperv = hasPermission("hyperv:manage");
  const [params, setParams] = useSearchParams();
  const tabParam = params.get("tab");
  const activeTab = tabParam === "csv" ? "csv" : tabParam === "smb" ? "smb" : "vms";
  const { data: vms } = useVms();
  const { data: csvs } = useCsvs();
  const { data: smbShares } = useSmbShares();
  const [selectedVm, setSelectedVm] = useState<Vm | null>(null);
  const [selectedCsv, setSelectedCsv] = useState<Csv | null>(null);
  const [selectedSmbShare, setSelectedSmbShare] = useState<SmbShare | null>(null);
  const [backupsTarget, setBackupsTarget] = useState<{ scope: BackupScope; name: string; clusterId: string | null } | null>(null);
  const [restoreWizardTarget, setRestoreWizardTarget] = useState<{ vmName: string; snapshotId: string; clusterId: string | null } | null>(
    null,
  );
  const [vmSearch, setVmSearch] = useState("");
  // Standort-Filter (Settings > Standorte) -- nur sichtbar, sobald
  // mindestens ein Standort angelegt ist.
  const { data: sites } = useSites();
  const sitesConfigured = (sites?.length ?? 0) > 0;
  // ?site=mismatch|unassigned: Einstieg ueber die Dashboard-Kachel.
  const [siteFilter, setSiteFilter] = useState<"all" | "mismatch" | "unassigned">(() => {
    const p = params.get("site");
    return p === "mismatch" || p === "unassigned" ? p : "all";
  });
  const siteMismatchCount = (vms ?? []).filter((vm) => vm.site_mismatch).length;
  const siteUnassignedCount = (vms ?? []).filter((vm) => vm.site_unassigned).length;
  const filteredVms = (vms ?? []).filter(
    (vm) =>
      matchesAllColumns(vm, vmSearch) &&
      (!sitesConfigured ||
        siteFilter === "all" ||
        (siteFilter === "mismatch" && vm.site_mismatch) ||
        (siteFilter === "unassigned" && vm.site_unassigned)),
  );
  const [csvSearch, setCsvSearch] = useState("");
  const filteredCsvs = (csvs ?? []).filter((csv) => matchesAllColumns(csv, csvSearch));
  const [smbShareSearch, setSmbShareSearch] = useState("");
  const filteredSmbShares = (smbShares ?? []).filter((share) => matchesAllColumns(share, smbShareSearch));

  // Deep-Link von einem VM-Badge in CsvChainHeader ("VMs auf diesem CSV") --
  // setParams({ tab: "vms", vm: vm.id }) setzt den Query-Param, dieser
  // Effekt wendet ihn EINMALIG an (Param wird danach wieder entfernt),
  // damit eine spaetere manuelle Ab-/Auswahl durch den Nutzer nicht durch
  // einen stehen gebliebenen Param ueberschrieben wird. Wartet auf
  // geladene vms, damit ein Deep-Link direkt nach einem Seiten-Reload
  // nicht ins Leere laeuft.
  useEffect(() => {
    const targetVmId = params.get("vm");
    if (!targetVmId || !vms) return;
    const target = vms.find((vm) => vm.id === targetVmId);
    if (target) setSelectedVm(target);
    const next = new URLSearchParams(params);
    next.delete("vm");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params, vms]);
  const { runOrPickForGroups, step1GroupChoices, step2PolicyChoices, pickStep1Group, pickStep2Policy, closeStep1, closeStep2 } =
    useRunPolicy();
  const { data: resourceGroups } = useResourceGroups();
  const deleteCheckpoint = useDeleteVmCheckpoint();
  const discoverVm = useDiscoverVm();

  // Backlog-Punkt 46: eine anwendungskonsistente Policy erzeugt fuer die
  // Dauer des Snapshots einen EIGENEN, kurzlebigen Checkpoint (create ->
  // Snapshot -> remove, siehe _run_node_checkpoints in jobs.py) -- ohne
  // dieses Wissen zeigte Inventory dafuer kurzzeitig "Checkpoint
  // vorhanden" und direkt danach "AVHDX ohne Checkpoint", obwohl beides
  // nur der normale, erwartete Backup-Ablauf ist (live gemeldet bei einem
  // 30-VM-Lauf). Vom bereits gepollten "Laufende Backup-Jobs"-Datensatz
  // abgeleitet (kein zusaetzlicher Request), Set der aktuell in einem
  // laufenden Lauf enthaltenen VM-Namen.
  const { data: runningJobs } = useRunningJobRuns(true);
  const vmNamesWithRunningBackup = useMemo(
    () => new Set((runningJobs ?? []).flatMap((r) => r.targets)),
    [runningJobs],
  );

  // Blendet einen vom Tool selbst erzeugten Checkpoint (app_created, Name
  // 'hvnb_...') aus, WAEHREND ein Backup fuer diese VM aktuell laeuft --
  // ein manuell angelegter Checkpoint bleibt dabei weiterhin sichtbar
  // (z.B. wenn die Backup-Policy zusaetzlich auf einem bereits bestehenden
  // manuellen Checkpoint aufsetzt). Existiert der Checkpoint noch NACH
  // Lauf-Ende, ist das der bekannte Orphan-Fall (AlertType.HYPERV_ORPHAN_
  // CHECKPOINT, siehe scheduler.py) und wird bewusst weiterhin angezeigt.
  //
  // Backlog-Punkt 49 (Nutzer-Vorgabe 2026-09-15): die Unterdrueckung allein
  // ueber "laeuft laut DB gerade ein Backup fuer diese VM" reicht nicht --
  // ein haengen gebliebener/abgestuerzter Lauf kann beliebig lange als
  // RUNNING stehen bleiben (WinRM-Timeout noch nicht erreicht, Watchdog
  // noch nicht gelaufen), waehrenddessen bliebe ein tatsaechlich verwaister
  // hvnb_-Checkpoint unsichtbar. Zusaetzlich zum Lauf-Status daher auch das
  // ALTER des Checkpoints pruefen: ein normaler Backup-Checkpoint besteht
  // nur Sekunden bis wenige Minuten (siehe auch die Karenzzeit-Begruendung
  // beim Orphan-Alarm oben) -- aelter als STUCK_CHECKPOINT_MINUTES heisst
  // "vermutlich haengen geblieben", wird dann auch waehrend eines
  // (vermeintlich) laufenden Backups wieder angezeigt.
  function visibleCheckpointsOf(vm: Vm) {
    if (!vmNamesWithRunningBackup.has(vm.name)) return vm.checkpoints;
    const cutoff = Date.now() - STUCK_CHECKPOINT_MINUTES * 60 * 1000;
    return vm.checkpoints.filter((cp) => !cp.app_created || new Date(cp.creation_time).getTime() < cutoff);
  }

  // Live, ohne auf den naechsten Alarm-Check zu warten (analog zum
  // Multi-CSV-Badge unten) -- die VM hat keinen aktiven Checkpoint, aber
  // mindestens eine Disk zeigt trotzdem eine AVHDX-Differenzdatei. Meist
  // eingefrorene Discovery-Daten (siehe AlertType.HYPERV_VM_AVHDX_WITHOUT_
  // CHECKPOINT in scheduler.py), behebbar per "VM Discovery". Waehrend
  // eines laufenden Backups fuer diese VM ebenfalls unterdrueckt -- die
  // AVHDX ohne Checkpoint direkt nach der Entfernung des Backup-eigenen
  // Checkpoints ist hier der Normalfall, nicht eingefrorene Daten.
  function avhdxVhdsOf(vm: Vm) {
    if (vm.checkpoints.length > 0 || vmNamesWithRunningBackup.has(vm.name)) return [];
    return vm.vhds.filter((v) => v.name.toLowerCase().endsWith(".avhdx"));
  }

  function discoverVmNow(vm: Vm) {
    if (!vm.cluster_id) return;
    discoverVm.mutate(
      { clusterId: vm.cluster_id, vmName: vm.name },
      {
        onSuccess: () => notifications.show({ title: "VM aktualisiert", message: vm.name, color: "green" }),
        onError: (err) =>
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "VM konnte nicht aktualisiert werden."), color: "red" }),
      },
    );
  }

  function showBackups(scope: BackupScope, name: string, clusterId: string | null | undefined) {
    setBackupsTarget({ scope, name, clusterId: clusterId ?? null });
  }

  // Loescht ALLE Checkpoints einer VM in einem Rutsch -- der Regelfall ist
  // ohnehin genau einer (ein abgebrochener Backup-Lauf hinterlaesst nicht
  // mehrere), eine Einzelauswahl je Checkpoint waere fuer diesen seltenen
  // Fall unnoetige UI-Komplexitaet.
  function deleteVmCheckpoints(vm: Vm) {
    const checkpoints = visibleCheckpointsOf(vm);
    if (!vm.cluster_id || checkpoints.length === 0) return;
    const clusterId = vm.cluster_id;
    confirmAction({
      title: "Checkpoint löschen",
      message: (
        <Stack gap={4}>
          <Text size="sm">
            {checkpoints.length === 1 ? "Diesen Checkpoint" : `Diese ${checkpoints.length} Checkpoints`} von "{vm.name}"
            unwiderruflich löschen?
          </Text>
          {checkpoints.map((cp) => (
            <Text key={cp.id} size="xs" c="dimmed">
              {cp.name} — seit {formatCheckpointAge(cp.creation_time)}
              {cp.app_created ? " (vermutlich von einem abgebrochenen Backup-Lauf)" : " (manuell erstellt)"}
            </Text>
          ))}
        </Stack>
      ),
      confirmLabel: "Löschen",
      color: "red",
      onConfirm: async () => {
        for (const cp of checkpoints) {
          try {
            await deleteCheckpoint.mutateAsync({ clusterId, vmName: vm.name, checkpointId: cp.id });
          } catch (err) {
            notifications.show({
              title: "Fehler",
              message: apiErrorMessage(err, `Checkpoint '${cp.name}' konnte nicht gelöscht werden.`),
              color: "red",
            });
            return;
          }
        }
        notifications.show({ title: "Checkpoint(s) gelöscht", message: vm.name, color: "green" });
      },
    });
  }

  function toggleSelectedVm(vm: Vm) {
    setSelectedCsv(null);
    setSelectedSmbShare(null);
    setSelectedVm((prev) => (prev?.id === vm.id ? null : vm));
  }

  function toggleSelectedCsv(csv: Csv) {
    setSelectedVm(null);
    setSelectedSmbShare(null);
    setSelectedCsv((prev) => (prev && csvIdentity(prev) === csvIdentity(csv) ? null : csv));
  }

  function toggleSelectedSmbShare(share: SmbShare) {
    setSelectedVm(null);
    setSelectedCsv(null);
    setSelectedSmbShare((prev) => (prev && smbShareIdentity(prev) === smbShareIdentity(share) ? null : share));
  }

  // Eine VM/ein CSV kann gleichzeitig in mehreren Protection Groups
  // enthalten sein (Nutzer-Vorgabe 2026-09-09) -- resource_group_names ist
  // global eindeutig (ResourceGroup.name hat einen unique-Constraint),
  // daher reicht ein Namensabgleich gegen die vollen ResourceGroup-Objekte
  // (liefert dadurch group.id UND group.policies direkt, siehe
  // runOrPickForGroups in utils/runPolicy.ts).
  function protectingGroupsOf(names: string[]): ResourceGroup[] {
    return (resourceGroups ?? []).filter((g) => names.includes(g.name));
  }

  function runBackupNow(vm: Vm) {
    runOrPickForGroups(protectingGroupsOf(vm.resource_group_names));
  }

  function runBackupNowForCsv(csv: Csv) {
    runOrPickForGroups(protectingGroupsOf(csv.resource_group_names));
  }

  function runBackupNowForSmbShare(share: SmbShare) {
    runOrPickForGroups(protectingGroupsOf(share.resource_group_names));
  }

  return (
    <Stack style={{ height: "calc(100vh - 112px)" }} gap="md">
      <Title order={3}>Inventory</Title>

      <Tabs
        value={activeTab}
        onChange={(v) => setParams({ tab: v ?? "vms" })}
        style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
        <Tabs.List>
          <Tabs.Tab value="vms">Virtuelle Maschinen</Tabs.Tab>
          <Tabs.Tab value="csv">Cluster Shared Volumes</Tabs.Tab>
          {(smbShares?.length ?? 0) > 0 && <Tabs.Tab value="smb">SMB3-Freigaben</Tabs.Tab>}
        </Tabs.List>

        <Tabs.Panel value="vms" pt="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          {selectedVm && (
            <Box mb="md">
              <VmChainHeader vm={selectedVm} csvs={csvs} smbShares={smbShares} onClose={() => setSelectedVm(null)} />
            </Box>
          )}
          <Paper p="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <Title order={5} mb="sm">Virtuelle Maschinen</Title>
            <Group justify="flex-start" mb="sm">
              <SearchInput value={vmSearch} onChange={setVmSearch} placeholder="VM-Name suchen…" />
              {sitesConfigured && (
                <SegmentedControl
                  size="xs"
                  value={siteFilter}
                  onChange={(v) => setSiteFilter(v as typeof siteFilter)}
                  data={[
                    { value: "all", label: "Alle" },
                    { value: "mismatch", label: `Standort-Abweichung (${siteMismatchCount})` },
                    { value: "unassigned", label: `Ohne Standort (${siteUnassignedCount})` },
                  ]}
                />
              )}
            </Group>
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            <Table striped highlightOnHover>
            <Table.Thead style={{ position: "sticky", top: 0, zIndex: 1, background: "var(--mantine-color-body)" }}>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Host</Table.Th>
                <Table.Th>Cluster</Table.Th>
                <Table.Th>Speicherort</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>Protection Group</Table.Th>
                <Table.Th>Protected</Table.Th>
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredVms.map((vm) => {
                const hasVhdxUsage = vm.vhdx_used_bytes != null && vm.vhdx_size_bytes != null && vm.vhdx_size_bytes > 0;
                const vhdxPct = hasVhdxUsage ? Math.round((vm.vhdx_used_bytes! / vm.vhdx_size_bytes!) * 100) : null;
                const avhdxVhds = avhdxVhdsOf(vm);
                const visibleCheckpoints = visibleCheckpointsOf(vm);
                return (
                <Table.Tr
                  key={vm.id}
                  onClick={() => toggleSelectedVm(vm)}
                  style={{ cursor: "pointer", backgroundColor: selectedVm?.id === vm.id ? "var(--mantine-color-blue-light)" : undefined }}
                >
                  <Table.Td>{vm.name}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Badge color={STATE_COLOR[vm.state] ?? "gray"} variant="light">
                        {vm.state}
                      </Badge>
                      {visibleCheckpoints.length > 0 && (
                        <Tooltip
                          multiline
                          w={280}
                          label={
                            <Stack gap={2}>
                              {visibleCheckpoints.map((cp) => (
                                <Text key={cp.id} size="xs">
                                  {cp.name} — seit {formatCheckpointAge(cp.creation_time)}
                                </Text>
                              ))}
                            </Stack>
                          }
                        >
                          <Badge color="orange" variant="filled" leftSection={<IconAlertTriangle size={12} />}>
                            {visibleCheckpoints.length > 1 ? `${visibleCheckpoints.length} Checkpoints` : "Checkpoint"}
                          </Badge>
                        </Tooltip>
                      )}
                      {vm.csv_paths.length + vm.smb_share_paths.length > 1 && (
                        <Tooltip
                          multiline
                          w={280}
                          label={
                            <Stack gap={2}>
                              <Text size="xs">Festplatten verteilt auf:</Text>
                              {[...vm.csv_paths, ...vm.smb_share_paths].map((p) => (
                                <Text key={p} size="xs">
                                  {p}
                                </Text>
                              ))}
                            </Stack>
                          }
                        >
                          <Badge color="orange" variant="filled" leftSection={<IconAlertTriangle size={12} />}>
                            {vm.csv_paths.length + vm.smb_share_paths.length} Speicherorte
                          </Badge>
                        </Tooltip>
                      )}
                      {vm.site_mismatch && vm.host_site && (
                        <Tooltip
                          multiline
                          w={300}
                          label={
                            <Stack gap={2}>
                              <Text size="xs">
                                Host {vm.host} steht in {vm.host_site.name}, aber folgende Speicherorte liegen an einem anderen
                                Standort:
                              </Text>
                              {vm.site_mismatch_storage.map((name) => (
                                <Text key={name} size="xs">
                                  {name}
                                </Text>
                              ))}
                            </Stack>
                          }
                        >
                          <Badge color="orange" variant="filled" leftSection={<IconAlertTriangle size={12} />}>
                            Standort-Abweichung
                          </Badge>
                        </Tooltip>
                      )}
                      {avhdxVhds.length > 0 && (
                        <Tooltip
                          multiline
                          w={280}
                          label={
                            <Stack gap={2}>
                              <Text size="xs">
                                Kein aktiver Checkpoint, trotzdem AVHDX -- meist eingefrorene Discovery-Daten. "VM Discovery"
                                aktualisiert den Stand sofort.
                              </Text>
                              {avhdxVhds.map((v) => (
                                <Text key={v.name} size="xs">
                                  {v.name}
                                </Text>
                              ))}
                            </Stack>
                          }
                        >
                          <Badge color="orange" variant="filled" leftSection={<IconAlertTriangle size={12} />}>
                            AVHDX ohne Checkpoint
                          </Badge>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Text size="sm">{vm.host}</Text>
                      {vm.host_site && <SiteBadgeView site={vm.host_site} />}
                    </Group>
                  </Table.Td>
                  <Table.Td>{vm.cluster ?? "-"}</Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      <Text size="sm">{[...vm.csv_paths, ...vm.smb_share_paths].join(", ") || "-"}</Text>
                      {vm.storage_sites.map((site) => (
                        <SiteBadgeView key={site.id} site={site} />
                      ))}
                    </Group>
                  </Table.Td>
                  <Table.Td miw={140}>
                    <Text size="xs" c="dimmed">
                      {formatBytes(vm.vhdx_used_bytes)} / {formatBytes(vm.vhdx_size_bytes)}
                      {vhdxPct != null ? ` (${vhdxPct}%)` : ""}
                    </Text>
                    {vhdxPct != null && (
                      <Progress value={vhdxPct} size={6} mt={2} color={vhdxPct >= 90 ? "red" : vhdxPct >= 75 ? "yellow" : "blue"} />
                    )}
                  </Table.Td>
                  <Table.Td>
                    <ResourceGroupCell groups={vm.resource_group_names} policies={vm.policy_names} />
                  </Table.Td>
                  <Table.Td>
                    <ProtectedBadge protected={vm.protected} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                      <Tooltip label="Backup jetzt starten">
                        <ActionIcon variant="light" disabled={!canRunBackup} onClick={() => runBackupNow(vm)}>
                          <IconBolt size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Details anzeigen">
                        <ActionIcon variant="light" onClick={() => setSelectedVm(vm)}>
                          <IconInfoCircle size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Tooltip label="Backups anzeigen">
                        <ActionIcon variant="light" onClick={() => showBackups("vm", vm.name, vm.cluster_id)}>
                          <IconHistory size={16} />
                        </ActionIcon>
                      </Tooltip>
                      {visibleCheckpoints.length > 0 && (
                        <Tooltip label="Checkpoint löschen">
                          <ActionIcon variant="light" color="red" disabled={!canManageHyperv} onClick={() => deleteVmCheckpoints(vm)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {avhdxVhds.length > 0 && (
                        <Tooltip label="VM Discovery -- Stand jetzt aktualisieren">
                          <ActionIcon
                            variant="light"
                            disabled={!canManageHyperv}
                            loading={discoverVm.isPending}
                            onClick={() => discoverVmNow(vm)}
                          >
                            <IconRefresh size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
            {(vms?.length ?? 0) > 0 && filteredVms.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Keine VM passt zur Suche „{vmSearch}“.
              </Text>
            )}
            </div>
          </Paper>
        </Tabs.Panel>

        <Tabs.Panel value="csv" pt="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          {selectedCsv && (
            <Box mb="md">
              <CsvChainHeader
                csv={selectedCsv}
                vms={vms}
                onClose={() => setSelectedCsv(null)}
                onVmClick={(vm) => {
                  setSelectedCsv(null);
                  setParams({ tab: "vms", vm: vm.id });
                }}
              />
            </Box>
          )}
          <Paper p="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            <Title order={5} mb="sm">
              Cluster Shared Volumes
            </Title>
            <Group justify="flex-start" mb="sm">
              <SearchInput value={csvSearch} onChange={setCsvSearch} />
            </Group>
            <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
            <Table striped highlightOnHover>
            <Table.Thead style={{ position: "sticky", top: 0, zIndex: 1, background: "var(--mantine-color-body)" }}>
              <Table.Tr>
                <Table.Th>Name</Table.Th>
                <Table.Th>Owner-Node</Table.Th>
                {sitesConfigured && <Table.Th>Standort</Table.Th>}
                <Table.Th>Status</Table.Th>
                <Table.Th>Pfad</Table.Th>
                <Table.Th>Größe</Table.Th>
                <Table.Th>Belegung</Table.Th>
                <Table.Th>LUN</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>Anzahl VMs</Table.Th>
                <Table.Th>Protection Group</Table.Th>
                <Table.Th>Protected</Table.Th>
                <Table.Th>Aktionen</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {filteredCsvs.map((csv) => {
                const usedPct =
                  csv.capacity_bytes && csv.capacity_bytes > 0
                    ? Math.round(((csv.used_bytes ?? 0) / csv.capacity_bytes) * 100)
                    : null;
                return (
                  <Table.Tr
                    key={csvIdentity(csv)}
                    onClick={() => toggleSelectedCsv(csv)}
                    style={{
                      cursor: "pointer",
                      backgroundColor: selectedCsv && csvIdentity(selectedCsv) === csvIdentity(csv) ? "var(--mantine-color-blue-light)" : undefined,
                    }}
                  >
                    <Table.Td>{csv.name}</Table.Td>
                    <Table.Td>{csv.owner_node}</Table.Td>
                    {sitesConfigured && (
                      <Table.Td>
                        {csv.site ? (
                          <Tooltip
                            label={csv.site_source === "override" ? "Manuell an dieser CSV festgelegt" : "Vom NetApp-System geerbt"}
                          >
                            <Group gap={4} wrap="nowrap">
                              <SiteBadgeView site={csv.site} />
                              {csv.site_source === "override" && (
                                <Text size="xs" c="dimmed">
                                  manuell
                                </Text>
                              )}
                            </Group>
                          </Tooltip>
                        ) : (
                          "-"
                        )}
                      </Table.Td>
                    )}
                    <Table.Td>
                      <Badge color="green" variant="light">
                        {csv.state}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{csv.volume_path}</Table.Td>
                    <Table.Td>{formatBytes(csv.capacity_bytes)}</Table.Td>
                    <Table.Td miw={160}>
                      {usedPct === null ? (
                        "-"
                      ) : (
                        <Stack gap={2}>
                          <Progress value={usedPct} color={usedPct >= 90 ? "red" : usedPct >= 75 ? "yellow" : "blue"} size="sm" />
                          <Group justify="space-between">
                            <Text size="xs" c="dimmed">
                              {formatBytes(csv.used_bytes)} / {formatBytes(csv.capacity_bytes)}
                            </Text>
                            <Text size="xs" c="dimmed">
                              {usedPct}%
                            </Text>
                          </Group>
                        </Stack>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {csv.lun_name ? (
                        <Tooltip label={csv.lun_name} openDelay={300}>
                          <span>{lunShortName(csv.lun_name)}</span>
                        </Tooltip>
                      ) : (
                        "-"
                      )}
                    </Table.Td>
                    <Table.Td>{csv.volume_name ?? "-"}</Table.Td>
                    <Table.Td>{vmsOnCsv(csv, vms).length}</Table.Td>
                    <Table.Td>
                      <ResourceGroupCell groups={csv.resource_group_names} policies={csv.policy_names} />
                    </Table.Td>
                    <Table.Td>
                      <ProtectedBadge protected={csv.protected} />
                    </Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                        <Tooltip label="Backup jetzt starten (CSV-Scope)">
                          <ActionIcon variant="light" disabled={!canRunBackup} onClick={() => runBackupNowForCsv(csv)}>
                            <IconBolt size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Details anzeigen">
                          <ActionIcon variant="light" onClick={() => setSelectedCsv(csv)}>
                            <IconInfoCircle size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Backups anzeigen">
                          <ActionIcon variant="light" onClick={() => showBackups("csv", csv.name, csv.cluster_id)}>
                            <IconHistory size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
          {(csvs?.length ?? 0) > 0 && filteredCsvs.length === 0 && (
            <Text c="dimmed" size="sm" ta="center" py="md">
              Kein CSV passt zur Suche „{csvSearch}".
            </Text>
          )}
          </div>
          </Paper>
        </Tabs.Panel>

        {(smbShares?.length ?? 0) > 0 && (
          <Tabs.Panel value="smb" pt="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
            {selectedSmbShare && (
              <Box mb="md">
                <SmbShareChainHeader
                  share={selectedSmbShare}
                  vms={vms}
                  onClose={() => setSelectedSmbShare(null)}
                  onVmClick={(vm) => {
                    setSelectedSmbShare(null);
                    setParams({ tab: "vms", vm: vm.id });
                  }}
                />
              </Box>
            )}
            <Paper p="md" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <Title order={5} mb="sm">
                SMB3-Freigaben
              </Title>
              <Group justify="flex-start" mb="sm">
                <SearchInput value={smbShareSearch} onChange={setSmbShareSearch} />
              </Group>
              <div style={{ flex: 1, minHeight: 0, overflowY: "auto" }}>
              <Table striped highlightOnHover>
              <Table.Thead style={{ position: "sticky", top: 0, zIndex: 1, background: "var(--mantine-color-body)" }}>
                <Table.Tr>
                  <Table.Th>Freigabe</Table.Th>
                  <Table.Th>Cluster</Table.Th>
                  <Table.Th>Größe</Table.Th>
                  <Table.Th>Belegung</Table.Th>
                  <Table.Th>Volume</Table.Th>
                  <Table.Th>SVM</Table.Th>
                  <Table.Th>Anzahl VMs</Table.Th>
                  <Table.Th>Protection Group</Table.Th>
                  <Table.Th>Protected</Table.Th>
                  <Table.Th>Aktionen</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {filteredSmbShares.map((share) => {
                  const usedPct =
                    share.capacity_bytes && share.capacity_bytes > 0
                      ? Math.round(((share.used_bytes ?? 0) / share.capacity_bytes) * 100)
                      : null;
                  return (
                    <Table.Tr
                      key={smbShareIdentity(share)}
                      onClick={() => toggleSelectedSmbShare(share)}
                      style={{
                        cursor: "pointer",
                        backgroundColor:
                          selectedSmbShare && smbShareIdentity(selectedSmbShare) === smbShareIdentity(share)
                            ? "var(--mantine-color-blue-light)"
                            : undefined,
                      }}
                    >
                      <Table.Td>{`\\\\${share.server}\\${share.share}`}</Table.Td>
                      <Table.Td>{share.hyperv_cluster_name ?? "-"}</Table.Td>
                      <Table.Td>{formatBytes(share.capacity_bytes)}</Table.Td>
                      <Table.Td miw={160}>
                        {usedPct === null ? (
                          "-"
                        ) : (
                          <Stack gap={2}>
                            <Progress value={usedPct} color={usedPct >= 90 ? "red" : usedPct >= 75 ? "yellow" : "blue"} size="sm" />
                            <Group justify="space-between">
                              <Text size="xs" c="dimmed">
                                {formatBytes(share.used_bytes)} / {formatBytes(share.capacity_bytes)}
                              </Text>
                              <Text size="xs" c="dimmed">
                                {usedPct}%
                              </Text>
                            </Group>
                          </Stack>
                        )}
                      </Table.Td>
                      <Table.Td>{share.volume_name ?? "-"}</Table.Td>
                      <Table.Td>{share.svm_name ?? "-"}</Table.Td>
                      <Table.Td>{vmsOnSmbShare(share, vms).length}</Table.Td>
                      <Table.Td>
                        <ResourceGroupCell groups={share.resource_group_names} policies={share.policy_names} />
                      </Table.Td>
                      <Table.Td>
                        <ProtectedBadge protected={share.protected} />
                      </Table.Td>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap" onClick={(e) => e.stopPropagation()}>
                          <Tooltip label="Backup jetzt starten (SMB3-Scope)">
                            <ActionIcon variant="light" disabled={!canRunBackup} onClick={() => runBackupNowForSmbShare(share)}>
                              <IconBolt size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Details anzeigen">
                            <ActionIcon variant="light" onClick={() => setSelectedSmbShare(share)}>
                              <IconInfoCircle size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Backups anzeigen">
                            <ActionIcon
                              variant="light"
                              onClick={() => showBackups("smb_share", `${share.server}|${share.share}`, share.cluster_id)}
                            >
                              <IconHistory size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
              </Table.Tbody>
            </Table>
            {(smbShares?.length ?? 0) > 0 && filteredSmbShares.length === 0 && (
              <Text c="dimmed" size="sm" ta="center" py="md">
                Keine SMB3-Freigabe passt zur Suche „{smbShareSearch}".
              </Text>
            )}
            </div>
            </Paper>
          </Tabs.Panel>
        )}
      </Tabs>

      <BackupsModal
        opened={!!backupsTarget}
        onClose={() => setBackupsTarget(null)}
        scope={backupsTarget?.scope ?? "vm"}
        name={backupsTarget?.name}
        clusterId={backupsTarget?.clusterId}
        onOpenRestoreWizard={
          backupsTarget?.scope === "vm"
            ? (snapshotId) => {
                setRestoreWizardTarget({ vmName: backupsTarget.name, snapshotId, clusterId: backupsTarget.clusterId });
                setBackupsTarget(null);
              }
            : undefined
        }
      />

      <RestoreWizardModal
        opened={!!restoreWizardTarget}
        onClose={() => setRestoreWizardTarget(null)}
        vm={
          restoreWizardTarget
            ? (() => {
                const vm = (vms ?? []).find(
                  (v) => v.name === restoreWizardTarget.vmName && v.cluster_id === restoreWizardTarget.clusterId,
                );
                return vm
                  ? {
                      name: vm.name,
                      host: vm.host,
                      state: vm.state,
                      cluster: vm.cluster,
                      cluster_id: vm.cluster_id,
                      backup_count: 0,
                      exists_in_inventory: true,
                    }
                  : null;
              })()
            : null
        }
        initialSnapshotId={restoreWizardTarget?.snapshotId}
      />

      <PolicyPickerModal
        opened={!!step1GroupChoices}
        onClose={closeStep1}
        policies={step1GroupChoices ?? []}
        onPick={pickStep1Group}
        title="Protection Group auswählen"
        description="Mehreren Protection Groups zugeordnet — für welche soll jetzt ein Backup gestartet werden?"
      />
      <PolicyPickerModal opened={!!step2PolicyChoices} onClose={closeStep2} policies={step2PolicyChoices ?? []} onPick={pickStep2Policy} />
    </Stack>
  );
}
