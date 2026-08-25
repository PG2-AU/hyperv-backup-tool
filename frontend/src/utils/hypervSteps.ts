import { apiClient } from "@/api/client";
import type { HyperVClusterCreationPlan } from "@/api/types";
import type { ProcessStepDef } from "@/components/ProcessModal";

export function buildHyperVClusterCreationSteps(plan: HyperVClusterCreationPlan): ProcessStepDef[] {
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
        await apiClient.post("/hyperv/clusters", {
          name: plan.name,
          management_address: plan.managementAddress,
          username: plan.username,
          password: plan.password,
          use_https: plan.useHttps,
        });
      },
    },
  ];
}
