import { apiClient } from "@/api/client";
import type { HyperVClusterCreationPlan } from "@/api/types";
import type { ProcessStepDef } from "@/components/ProcessModal";

export function buildHyperVClusterCreationSteps(plan: HyperVClusterCreationPlan, onCreated?: (clusterId: string) => void): ProcessStepDef[] {
  const port = plan.useHttps ? 5986 : 5985;
  return [
    {
      id: "reachability",
      emoji: "📡",
      label: `Prüfe Netzwerk-Erreichbarkeit (Port ${port}, ${plan.useHttps ? "HTTPS" : "HTTP"})`,
      run: async () => {
        await apiClient.post("/hyperv/clusters/check-reachability", {
          management_address: plan.managementAddress,
          use_https: plan.useHttps,
        });
      },
    },
    {
      id: "connect",
      emoji: "🔌",
      label: "Verbinde per WinRM und frage Cluster-Informationen ab (Get-Cluster / Get-ClusterNode)",
      run: async () => {
        const resp = await apiClient.post<{ id: string }>("/hyperv/clusters", {
          name: plan.name,
          management_address: plan.managementAddress,
          username: plan.username,
          password: plan.password,
          use_https: plan.useHttps,
        });
        onCreated?.(resp.data.id);
      },
    },
  ];
}

// Gleicher zweistufiger Ablauf wie bei der Neuanlage (siehe oben), nur PUT
// auf den bestehenden Cluster statt POST -- fuer eine Bearbeitung/
// Passwort-Rotation, bei der die cluster.id bewusst erhalten bleibt (siehe
// update_cluster in hyperv_clusters.py). Ein leeres plan.password behaelt
// serverseitig das bisherige Kennwort bei.
export function buildHyperVClusterUpdateSteps(clusterId: string, plan: HyperVClusterCreationPlan): ProcessStepDef[] {
  const port = plan.useHttps ? 5986 : 5985;
  return [
    {
      id: "reachability",
      emoji: "📡",
      label: `Prüfe Netzwerk-Erreichbarkeit (Port ${port}, ${plan.useHttps ? "HTTPS" : "HTTP"})`,
      run: async () => {
        await apiClient.post("/hyperv/clusters/check-reachability", {
          management_address: plan.managementAddress,
          use_https: plan.useHttps,
        });
      },
    },
    {
      id: "connect",
      emoji: "🔌",
      label: "Verbinde per WinRM und aktualisiere die gespeicherten Zugangsdaten",
      run: async () => {
        await apiClient.put(`/hyperv/clusters/${clusterId}`, {
          name: plan.name,
          management_address: plan.managementAddress,
          username: plan.username,
          password: plan.password || undefined,
          use_https: plan.useHttps,
        });
      },
    },
  ];
}
