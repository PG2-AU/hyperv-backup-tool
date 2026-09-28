import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type { DbBackupConfig, DbBackupConfigWrite, DbBackupList, DbRestorePreview } from "@/api/types";

// DB-Sicherung (backend app.api.routes.db_backup, Backlog #66).

export function useDbBackupConfig() {
  return useQuery({
    queryKey: ["db-backup-config"],
    queryFn: async () => (await apiClient.get<DbBackupConfig>("/db-backup")).data,
  });
}

export function useSaveDbBackupConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DbBackupConfigWrite) => (await apiClient.put<DbBackupConfig>("/db-backup", payload)).data,
    onSuccess: (data) => queryClient.setQueryData(["db-backup-config"], data),
  });
}

export function useTestDbBackupTarget() {
  return useMutation({
    mutationFn: async () => (await apiClient.post<{ message: string }>("/db-backup/test")).data,
  });
}

export function useRunDbBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => (await apiClient.post<DbBackupConfig>("/db-backup/run")).data,
    onSuccess: (data) => {
      queryClient.setQueryData(["db-backup-config"], data);
      queryClient.invalidateQueries({ queryKey: ["db-backup-list"] });
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

// Liste der Sicherungen -- fragt die Freigabe live ab, daher nur auf
// Anforderung (enabled) und ohne automatisches Neuladen.
export function useDbBackupList(enabled: boolean) {
  return useQuery({
    queryKey: ["db-backup-list"],
    queryFn: async () => (await apiClient.get<DbBackupList>("/db-backup/backups")).data,
    enabled,
    refetchOnWindowFocus: false,
  });
}

export type RestoreSource = { kind: "stored"; source: "share" | "local"; name: string } | { kind: "upload"; file: File };

function uploadForm(file: File) {
  const form = new FormData();
  form.append("file", file);
  return form;
}

export function usePreviewDbRestore() {
  return useMutation({
    mutationFn: async (source: RestoreSource) =>
      source.kind === "stored"
        ? (await apiClient.post<DbRestorePreview>("/db-backup/restore/preview", { source: source.source, name: source.name }))
            .data
        : (await apiClient.post<DbRestorePreview>("/db-backup/restore/upload/preview", uploadForm(source.file))).data,
  });
}

export function useRunDbRestore() {
  return useMutation({
    mutationFn: async ({ source, confirm }: { source: RestoreSource; confirm: string }) =>
      source.kind === "stored"
        ? (
            await apiClient.post<{ safety_copy: string; message: string }>("/db-backup/restore", {
              source: source.source,
              name: source.name,
              confirm,
            })
          ).data
        : (
            await apiClient.post<{ safety_copy: string; message: string }>("/db-backup/restore/upload", uploadForm(source.file), {
              params: { confirm },
            })
          ).data,
  });
}
