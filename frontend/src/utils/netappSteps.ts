import { apiClient } from "@/api/client";
import type {
  LunCreationPlan,
  LunEditPlan,
  PolicyCreationPlan,
  PolicyEditPlan,
  ScheduleCreationPlan,
  SnapmirrorCreationPlan,
  SnapmirrorEditPlan,
  VolumeCreationPlan,
  VolumeEditPlan,
} from "@/api/types";
import type { ProcessStepDef } from "@/components/ProcessModal";

function discoverStep(clusterId: string): ProcessStepDef {
  return { id: "discover", emoji: "🔄", label: "Aktualisiere Übersicht", alwaysRun: true, run: async () => {
    await apiClient.post(`/netapp/clusters/${clusterId}/discover`);
  } };
}

export function buildLunCreationSteps(plan: LunCreationPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  const steps: ProcessStepDef[] = [];

  if (plan.volumeMode === "new") {
    steps.push({
      id: "volume",
      emoji: "🗂️",
      label: `Lege Volume '${plan.volumeName}' an (Security Style: unix, Space Guarantee: aus)`,
      run: async () => {
        await apiClient.post(`${base}/volumes`, {
          svm_name: plan.svmName,
          name: plan.volumeName,
          aggregate_name: plan.newVolumeAggregate,
          size_bytes: plan.newVolumeSizeBytes,
          security_style: "unix",
          guarantee_type: "none",
        });
      },
    });
  }

  steps.push({
    id: "lun",
    emoji: "🧱",
    label: `Lege LUN '${plan.lunName}' an (Space Allocation: aktiv)`,
    run: async () => {
      await apiClient.post(`${base}/luns`, {
        svm_name: plan.svmName,
        lun_name: plan.lunName,
        os_type: plan.osType,
        size_bytes: plan.lunSizeBytes,
        volume_name: plan.volumeName,
        space_allocation_enabled: true,
      });
    },
  });

  if (plan.igroupMode === "new" && plan.newIgroup) {
    steps.push({
      id: "igroup",
      emoji: "🎯",
      label: `Lege Initiator-Gruppe '${plan.newIgroup.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/igroups`, {
          svm_name: plan.svmName,
          name: plan.newIgroup!.name,
          os_type: plan.newIgroup!.osType,
          protocol: plan.newIgroup!.protocol,
          initiators: plan.newIgroup!.initiators,
        });
      },
    });
  }

  if (plan.igroupMode !== "none") {
    steps.push({
      id: "mapping",
      emoji: "🔌",
      label: "Verknüpfe LUN mit IGroup",
      run: async () => {
        const igroupName = plan.igroupMode === "new" ? plan.newIgroup?.name : plan.igroupName;
        await apiClient.post(`${base}/lun-maps`, {
          svm_name: plan.svmName,
          lun_name: `/vol/${plan.volumeName}/${plan.lunName}`,
          igroup_name: igroupName,
        });
      },
    });
  }

  steps.push(discoverStep(plan.clusterId));
  return steps;
}

export function buildVolumeCreationSteps(plan: VolumeCreationPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  return [
    {
      id: "volume",
      emoji: "🗂️",
      label: `Lege Volume '${plan.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/volumes`, {
          svm_name: plan.svmName,
          name: plan.name,
          aggregate_name: plan.aggregateName,
          size_bytes: plan.sizeBytes,
          security_style: plan.securityStyle,
          guarantee_type: plan.guaranteeType,
        });
      },
    },
    discoverStep(plan.clusterId),
  ];
}

export function buildLunEditSteps(plan: LunEditPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  const steps: ProcessStepDef[] = [];

  if (plan.newSizeBytes != null || plan.setEnabled != null) {
    steps.push({
      id: "update",
      emoji: "🛠️",
      label: "Ändere LUN-Eigenschaften",
      run: async () => {
        await apiClient.patch(`${base}/luns/${plan.lunUuid}`, {
          size_bytes: plan.newSizeBytes,
          enabled: plan.setEnabled,
        });
      },
    });
  }

  if (plan.unmapIgroupName) {
    steps.push({
      id: "unmap",
      emoji: "🔌",
      label: `Entferne Mapping zu '${plan.unmapIgroupName}'`,
      run: async () => {
        await apiClient.delete(
          `${base}/lun-maps/${plan.lunUuid}?igroup_name=${encodeURIComponent(plan.unmapIgroupName!)}&svm_name=${encodeURIComponent(plan.svmName)}`,
        );
      },
    });
  }

  if (plan.mapIgroupName) {
    steps.push({
      id: "map",
      emoji: "🔌",
      label: `Verknüpfe mit IGroup '${plan.mapIgroupName}'`,
      run: async () => {
        await apiClient.post(`${base}/lun-maps`, {
          svm_name: plan.svmName,
          lun_name: `/vol/${plan.volumeName}/${plan.currentShortName}`,
          igroup_name: plan.mapIgroupName,
        });
      },
    });
  }

  steps.push(discoverStep(plan.clusterId));
  return steps;
}

export function buildLunDeleteSteps(clusterId: string, lunUuid: string): ProcessStepDef[] {
  return [
    {
      id: "delete",
      emoji: "🗑️",
      label: "Lösche LUN",
      run: async () => {
        await apiClient.delete(`/netapp/clusters/${clusterId}/luns/${lunUuid}`);
      },
    },
    discoverStep(clusterId),
  ];
}

export function buildVolumeEditSteps(plan: VolumeEditPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  const steps: ProcessStepDef[] = [];

  if (plan.newSizeBytes != null) {
    steps.push({
      id: "resize",
      emoji: "📐",
      label: `Ändere Volume-Größe`,
      run: async () => {
        await apiClient.patch(`${base}/volumes/${plan.volumeUuid}`, { size_bytes: plan.newSizeBytes });
      },
    });
  }

  if (plan.setState) {
    steps.push({
      id: "state",
      emoji: plan.setState === "offline" ? "⏸️" : "▶️",
      label: plan.setState === "offline" ? "Nehme Volume offline" : "Nehme Volume online",
      run: async () => {
        await apiClient.patch(`${base}/volumes/${plan.volumeUuid}`, { state: plan.setState });
      },
    });
  }

  steps.push(discoverStep(plan.clusterId));
  return steps;
}

export function buildVolumeDeleteSteps(clusterId: string, volumeUuid: string): ProcessStepDef[] {
  return [
    {
      id: "delete",
      emoji: "🗑️",
      label: "Lösche Volume",
      run: async () => {
        await apiClient.delete(`/netapp/clusters/${clusterId}/volumes/${volumeUuid}`);
      },
    },
    discoverStep(clusterId),
  ];
}

export function buildSnapmirrorCreationSteps(plan: SnapmirrorCreationPlan): ProcessStepDef[] {
  const destBase = `/netapp/clusters/${plan.destinationClusterId}`;
  const steps: ProcessStepDef[] = [];
  let relationshipUuid: string | null = null;

  steps.push({
    id: "destination-volume",
    emoji: "🗂️",
    label: `Lege Ziel-Volume '${plan.destinationVolumeName}' an (Typ: DP)`,
    run: async () => {
      await apiClient.post(`${destBase}/volumes`, {
        svm_name: plan.destinationSvmName,
        name: plan.destinationVolumeName,
        aggregate_name: plan.destinationAggregate,
        size_bytes: plan.sourceVolumeSizeBytes,
        volume_type: "dp",
      });
    },
  });

  if (plan.policyMode === "new" && plan.newPolicy) {
    steps.push({
      id: "policy",
      emoji: "📋",
      label: `Lege SnapMirror-Policy '${plan.newPolicy.name}' an`,
      run: async () => {
        await apiClient.post(`${destBase}/snapmirror-policies`, {
          svm_name: plan.newPolicy!.svmName,
          name: plan.newPolicy!.name,
          vault_type: plan.newPolicy!.vaultType,
          rules: plan.newPolicy!.rules,
        });
      },
    });
  }

  if (plan.scheduleMode === "new" && plan.newSchedule) {
    steps.push({
      id: "schedule",
      emoji: "⏱️",
      label: `Lege Schedule '${plan.newSchedule.name}' an`,
      run: async () => {
        await apiClient.post(`${destBase}/schedules`, {
          name: plan.newSchedule!.name,
          svm_name: plan.newSchedule!.svmName,
          minutes: plan.newSchedule!.minutes,
          hours: plan.newSchedule!.hours,
          days: plan.newSchedule!.days,
          weekdays: plan.newSchedule!.weekdays,
        });
      },
    });
  }

  steps.push({
    id: "relationship",
    emoji: "🪞",
    label: `Erstelle SnapMirror-Beziehung ${plan.sourceSvmName}:${plan.sourceVolumeName} → ${plan.destinationSvmName}:${plan.destinationVolumeName}`,
    run: async () => {
      const policyName = plan.policyMode === "new" ? plan.newPolicy?.name : plan.policyName;
      const scheduleName = plan.scheduleMode === "new" ? plan.newSchedule?.name : plan.scheduleMode === "existing" ? plan.scheduleName : undefined;
      const resp = await apiClient.post<{ uuid: string }>(`${destBase}/snapmirror-relationships`, {
        source_cluster_id: plan.sourceClusterId,
        source_svm_name: plan.sourceSvmName,
        source_volume_name: plan.sourceVolumeName,
        destination_svm_name: plan.destinationSvmName,
        destination_volume_name: plan.destinationVolumeName,
        policy_name: policyName,
        schedule_name: scheduleName,
      });
      relationshipUuid = resp.data.uuid;
    },
  });

  if (plan.autoInitialize) {
    steps.push({
      id: "initialize",
      emoji: "🚀",
      label: "Starte initialen Transfer (Baseline)",
      run: async () => {
        if (!relationshipUuid) throw new Error("Keine Beziehung zum Initialisieren vorhanden");
        await apiClient.post(`${destBase}/snapmirror-relationships/${relationshipUuid}/initialize`);
      },
    });
  }

  if (plan.sourceClusterId !== plan.destinationClusterId) {
    steps.push({
      id: "discover-source",
      emoji: "🔄",
      label: "Aktualisiere Übersicht (Quell-Cluster)",
      alwaysRun: true,
      run: async () => {
        await apiClient.post(`/netapp/clusters/${plan.sourceClusterId}/discover`);
      },
    });
  }
  steps.push(discoverStep(plan.destinationClusterId));

  return steps;
}

export function buildSnapmirrorEditSteps(plan: SnapmirrorEditPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  const steps: ProcessStepDef[] = [];

  if (plan.policyMode === "new" && plan.newPolicy) {
    steps.push({
      id: "policy",
      emoji: "📋",
      label: `Lege SnapMirror-Policy '${plan.newPolicy.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/snapmirror-policies`, {
          svm_name: plan.newPolicy!.svmName,
          name: plan.newPolicy!.name,
          vault_type: plan.newPolicy!.vaultType,
          rules: plan.newPolicy!.rules,
        });
      },
    });
  }

  if (plan.scheduleMode === "new" && plan.newSchedule) {
    steps.push({
      id: "schedule",
      emoji: "⏱️",
      label: `Lege Schedule '${plan.newSchedule.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/schedules`, {
          name: plan.newSchedule!.name,
          svm_name: plan.newSchedule!.svmName,
          minutes: plan.newSchedule!.minutes,
          hours: plan.newSchedule!.hours,
          days: plan.newSchedule!.days,
          weekdays: plan.newSchedule!.weekdays,
        });
      },
    });
  }

  steps.push({
    id: "update",
    emoji: "🛠️",
    label: `Ändere SnapMirror-Beziehung ${plan.sourcePath}`,
    run: async () => {
      const policyName = plan.policyMode === "new" ? plan.newPolicy?.name : plan.policyName;
      const scheduleName =
        plan.scheduleMode === "new" ? plan.newSchedule?.name : plan.scheduleMode === "existing" ? plan.scheduleName : plan.scheduleMode === "none" ? "" : undefined;
      await apiClient.patch(`${base}/snapmirror-relationships/${plan.relationshipUuid}`, {
        policy_name: policyName,
        schedule_name: scheduleName,
      });
    },
  });

  steps.push(discoverStep(plan.clusterId));
  return steps;
}

export function buildPolicyCreationSteps(plan: PolicyCreationPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  return [
    {
      id: "policy",
      emoji: "📋",
      label: `Lege SnapMirror-Policy '${plan.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/snapmirror-policies`, {
          svm_name: plan.svmName,
          name: plan.name,
          vault_type: plan.vaultType,
          rules: plan.rules,
        });
      },
    },
    discoverStep(plan.clusterId),
  ];
}

export function buildPolicyEditSteps(plan: PolicyEditPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  return [
    {
      id: "update",
      emoji: "🛠️",
      label: `Ändere Regeln der Policy '${plan.policyName}'`,
      run: async () => {
        await apiClient.patch(`${base}/snapmirror-policies/${plan.policyUuid}`, { rules: plan.rules });
      },
    },
    discoverStep(plan.clusterId),
  ];
}

export function buildScheduleCreationSteps(plan: ScheduleCreationPlan): ProcessStepDef[] {
  const base = `/netapp/clusters/${plan.clusterId}`;
  return [
    {
      id: "schedule",
      emoji: "⏱️",
      label: `Lege Schedule '${plan.name}' an`,
      run: async () => {
        await apiClient.post(`${base}/schedules`, {
          name: plan.name,
          svm_name: plan.svmName,
          minutes: plan.minutes,
          hours: plan.hours,
          days: plan.days,
          weekdays: plan.weekdays,
        });
      },
    },
    discoverStep(plan.clusterId),
  ];
}
