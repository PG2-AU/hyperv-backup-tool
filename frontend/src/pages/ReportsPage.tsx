import { useMemo, useState } from "react";
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Menu,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Tabs,
  TagsInput,
  Text,
  TextInput,
  Title,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconDeviceFloppy,
  IconDots,
  IconDownload,
  IconEdit,
  IconExternalLink,
  IconFileTypeCsv,
  IconFileTypePdf,
  IconMail,
  IconPlayerPlay,
  IconPlus,
  IconRefresh,
  IconTrash,
} from "@tabler/icons-react";
import { useSearchParams } from "react-router-dom";

import { useHyperVClusters, useNetAppClusters, usePolicies, useResourceGroups, useVms } from "@/api/hooks";
import {
  downloadReportFile,
  openReportPdf,
  useDeleteReportDefinition,
  useDeleteReportRun,
  useGenerateReport,
  useReportDefinitions,
  useReportOptions,
  useReportRuns,
  useRunReportDefinition,
  useSaveReportDefinition,
} from "@/api/hooks.reports";
import type { ReportDefinition, ReportDefinitionWrite, ReportOptions, ReportParams, ReportRun, ScheduleType } from "@/api/hooks.reports";
import { useSites } from "@/api/hooks.sites";
import { SearchInput } from "@/components/SearchInput";
import { useAuthStore } from "@/store/authStore";
import { confirmAction } from "@/utils/confirm";
import { apiErrorMessage } from "@/utils/errors";
import { formatBytes, formatDateTime } from "@/utils/format";
import { dedupeOptions } from "@/utils/selectOptions";
import { matchesAllColumns } from "@/utils/search";

// Reports (Backlog #84): "Neuer Report" (Auswahl je Typ, PDF im neuen Tab),
// "Gespeicherte Reports" (Vorlagen mit Zeitplan + Mailversand) und
// "Historie" (12 Monate, PDF/CSV erneut oeffnen).

const DEFAULT_PARAMS: Record<string, ReportParams> = {
  protection_status: { max_age_hours: 26, include_csv: true, include_smb: true, only_findings: false },
  backup_success: { period: "previous_month", detail: false },
  restore_points: { include_secondary: true, only_findings: false },
  restore_proof: { period: "previous_month", kinds: ["disk", "recreate", "file"], only_failed: false },
  capacity: { object_types: ["aggregate", "volume", "csv", "smb_share"], hyperv_only: true, warn_percent: 85, crit_percent: 95, only_findings: false },
  snapmirror: { max_copy_age_hours: 26, include_copies: true, only_findings: false },
  inventory: { include_csv: true, include_smb: true },
  audit: { period: "previous_month", include_runs: true, include_scheduled: false, include_logins: true },
};

const RESTORE_KINDS = [
  { value: "disk", label: "Disk-Restore" },
  { value: "recreate", label: "VM-Neuerstellung" },
  { value: "file", label: "Datei-Restore" },
];
const CAPACITY_TYPES = [
  { value: "aggregate", label: "Aggregate" },
  { value: "volume", label: "Volumes" },
  { value: "lun", label: "LUNs" },
  { value: "csv", label: "CSVs" },
  { value: "smb_share", label: "SMB3-Freigaben" },
];

const WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"];
const SCHEDULE_LABEL: Record<ScheduleType, string> = { none: "Kein Zeitplan", daily: "Täglich", weekly: "Wöchentlich", monthly: "Monatlich" };

function scheduleText(d: Pick<ReportDefinition, "schedule_type" | "schedule_day" | "schedule_time">): string {
  if (d.schedule_type === "daily") return `Täglich ${d.schedule_time}`;
  if (d.schedule_type === "weekly") return `${WEEKDAYS[d.schedule_day ?? 0]}s ${d.schedule_time}`;
  if (d.schedule_type === "monthly") return `Monatlich am ${d.schedule_day ?? 1}. um ${d.schedule_time}`;
  return "–";
}

function findingsBadge(run: ReportRun) {
  if (run.status !== "succeeded") return <Badge color="red" variant="light">Fehlgeschlagen</Badge>;
  return (
    <Tooltip label={run.findings_text ?? ""} disabled={!run.findings_text} multiline w={320}>
      <Badge color={run.findings ? "orange" : "green"} variant="light">
        {run.findings ? `${run.findings} Auffälligkeit(en)` : "Ohne Auffälligkeiten"}
      </Badge>
    </Tooltip>
  );
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? (value as string[]) : [];
}

// --- Auswahl je Report-Typ ------------------------------------------------------------------

function ReportParamsForm({
  reportType,
  value,
  onChange,
  options,
}: {
  reportType: string;
  value: ReportParams;
  onChange: (next: ReportParams) => void;
  options: ReportOptions;
}) {
  const { data: clusters = [] } = useHyperVClusters();
  const { data: groups = [] } = useResourceGroups();
  const { data: policies = [] } = usePolicies();
  const { data: sites = [] } = useSites();
  const { data: vms = [] } = useVms();
  const { data: netappClusters = [] } = useNetAppClusters();
  const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });
  const num = (key: string, fallback: number) => (value[key] == null || value[key] === "" ? fallback : Number(value[key]));
  const check = (key: string, label: string, defaultOn = false) => (
    <Checkbox label={label} checked={defaultOn ? value[key] !== false : !!value[key]} onChange={(e) => set(key, e.currentTarget.checked)} />
  );

  const period = String(value.period ?? "previous_month");
  const periodPicker = (
    <Group align="flex-end">
      <Select
        label="Zeitraum"
        data={Object.entries(options.periods).map(([v, label]) => ({ value: v, label }))}
        value={period}
        onChange={(v) => set("period", v ?? "previous_month")}
        allowDeselect={false}
        w={220}
      />
      {period === "custom" && (
        <>
          <TextInput label="von" type="date" value={String(value.period_from ?? "")} onChange={(e) => set("period_from", e.currentTarget.value)} />
          <TextInput label="bis (inklusive)" type="date" value={String(value.period_to ?? "")} onChange={(e) => set("period_to", e.currentTarget.value)} />
        </>
      )}
    </Group>
  );
  const siteSelect = (
    <MultiSelect
      label="Standorte"
      placeholder={asList(value.site_ids).length ? undefined : "Alle"}
      data={dedupeOptions(sites.map((s) => ({ value: s.id, label: s.name })))}
      value={asList(value.site_ids)}
      onChange={(v) => set("site_ids", v)}
      clearable
    />
  );
  const netappSelect = (
    <MultiSelect
      label="NetApp-Systeme"
      placeholder={asList(value.netapp_cluster_ids).length ? undefined : "Alle"}
      data={dedupeOptions(netappClusters.map((c) => ({ value: c.id, label: c.name })))}
      value={asList(value.netapp_cluster_ids)}
      onChange={(v) => set("netapp_cluster_ids", v)}
      clearable
    />
  );
  const vmSelect = (
    <MultiSelect
      label="VMs"
      placeholder={asList(value.vm_names).length ? undefined : "Alle"}
      data={dedupeOptions(vms.map((v) => ({ value: v.name, label: v.name })))}
      value={asList(value.vm_names)}
      onChange={(v) => set("vm_names", v)}
      searchable
      clearable
    />
  );

  const clusterSelect = (
    <MultiSelect
      label="Hyper-V-Cluster"
      placeholder={asList(value.cluster_ids).length ? undefined : "Alle"}
      data={dedupeOptions(clusters.map((c) => ({ value: c.id, label: c.name })))}
      value={asList(value.cluster_ids)}
      onChange={(v) => set("cluster_ids", v)}
      clearable
    />
  );
  const groupSelect = (
    <MultiSelect
      label="Protection Groups"
      placeholder={asList(value.resource_group_ids).length ? undefined : "Alle"}
      data={dedupeOptions(groups.map((g) => ({ value: g.id, label: g.name })))}
      value={asList(value.resource_group_ids)}
      onChange={(v) => set("resource_group_ids", v)}
      searchable
      clearable
    />
  );

  if (reportType === "protection_status") {
    return (
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, md: 3 }}>
          {clusterSelect}
          {siteSelect}
          {groupSelect}
        </SimpleGrid>
        <NumberInput
          label="Überfällig ab (Stunden ohne Backup)"
          description="Ein geschütztes Objekt ohne Backup innerhalb dieser Zeit gilt als überfällig."
          min={1}
          max={24 * 90}
          w={320}
          value={Number(value.max_age_hours ?? 26)}
          onChange={(v) => set("max_age_hours", typeof v === "number" ? v : 26)}
        />
        <Group>
          <Checkbox label="CSVs einbeziehen" checked={value.include_csv !== false} onChange={(e) => set("include_csv", e.currentTarget.checked)} />
          <Checkbox label="SMB3-Freigaben einbeziehen" checked={value.include_smb !== false} onChange={(e) => set("include_smb", e.currentTarget.checked)} />
          <Checkbox label="Nur Auffälligkeiten auflisten" checked={!!value.only_findings} onChange={(e) => set("only_findings", e.currentTarget.checked)} />
        </Group>
      </Stack>
    );
  }

  if (reportType === "backup_success") {
    return (
      <Stack gap="sm">
        {periodPicker}
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <MultiSelect
            label="Policies"
            placeholder={asList(value.policy_ids).length ? undefined : "Alle"}
            data={dedupeOptions(policies.map((p) => ({ value: p.id, label: p.name })))}
            value={asList(value.policy_ids)}
            onChange={(v) => set("policy_ids", v)}
            searchable
            clearable
          />
          {groupSelect}
        </SimpleGrid>
        <Checkbox
          label="Alle Einzelläufe auflisten (sonst nur Übersicht und Problemfälle)"
          checked={!!value.detail}
          onChange={(e) => set("detail", e.currentTarget.checked)}
        />
        <Text size="xs" c="dimmed">
          Der Report vergleicht automatisch mit dem gleich langen Vorzeitraum. Die CSV-Datei enthält immer alle Einzelläufe.
        </Text>
      </Stack>
    );
  }

  if (reportType === "restore_points") {
    return (
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, md: 3 }}>
          {clusterSelect}
          {groupSelect}
          {vmSelect}
        </SimpleGrid>
        <Group>
          <Checkbox
            label="Sekundäre Punkte (SnapMirror-Ziel) einbeziehen"
            checked={value.include_secondary !== false}
            onChange={(e) => set("include_secondary", e.currentTarget.checked)}
          />
          <Checkbox label="Nur VMs ohne Wiederherstellungspunkt" checked={!!value.only_findings} onChange={(e) => set("only_findings", e.currentTarget.checked)} />
        </Group>
      </Stack>
    );
  }

  if (reportType === "restore_proof") {
    return (
      <Stack gap="sm">
        {periodPicker}
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <MultiSelect
            label="Art"
            data={RESTORE_KINDS}
            value={asList(value.kinds).length ? asList(value.kinds) : RESTORE_KINDS.map((k) => k.value)}
            onChange={(v) => set("kinds", v)}
          />
          {vmSelect}
        </SimpleGrid>
        {check("only_failed", "Nur fehlgeschlagene Wiederherstellungen auflisten")}
        <Text size="xs" c="dimmed">
          Vergleicht mit dem gleich langen Vorzeitraum. Als Nachweis für Audits geeignet: wer hat wann welche VM aus welchem Backup-Stand wiederhergestellt.
        </Text>
      </Stack>
    );
  }

  if (reportType === "capacity") {
    return (
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          <MultiSelect
            label="Objekte"
            data={CAPACITY_TYPES}
            value={asList(value.object_types).length ? asList(value.object_types) : ["aggregate", "volume", "csv", "smb_share"]}
            onChange={(v) => set("object_types", v)}
          />
          {netappSelect}
        </SimpleGrid>
        <Group align="flex-end">
          <NumberInput label="Beobachten ab (% belegt)" min={1} max={100} w={200} value={num("warn_percent", 85)} onChange={(v) => set("warn_percent", v)} />
          <NumberInput label="Kritisch ab (% belegt)" min={1} max={100} w={200} value={num("crit_percent", 95)} onChange={(v) => set("crit_percent", v)} />
        </Group>
        <Group>
          {check("hyperv_only", "Nur von Hyper-V genutzte Volumes/LUNs", true)}
          {check("only_findings", "Nur Auffälligkeiten auflisten")}
        </Group>
        <Text size="xs" c="dimmed">
          Prognose wie im Kapazitätsverlauf (Trend der letzten 30 Tage). Kritisch zusätzlich, wenn ein Objekt in ≤ 28 Tagen vollläuft, beobachten bei ≤ 90 Tagen.
        </Text>
      </Stack>
    );
  }

  if (reportType === "snapmirror") {
    return (
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, md: 2 }}>{netappSelect}</SimpleGrid>
        <Group align="flex-end">
          <NumberInput
            label="Lag-Schwellwert (Stunden)"
            description="Leer = Wert aus Settings > Alarms"
            min={1}
            w={240}
            value={value.lag_hours == null ? "" : Number(value.lag_hours)}
            onChange={(v) => set("lag_hours", v === "" ? null : v)}
          />
          <NumberInput
            label="Kopie veraltet ab (Stunden)"
            description="jüngste Backup-Kopie auf dem Ziel"
            min={1}
            w={240}
            value={num("max_copy_age_hours", 26)}
            onChange={(v) => set("max_copy_age_hours", v)}
          />
        </Group>
        <Group>
          {check("include_copies", "Backup-Kopien je Volume auflisten", true)}
          {check("only_findings", "Nur Auffälligkeiten auflisten")}
        </Group>
      </Stack>
    );
  }

  if (reportType === "inventory") {
    return (
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 1, md: 2 }}>
          {clusterSelect}
          {siteSelect}
        </SimpleGrid>
        <Group>
          {check("include_csv", "CSVs einbeziehen", true)}
          {check("include_smb", "SMB3-Freigaben einbeziehen", true)}
        </Group>
        <Text size="xs" c="dimmed">
          Stand der letzten Discovery. Die CSV-Datei enthält die VM-Liste.
        </Text>
      </Stack>
    );
  }

  if (reportType === "audit") {
    return (
      <Stack gap="sm">
        {periodPicker}
        <TagsInput
          label="Benutzer"
          description="Leer = alle. Name eingeben und mit Enter bestätigen."
          value={asList(value.usernames)}
          onChange={(v) => set("usernames", v)}
          clearable
        />
        <Group>
          {check("include_runs", "Abläufe einbeziehen (Restore, VM/CSV anlegen, verschieben …)", true)}
          {check("include_scheduled", "auch geplante Backups")}
          {check("include_logins", "Anmeldungen einbeziehen", true)}
        </Group>
        <Text size="xs" c="dimmed">
          Jede Änderung über die App wird seit diesem Update vollständig protokolliert (wer, wann, was, Ergebnis). Für die Zeit davor enthält der Report
          nur die Einträge des System-Logs, die einen Benutzer nennen.
        </Text>
      </Stack>
    );
  }
  return null;
}

function paramsError(reportType: string, params: ReportParams): string | null {
  if (params.period === "custom") {
    if (!params.period_from || !params.period_to) return "Bitte Beginn und Ende des Zeitraums angeben.";
    if (String(params.period_from) > String(params.period_to)) return "Der Beginn liegt nach dem Ende.";
  }
  if (reportType === "restore_proof" && Array.isArray(params.kinds) && params.kinds.length === 0) return "Bitte mindestens eine Art wählen.";
  if (reportType === "capacity") {
    if (Array.isArray(params.object_types) && params.object_types.length === 0) return "Bitte mindestens einen Objekttyp wählen.";
    if (Number(params.crit_percent ?? 95) < Number(params.warn_percent ?? 85)) return "„Kritisch ab“ muss mindestens so hoch sein wie „Beobachten ab“.";
  }
  return null;
}

// --- Vorlage anlegen / bearbeiten -------------------------------------------------------------

function DefinitionModal({
  opened,
  onClose,
  initial,
  editId,
  options,
}: {
  opened: boolean;
  onClose: (saved: boolean) => void;
  initial: ReportDefinitionWrite;
  editId?: string;
  options: ReportOptions;
}) {
  const [form, setForm] = useState<ReportDefinitionWrite>(initial);
  const save = useSaveReportDefinition();
  const set = <K extends keyof ReportDefinitionWrite>(key: K, v: ReportDefinitionWrite[K]) => setForm((f) => ({ ...f, [key]: v }));
  const error = paramsError(form.report_type, form.params);

  function submit() {
    save.mutate(
      { id: editId, payload: { ...form, name: form.name.trim() } },
      {
        onSuccess: () => {
          notifications.show({ message: editId ? "Vorlage gespeichert." : "Vorlage angelegt.", color: "green" });
          onClose(true);
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Speichern fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={() => onClose(false)} title={editId ? "Report-Vorlage bearbeiten" : "Report-Vorlage anlegen"} size="xl">
      <Stack>
        <Group grow align="flex-start">
          <TextInput label="Name" required value={form.name} onChange={(e) => set("name", e.currentTarget.value)} placeholder="z.B. Schutzstatus monatlich" />
          <Select
            label="Report"
            data={options.types.map((t) => ({ value: t.type, label: t.label }))}
            value={form.report_type}
            onChange={(v) => v && setForm((f) => ({ ...f, report_type: v, params: { ...(DEFAULT_PARAMS[v] ?? {}) } }))}
            allowDeselect={false}
            disabled={!!editId}
          />
        </Group>
        <Paper withBorder p="sm">
          <Text fw={500} size="sm" mb="xs">
            Auswahl
          </Text>
          <ReportParamsForm reportType={form.report_type} value={form.params} onChange={(p) => set("params", p)} options={options} />
        </Paper>
        <Paper withBorder p="sm">
          <Text fw={500} size="sm" mb="xs">
            Zeitplan und Versand
          </Text>
          <Stack gap="sm">
            <Group align="flex-end">
              <Select
                label="Zeitplan"
                data={(Object.keys(SCHEDULE_LABEL) as ScheduleType[]).map((v) => ({ value: v, label: SCHEDULE_LABEL[v] }))}
                value={form.schedule_type}
                onChange={(v) => {
                  const type = (v ?? "none") as ScheduleType;
                  setForm((f) => ({ ...f, schedule_type: type, schedule_day: type === "weekly" ? 0 : type === "monthly" ? 1 : null }));
                }}
                allowDeselect={false}
                w={180}
              />
              {form.schedule_type === "weekly" && (
                <Select
                  label="Wochentag"
                  data={WEEKDAYS.map((label, i) => ({ value: String(i), label }))}
                  value={String(form.schedule_day ?? 0)}
                  onChange={(v) => set("schedule_day", Number(v ?? 0))}
                  allowDeselect={false}
                  w={160}
                />
              )}
              {form.schedule_type === "monthly" && (
                <NumberInput
                  label="Tag des Monats"
                  min={1}
                  max={28}
                  value={form.schedule_day ?? 1}
                  onChange={(v) => set("schedule_day", typeof v === "number" ? v : 1)}
                  w={140}
                />
              )}
              {form.schedule_type !== "none" && (
                <TextInput label="Uhrzeit" type="time" value={form.schedule_time} onChange={(e) => set("schedule_time", e.currentTarget.value)} w={120} />
              )}
            </Group>
            {form.schedule_type !== "none" && (
              <Text size="xs" c="dimmed">
                Zeitzone {options.timezone}. Ein verpasster Termin (App war aus) wird beim nächsten Start nachgeholt. Monatlich nur bis zum 28., damit jeder Monat
                den Termin hat.
              </Text>
            )}
            <TagsInput
              label="E-Mail-Empfänger"
              description="Adresse eingeben und mit Enter bestätigen. Ohne Empfänger wird der Report nur in der Historie abgelegt."
              value={form.recipients}
              onChange={(v) => set("recipients", v)}
              splitChars={[",", ";", " "]}
              clearable
            />
            <Group>
              <Checkbox
                label="Nur senden, wenn es Auffälligkeiten gibt"
                checked={form.only_if_findings}
                onChange={(e) => set("only_if_findings", e.currentTarget.checked)}
              />
              <Checkbox label="CSV-Datei mitsenden" checked={form.attach_csv} onChange={(e) => set("attach_csv", e.currentTarget.checked)} />
              <Switch label="Aktiv" checked={form.enabled} onChange={(e) => set("enabled", e.currentTarget.checked)} />
            </Group>
          </Stack>
        </Paper>
        {error && <Alert color="yellow">{error}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={() => onClose(false)}>
            Abbrechen
          </Button>
          <Button leftSection={<IconDeviceFloppy size={16} />} onClick={submit} loading={save.isPending} disabled={!form.name.trim() || !!error}>
            Speichern
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// --- Tab "Neuer Report" ---------------------------------------------------------------------------

function NewReportTab({ options, onSaveAsTemplate }: { options: ReportOptions; onSaveAsTemplate: (type: string, params: ReportParams) => void }) {
  const canManage = useAuthStore((s) => s.hasPermission)("report:manage");
  const [reportType, setReportType] = useState(options.types[0]?.type ?? "protection_status");
  const [paramsByType, setParamsByType] = useState<Record<string, ReportParams>>(() => structuredClone(DEFAULT_PARAMS));
  const generate = useGenerateReport();
  const params = paramsByType[reportType] ?? {};
  const info = options.types.find((t) => t.type === reportType);
  const error = paramsError(reportType, params);

  function run() {
    // Fenster jetzt im Klick oeffnen, sonst blockt der Popup-Blocker.
    const win = window.open("", "_blank");
    generate.mutate(
      { report_type: reportType, params },
      {
        onSuccess: (result) =>
          openReportPdf(result.id, win).catch((err) =>
            notifications.show({ title: "Fehler", message: apiErrorMessage(err, "PDF konnte nicht geöffnet werden."), color: "red" }),
          ),
        onError: (err) => {
          win?.close();
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Report konnte nicht erstellt werden."), color: "red" });
        },
      },
    );
  }

  return (
    <Stack>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }}>
        {options.types.map((t) => (
          <Paper
            key={t.type}
            withBorder
            p="sm"
            style={{
              cursor: "pointer",
              borderColor: t.type === reportType ? "var(--mantine-color-blue-filled)" : undefined,
              borderWidth: t.type === reportType ? 2 : undefined,
            }}
            onClick={() => setReportType(t.type)}
          >
            <Text fw={600}>{t.label}</Text>
            <Text size="xs" c="dimmed">
              {t.description}
            </Text>
          </Paper>
        ))}
      </SimpleGrid>
      <Paper withBorder p="md">
        <Title order={5} mb="sm">
          Auswahl: {info?.label}
        </Title>
        <ReportParamsForm
          reportType={reportType}
          value={params}
          onChange={(p) => setParamsByType((all) => ({ ...all, [reportType]: p }))}
          options={options}
        />
        {error && (
          <Alert color="yellow" mt="sm">
            {error}
          </Alert>
        )}
        <Group mt="md">
          <Button leftSection={<IconFileTypePdf size={16} />} onClick={run} loading={generate.isPending} disabled={!!error}>
            Report erstellen
          </Button>
          {canManage && (
            <Button variant="default" leftSection={<IconDeviceFloppy size={16} />} onClick={() => onSaveAsTemplate(reportType, params)} disabled={!!error}>
              Als Vorlage speichern …
            </Button>
          )}
          <Text size="xs" c="dimmed">
            Das PDF öffnet sich in einem neuen Tab und liegt anschließend 12 Monate in der Historie.
          </Text>
        </Group>
      </Paper>
    </Stack>
  );
}

// --- Tab "Gespeicherte Reports" ---------------------------------------------------------------

function DefinitionsTab({ options, onEdit, onCreate }: { options: ReportOptions; onEdit: (d: ReportDefinition) => void; onCreate: () => void }) {
  const canManage = useAuthStore((s) => s.hasPermission)("report:manage");
  const { data: definitions = [], isLoading, refetch } = useReportDefinitions();
  const runDefinition = useRunReportDefinition();
  const deleteDefinition = useDeleteReportDefinition();
  const [busy, setBusy] = useState<string | null>(null);
  const typeLabel = (type: string) => options.types.find((t) => t.type === type)?.label ?? type;

  function runNow(d: ReportDefinition, send: boolean) {
    const win = send ? null : window.open("", "_blank");
    setBusy(d.id);
    runDefinition.mutate(
      { id: d.id, send },
      {
        onSuccess: (result) => {
          if (send) notifications.show({ message: `Report erstellt und an ${d.recipients.join(", ")} gesendet.`, color: "green" });
          else
            openReportPdf(result.id, win).catch((err) =>
              notifications.show({ title: "Fehler", message: apiErrorMessage(err, "PDF konnte nicht geöffnet werden."), color: "red" }),
            );
        },
        onError: (err) => {
          win?.close();
          notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Report fehlgeschlagen."), color: "red" });
        },
        onSettled: () => setBusy(null),
      },
    );
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          Vorlagen speichern Typ und Auswahl eines Reports; mit Zeitplan werden sie automatisch erstellt und an die Empfänger gesendet.
        </Text>
        <Group>
          <ActionIcon variant="default" size="lg" onClick={() => refetch()} aria-label="Aktualisieren">
            <IconRefresh size={16} />
          </ActionIcon>
          {canManage && (
            <Button leftSection={<IconPlus size={16} />} onClick={onCreate}>
              Neue Vorlage
            </Button>
          )}
        </Group>
      </Group>
      <Paper withBorder>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Name</Table.Th>
              <Table.Th>Report</Table.Th>
              <Table.Th>Zeitplan</Table.Th>
              <Table.Th>Empfänger</Table.Th>
              <Table.Th>Nächster Lauf</Table.Th>
              <Table.Th>Letzter Lauf</Table.Th>
              <Table.Th w={60} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {definitions.map((d) => (
              <Table.Tr key={d.id} style={{ opacity: d.enabled ? 1 : 0.55 }}>
                <Table.Td>
                  <Text size="sm" fw={500}>
                    {d.name}
                  </Text>
                  {!d.enabled && (
                    <Badge size="xs" color="gray" variant="light">
                      inaktiv
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{typeLabel(d.report_type)}</Table.Td>
                <Table.Td>{scheduleText(d)}</Table.Td>
                <Table.Td>
                  <Text size="sm">{d.recipients.length ? d.recipients.join(", ") : "–"}</Text>
                  {d.recipients.length > 0 && (d.only_if_findings || d.attach_csv) && (
                    <Text size="xs" c="dimmed">
                      {[d.only_if_findings && "nur bei Auffälligkeiten", d.attach_csv && "mit CSV"].filter(Boolean).join(", ")}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{formatDateTime(d.next_run_at)}</Table.Td>
                <Table.Td>
                  {d.last_run ? (
                    <Group gap={6} wrap="nowrap">
                      <Text size="sm">{formatDateTime(d.last_run.created_at)}</Text>
                      {findingsBadge(d.last_run)}
                    </Group>
                  ) : (
                    "–"
                  )}
                </Table.Td>
                <Table.Td>
                  <Menu position="bottom-end" withinPortal>
                    <Menu.Target>
                      <ActionIcon variant="subtle" loading={busy === d.id} aria-label="Aktionen">
                        <IconDots size={16} />
                      </ActionIcon>
                    </Menu.Target>
                    <Menu.Dropdown>
                      {d.last_run?.status === "succeeded" && (
                        <Menu.Item
                          leftSection={<IconExternalLink size={14} />}
                          onClick={() =>
                            openReportPdf(d.last_run!.id).catch((err) =>
                              notifications.show({ title: "Fehler", message: apiErrorMessage(err, "PDF konnte nicht geöffnet werden."), color: "red" }),
                            )
                          }
                        >
                          Letzten Report öffnen
                        </Menu.Item>
                      )}
                      {canManage && (
                        <>
                          <Menu.Item leftSection={<IconPlayerPlay size={14} />} onClick={() => runNow(d, false)}>
                            Jetzt erstellen
                          </Menu.Item>
                          <Menu.Item leftSection={<IconMail size={14} />} disabled={!d.recipients.length} onClick={() => runNow(d, true)}>
                            Jetzt erstellen und senden
                          </Menu.Item>
                          <Menu.Item leftSection={<IconEdit size={14} />} onClick={() => onEdit(d)}>
                            Bearbeiten
                          </Menu.Item>
                          <Menu.Divider />
                          <Menu.Item
                            color="red"
                            leftSection={<IconTrash size={14} />}
                            onClick={() =>
                              confirmAction({
                                title: "Vorlage löschen",
                                message: `Vorlage "${d.name}" löschen? Bereits erstellte Reports bleiben in der Historie.`,
                                confirmLabel: "Löschen",
                                onConfirm: () =>
                                  deleteDefinition.mutate(d.id, {
                                    onError: (err) =>
                                      notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Löschen fehlgeschlagen."), color: "red" }),
                                  }),
                              })
                            }
                          >
                            Löschen
                          </Menu.Item>
                        </>
                      )}
                    </Menu.Dropdown>
                  </Menu>
                </Table.Td>
              </Table.Tr>
            ))}
            {!isLoading && definitions.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={7}>
                  <Text size="sm" c="dimmed" ta="center" py="md">
                    Noch keine Vorlagen. Unter „Neuer Report“ eine Auswahl treffen und „Als Vorlage speichern“ wählen.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Paper>
    </Stack>
  );
}

// --- Tab "Historie" -----------------------------------------------------------------------------

function HistoryTab({ options }: { options: ReportOptions }) {
  const canManage = useAuthStore((s) => s.hasPermission)("report:manage");
  const { data: runs = [], isLoading, refetch } = useReportRuns();
  const deleteRun = useDeleteReportRun();
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const typeLabel = (type: string) => options.types.find((t) => t.type === type)?.label ?? type;

  const rows = useMemo(
    () =>
      runs.filter(
        (r) =>
          (!typeFilter || r.report_type === typeFilter) &&
          matchesAllColumns(
            { title: r.title, subtitle: r.subtitle, definition: r.definition_name, by: r.created_by, findings: r.findings_text, to: r.emailed_to.join(" ") },
            search,
          ),
      ),
    [runs, search, typeFilter],
  );

  const fail = (err: unknown) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Datei konnte nicht geladen werden."), color: "red" });

  return (
    <Stack>
      <Group justify="space-between">
        <Group>
          <SearchInput value={search} onChange={setSearch} />
          <Select
            placeholder="Alle Reports"
            data={options.types.map((t) => ({ value: t.type, label: t.label }))}
            value={typeFilter}
            onChange={setTypeFilter}
            clearable
            w={220}
          />
        </Group>
        <Group>
          <Text size="xs" c="dimmed">
            Reports werden 12 Monate aufbewahrt.
          </Text>
          <ActionIcon variant="default" size="lg" onClick={() => refetch()} aria-label="Aktualisieren">
            <IconRefresh size={16} />
          </ActionIcon>
        </Group>
      </Group>
      <Paper withBorder>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Erstellt</Table.Th>
              <Table.Th>Report</Table.Th>
              <Table.Th>Ergebnis</Table.Th>
              <Table.Th>Erstellt von</Table.Th>
              <Table.Th>Versand</Table.Th>
              <Table.Th>Größe</Table.Th>
              <Table.Th w={120} />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{formatDateTime(r.created_at)}</Table.Td>
                <Table.Td>
                  <Text size="sm" fw={500}>
                    {r.definition_name ? `${r.definition_name} (${typeLabel(r.report_type)})` : typeLabel(r.report_type)}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {r.status === "succeeded" ? r.subtitle : r.error_message}
                  </Text>
                </Table.Td>
                <Table.Td>{findingsBadge(r)}</Table.Td>
                <Table.Td>{r.created_by ?? "–"}</Table.Td>
                <Table.Td>
                  {r.email_error ? (
                    <Tooltip label={r.email_error} multiline w={360}>
                      <Badge color="red" variant="light">
                        Versand fehlgeschlagen
                      </Badge>
                    </Tooltip>
                  ) : r.emailed_to.length ? (
                    <Text size="xs">{r.emailed_to.join(", ")}</Text>
                  ) : (
                    "–"
                  )}
                </Table.Td>
                <Table.Td style={{ whiteSpace: "nowrap" }}>{r.size_bytes ? formatBytes(r.size_bytes) : "–"}</Table.Td>
                <Table.Td>
                  {r.status === "succeeded" && (
                    <Group gap={4} wrap="nowrap">
                      <Tooltip label="PDF in neuem Tab öffnen">
                        <ActionIcon variant="subtle" onClick={() => openReportPdf(r.id).catch(fail)} aria-label="Öffnen">
                          <IconExternalLink size={16} />
                        </ActionIcon>
                      </Tooltip>
                      <Menu position="bottom-end" withinPortal>
                        <Menu.Target>
                          <ActionIcon variant="subtle" aria-label="Herunterladen">
                            <IconDownload size={16} />
                          </ActionIcon>
                        </Menu.Target>
                        <Menu.Dropdown>
                          <Menu.Item leftSection={<IconFileTypePdf size={14} />} onClick={() => downloadReportFile(r.id, "pdf").catch(fail)}>
                            PDF herunterladen
                          </Menu.Item>
                          <Menu.Item leftSection={<IconFileTypeCsv size={14} />} disabled={!r.has_csv} onClick={() => downloadReportFile(r.id, "csv").catch(fail)}>
                            CSV herunterladen
                          </Menu.Item>
                          {r.file_sha256 && (
                            <>
                              <Menu.Divider />
                              <Menu.Label style={{ wordBreak: "break-all", maxWidth: 280 }}>Datei-SHA-256: {r.file_sha256}</Menu.Label>
                            </>
                          )}
                        </Menu.Dropdown>
                      </Menu>
                      {canManage && (
                        <Tooltip label="Aus der Historie löschen">
                          <ActionIcon
                            variant="subtle"
                            color="red"
                            aria-label="Löschen"
                            onClick={() =>
                              confirmAction({
                                title: "Report löschen",
                                message: "Diesen Report (PDF und CSV) endgültig aus der Historie löschen?",
                                confirmLabel: "Löschen",
                                onConfirm: () => deleteRun.mutate(r.id, { onError: fail }),
                              })
                            }
                          >
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  )}
                  {r.status !== "succeeded" && canManage && (
                    <ActionIcon variant="subtle" color="red" aria-label="Löschen" onClick={() => deleteRun.mutate(r.id, { onError: fail })}>
                      <IconTrash size={16} />
                    </ActionIcon>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={7}>
                  <Text size="sm" c="dimmed" ta="center" py="md">
                    {runs.length ? "Keine Treffer." : "Noch keine Reports erstellt."}
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Paper>
    </Stack>
  );
}

// --- Seite --------------------------------------------------------------------------------------

const EMPTY_DEFINITION: ReportDefinitionWrite = {
  name: "",
  report_type: "protection_status",
  params: DEFAULT_PARAMS.protection_status,
  schedule_type: "monthly",
  schedule_day: 1,
  schedule_time: "06:00",
  recipients: [],
  only_if_findings: false,
  attach_csv: false,
  enabled: true,
};

export function ReportsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") ?? "new";
  const { data: options, error } = useReportOptions();
  // Modal-Zustand: key erzwingt frisches Formular je Oeffnen
  const [modal, setModal] = useState<{ key: number; initial: ReportDefinitionWrite; editId?: string } | null>(null);

  const openModal = (initial: ReportDefinitionWrite, editId?: string) => setModal({ key: Date.now(), initial, editId });

  return (
    <Stack>
      <Title order={2}>Reports</Title>
      {error && <Alert color="red">{apiErrorMessage(error, "Reports konnten nicht geladen werden.")}</Alert>}
      {options && (
        <Tabs value={tab} onChange={(v) => setSearchParams({ tab: v ?? "new" })}>
          <Tabs.List>
            <Tabs.Tab value="new">Neuer Report</Tabs.Tab>
            <Tabs.Tab value="saved">Gespeicherte Reports</Tabs.Tab>
            <Tabs.Tab value="history">Historie</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="new" pt="md">
            <NewReportTab
              options={options}
              onSaveAsTemplate={(type, params) => {
                const label = options.types.find((t) => t.type === type)?.label ?? "";
                openModal({ ...EMPTY_DEFINITION, name: `${label} monatlich`, report_type: type, params: structuredClone(params) });
              }}
            />
          </Tabs.Panel>
          <Tabs.Panel value="saved" pt="md">
            <DefinitionsTab
              options={options}
              onCreate={() => openModal(structuredClone(EMPTY_DEFINITION))}
              onEdit={(d) =>
                openModal(
                  {
                    name: d.name,
                    report_type: d.report_type,
                    params: structuredClone(d.params),
                    schedule_type: d.schedule_type,
                    schedule_day: d.schedule_day,
                    schedule_time: d.schedule_time,
                    recipients: [...d.recipients],
                    only_if_findings: d.only_if_findings,
                    attach_csv: d.attach_csv,
                    enabled: d.enabled,
                  },
                  d.id,
                )
              }
            />
          </Tabs.Panel>
          <Tabs.Panel value="history" pt="md">
            <HistoryTab options={options} />
          </Tabs.Panel>
        </Tabs>
      )}
      {options && modal && (
        <DefinitionModal
          key={modal.key}
          opened
          onClose={(saved) => {
            setModal(null);
            if (saved && tab !== "saved") setSearchParams({ tab: "saved" });
          }}
          initial={modal.initial}
          editId={modal.editId}
          options={options}
        />
      )}
    </Stack>
  );
}
