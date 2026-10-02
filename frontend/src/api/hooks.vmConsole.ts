import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/api/client";

// Remote-Sitzung auf eine VM (backend app.api.routes.vm_console, Backlog #75).

export interface VmConsoleInfo {
  cluster_id: string;
  vm_name: string;
  vm_id: string;
  state: string;
  host: string;
  console_port: number;
  ip_addresses: string[];
}

function base(clusterId: string, vmName: string) {
  return `/vm-console/${clusterId}/${encodeURIComponent(vmName)}`;
}

export function useVmConsoleInfo(clusterId: string | null | undefined, vmName: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["vm-console", clusterId, vmName],
    queryFn: async () => (await apiClient.get<VmConsoleInfo>(base(clusterId!, vmName!))).data,
    enabled: enabled && !!clusterId && !!vmName,
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

// Laedt die .rdp-Datei mit Anmelde-Header und uebergibt sie dem Browser als Download.
export async function downloadVmRdp(clusterId: string, vmName: string, kind: "console" | "guest", address?: string) {
  const response = await apiClient.get(`${base(clusterId, vmName)}/${kind}.rdp`, {
    params: kind === "guest" ? { address } : undefined,
    responseType: "blob",
  });
  const url = URL.createObjectURL(response.data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${vmName.replace(/[^A-Za-z0-9_.-]+/g, "_")}-${kind === "console" ? "Konsole" : "RDP"}.rdp`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
