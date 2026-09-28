import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { ConfigImportPreview, ConfigImportResult } from "@/api/types";

// Konfiguration exportieren/importieren (backend app.api.routes.config_transfer).

export function useConfigImportStatus() {
  return useQuery({
    queryKey: ["config-import-status"],
    queryFn: async () => (await apiClient.get<{ blockers: string[] }>("/config-transfer/import/status")).data,
  });
}

// Laedt das ZIP als Blob und stoesst den Browser-Download an. Der
// Dateiname kommt aus dem Content-Disposition-Header des Servers.
export function useExportConfig() {
  return useMutation({
    mutationFn: async (includeCatalog: boolean) => {
      const response = await apiClient.get<Blob>("/config-transfer/export", {
        params: { include_catalog: includeCatalog },
        responseType: "blob",
      });
      const disposition = String(response.headers["content-disposition"] ?? "");
      const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "hvnb-config.zip";
      const url = URL.createObjectURL(response.data);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      return filename;
    },
  });
}

function asForm(file: File) {
  const form = new FormData();
  form.append("file", file);
  return form;
}

export function usePreviewConfigImport() {
  return useMutation({
    mutationFn: async (file: File) => (await apiClient.post<ConfigImportPreview>("/config-transfer/import/preview", asForm(file))).data,
  });
}

export function useRunConfigImport() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => (await apiClient.post<ConfigImportResult>("/config-transfer/import", asForm(file))).data,
    // Praktisch jede Ansicht ist betroffen -- alles neu laden.
    onSuccess: () => queryClient.invalidateQueries(),
  });
}
