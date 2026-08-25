import { apiClient } from "@/api/client";
import type { HyperVClusterCreationPlan } from "@/api/types";
import type { ProcessStepDef } from "@/components/ProcessModal";

export function buildHyperVClusterCreationSteps(plan: HyperVClusterCreationPlan): ProcessStepDef[] {
  return [
    {
      id: "reachability",
      emoji: "📡",
      label: `Prüfe Netzwerk-Erreichbarkeit (Port ${plan.winrmPort})`,
      run: async () => {
        await apiClient.post("/hyperv/clusters/check-reachability", { management_address: plan.managementAddress });
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
        });
      },
    },
  ];
}
