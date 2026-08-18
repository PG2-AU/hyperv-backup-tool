import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/api/client";
import type {
  BackupJobDefinition,
  BackupJobRun,
  Csv,
  MetroClusterStatus,
  NetAppCluster,
  SnapMirrorRelationship,
  SvmInfo,
  Vm,
} from "@/api/types";

export function useVms() {
  return useQuery({
    queryKey: ["vms"],
    queryFn: async () => (await apiClient.get<Vm[]>("/vms")).data,
  });
}

export function useCsvs() {
  return useQuery({
    queryKey: ["csvs"],
    queryFn: async () => (await apiClient.get<Csv[]>("/vms/csvs")).data,
  });
}

export function useSvms() {
  return useQuery({
    queryKey: ["svms"],
    queryFn: async () => (await apiClient.get<SvmInfo[]>("/storage/svms")).data,
  });
}

export function useSnapMirrorRelationships() {
  return useQuery({
    queryKey: ["snapmirror"],
    queryFn: async () => (await apiClient.get<SnapMirrorRelationship[]>("/storage/snapmirror-relationships")).data,
  });
}

export function useMetroClusterStatus() {
  return useQuery({
    queryKey: ["metrocluster"],
    queryFn: async () => (await apiClient.get<MetroClusterStatus>("/storage/metrocluster-status")).data,
  });
}

export function useJobs() {
  return useQuery({
    queryKey: ["jobs"],
    queryFn: async () => (await apiClient.get<BackupJobDefinition[]>("/jobs")).data,
  });
}

export function useJobRuns() {
  return useQuery({
    queryKey: ["job-runs"],
    queryFn: async () => (await apiClient.get<BackupJobRun[]>("/jobs/runs")).data,
  });
}

export function useTriggerJobRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (jobId: string) => (await apiClient.post<BackupJobRun>(`/jobs/${jobId}/run`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["job-runs"] }),
  });
}

export function useNetAppClusters() {
  return useQuery({
    queryKey: ["netapp-clusters"],
    queryFn: async () => (await apiClient.get<NetAppCluster[]>("/netapp/clusters")).data,
  });
}

export interface NetAppClusterCreatePayload {
  name: string;
  management_lif: string;
  username: string;
  password: string;
  verify_ssl: boolean;
}

export function useCreateNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: NetAppClusterCreatePayload) =>
      (await apiClient.post<NetAppCluster>("/netapp/clusters", payload)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useVerifyNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<NetAppCluster>(`/netapp/clusters/${id}/verify`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useEnrollNetAppClusterCertificate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => (await apiClient.post<NetAppCluster>(`/netapp/clusters/${id}/enroll-certificate`)).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}

export function useDeleteNetAppCluster() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/netapp/clusters/${id}`);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["netapp-clusters"] }),
  });
}
