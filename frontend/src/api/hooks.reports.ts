import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// Reports (backend app.api.routes.reports, Backlog #84).

export type ReportParams = Record<string, unknown>;
export type ScheduleType = "none" | "daily" | "weekly" | "monthly";

export interface ReportTypeInfo {
  type: string;
  label: string;
  description: string;
  uses_period: boolean;
}

export interface ReportOptions {
  types: ReportTypeInfo[];
  periods: Record<string, string>;
  timezone: string;
}

export interface ReportRun {
  id: string;
  definition_id?: string | null;
  definition_name?: string | null;
  report_type: string;
  title: string;
  subtitle?: string | null;
  params: ReportParams;
  created_by?: string | null;
  created_at: string;
  status: string;
  error_message?: string | null;
  findings: number;
  findings_text?: string | null;
  size_bytes?: number | null;
  has_csv: boolean;
  file_sha256?: string | null;
  content_sha256?: string | null;
  emailed_to: string[];
  email_error?: string | null;
}

export interface ReportDefinitionWrite {
  name: string;
  report_type: string;
  params: ReportParams;
  schedule_type: ScheduleType;
  schedule_day?: number | null;
  schedule_time: string;
  recipients: string[];
  only_if_findings: boolean;
  attach_csv: boolean;
  enabled: boolean;
}

export interface ReportDefinition extends ReportDefinitionWrite {
  id: string;
  created_by?: string | null;
  created_at: string;
  last_scheduled_for?: string | null;
  next_run_at?: string | null;
  last_run?: ReportRun | null;
}

export function useReportOptions() {
  return useQuery({
    queryKey: ["report-options"],
    queryFn: async () => (await apiClient.get<ReportOptions>("/reports/options")).data,
    staleTime: Infinity,
  });
}

export function useReportRuns() {
  return useQuery({
    queryKey: ["report-runs"],
    queryFn: async () => (await apiClient.get<ReportRun[]>("/reports/runs")).data,
  });
}

export function useReportDefinitions() {
  return useQuery({
    queryKey: ["report-definitions"],
    queryFn: async () => (await apiClient.get<ReportDefinition[]>("/reports/definitions")).data,
  });
}

function useInvalidateReports() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["report-runs"] });
    queryClient.invalidateQueries({ queryKey: ["report-definitions"] });
  };
}

export function useGenerateReport() {
  const invalidate = useInvalidateReports();
  return useMutation({
    mutationFn: async (payload: { report_type: string; params: ReportParams }) =>
      (await apiClient.post<ReportRun>("/reports/generate", payload)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteReportRun() {
  const invalidate = useInvalidateReports();
  return useMutation({
    mutationFn: async (id: string) => apiClient.delete(`/reports/runs/${id}`),
    onSuccess: invalidate,
  });
}

export function useSaveReportDefinition() {
  const invalidate = useInvalidateReports();
  return useMutation({
    mutationFn: async ({ id, payload }: { id?: string; payload: ReportDefinitionWrite }) =>
      id
        ? (await apiClient.put<ReportDefinition>(`/reports/definitions/${id}`, payload)).data
        : (await apiClient.post<ReportDefinition>("/reports/definitions", payload)).data,
    onSuccess: invalidate,
  });
}

export function useDeleteReportDefinition() {
  const invalidate = useInvalidateReports();
  return useMutation({
    mutationFn: async (id: string) => apiClient.delete(`/reports/definitions/${id}`),
    onSuccess: invalidate,
  });
}

export function useRunReportDefinition() {
  const invalidate = useInvalidateReports();
  return useMutation({
    mutationFn: async ({ id, send }: { id: string; send: boolean }) =>
      (await apiClient.post<ReportRun>(`/reports/definitions/${id}/run`, { send })).data,
    // auch bei Fehler: ein Versandfehler hinterlaesst trotzdem einen Report in der Historie
    onSettled: invalidate,
  });
}

// Die Dateien brauchen den Bearer-Token, daher per apiClient als Blob statt
// als einfacher Link.

/** PDF in einem neuen Browser-Tab oeffnen. Das Fenster wird synchron im
 *  Klick geoeffnet (sonst blockt der Popup-Blocker) und erst nach dem Laden
 *  auf das PDF umgeleitet. Ein bereits geoeffnetes Fenster kann uebergeben
 *  werden (z.B. vor dem Erzeugen eines neuen Reports geoeffnet). */
export async function openReportPdf(id: string, target?: Window | null): Promise<void> {
  const win = target ?? window.open("", "_blank");
  if (win) {
    win.document.title = "Report wird geladen …";
    win.document.body.innerHTML = "<p style='font-family:sans-serif;color:#555'>Report wird geladen …</p>";
  }
  try {
    const response = await apiClient.get<Blob>(`/reports/runs/${id}/pdf`, { params: { inline: true }, responseType: "blob" });
    const url = URL.createObjectURL(new Blob([response.data], { type: "application/pdf" }));
    if (win) win.location.href = url;
    else window.location.href = url;
    // Nicht sofort freigeben -- der Tab laedt die URL asynchron.
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    win?.close();
    throw err;
  }
}

function filenameFrom(disposition: string | undefined, fallback: string): string {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition ?? "");
  return match ? decodeURIComponent(match[1]) : fallback;
}

export async function downloadReportFile(id: string, kind: "pdf" | "csv"): Promise<void> {
  const response = await apiClient.get<Blob>(`/reports/runs/${id}/${kind}`, {
    params: kind === "pdf" ? { inline: false } : undefined,
    responseType: "blob",
  });
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filenameFrom(response.headers["content-disposition"] as string | undefined, `report.${kind}`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
