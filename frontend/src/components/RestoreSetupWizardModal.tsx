import { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  PasswordInput,
  Select,
  Stack,
  Stepper,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle } from "@tabler/icons-react";

import {
  useBroadcastDomains,
  useCreateRestoreLif,
  useNetAppClusters,
  useRestoreInitiator,
  useRestoreProxyHost,
  useSaveRestoreProxyHost,
  useSetupRestoreInfra,
  useSvmLifCandidates,
  useSvms,
} from "@/api/hooks";
import { apiErrorMessage } from "@/utils/errors";
import { dedupeOptions } from "@/utils/selectOptions";

interface RestoreSetupWizardModalProps {
  opened: boolean;
  onClose: () => void;
}

export function RestoreSetupWizardModal({ opened, onClose }: RestoreSetupWizardModalProps) {
  const [active, setActive] = useState(0);
  const [clusterId, setClusterId] = useState<string | null>(null);
  const [svmName, setSvmName] = useState<string | null>(null);
  const [selectedLifAddress, setSelectedLifAddress] = useState<string | null>(null);
  const [selectedLifName, setSelectedLifName] = useState<string | null>(null);
  const [igroupName, setIgroupName] = useState("hvnb_restore");

  const [newLifName, setNewLifName] = useState("iscsi_restore");
  const [newLifAddress, setNewLifAddress] = useState("");
  const [newLifNetmask, setNewLifNetmask] = useState("24");
  const [newLifDomain, setNewLifDomain] = useState<string | null>(null);
  const [showCreateLif, setShowCreateLif] = useState(false);

  const [proxyAddress, setProxyAddress] = useState("");
  const [proxyUsername, setProxyUsername] = useState("");
  const [proxyPassword, setProxyPassword] = useState("");
  const [proxyUseHttps, setProxyUseHttps] = useState(true);

  const { data: initiator, isLoading: initiatorLoading } = useRestoreInitiator(opened);
  const { data: proxyHost } = useRestoreProxyHost(opened);
  const saveProxyHost = useSaveRestoreProxyHost();
  const { data: clusters } = useNetAppClusters();
  const { data: svms } = useSvms();
  const { data: lifCandidates, refetch: refetchLifs, isFetching: lifsLoading } = useSvmLifCandidates(
    clusterId ?? undefined,
    svmName ?? undefined,
  );
  const { data: broadcastDomains } = useBroadcastDomains(clusterId ?? undefined, showCreateLif);
  const createLif = useCreateRestoreLif();
  const setupInfra = useSetupRestoreInfra();

  useEffect(() => {
    if (!opened) {
      setActive(0);
      setClusterId(null);
      setSvmName(null);
      setSelectedLifAddress(null);
      setSelectedLifName(null);
      setIgroupName("hvnb_restore");
      setShowCreateLif(false);
      setProxyPassword("");
    }
  }, [opened]);

  useEffect(() => {
    if (proxyHost?.configured) {
      setProxyAddress(proxyHost.address ?? "");
      setProxyUsername(proxyHost.username ?? "");
      setProxyUseHttps(proxyHost.use_https);
    }
  }, [proxyHost]);

  function handleSaveProxyHost() {
    saveProxyHost.mutate(
      { address: proxyAddress, username: proxyUsername, password: proxyPassword || null, use_https: proxyUseHttps },
      {
        onSuccess: () => {
          notifications.show({ title: "Gespeichert", message: "Restore-Proxy-Host wurde gespeichert.", color: "green" });
          setProxyPassword("");
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Proxy-Host konnte nicht gespeichert werden."), color: "red" }),
      },
    );
  }

  const svmOptions = dedupeOptions(
    (svms ?? []).filter((s) => s.cluster_id === clusterId).map((s) => ({ value: s.name, label: s.name })),
  );
  const clusterOptions = (clusters ?? []).map((c) => ({ value: c.id, label: c.name }));

  const domain = (broadcastDomains ?? []).find((d) => d.name === newLifDomain);
  const domainPortOptions = dedupeOptions(
    (domain?.ports ?? []).map((p) => ({ value: `${p.node_name}|${p.port_name}`, label: `${p.node_name} / ${p.port_name}` })),
  );
  const [newLifPort, setNewLifPort] = useState<string | null>(null);

  function handleCreateLif() {
    if (!clusterId || !svmName || !domain || !newLifPort || !newLifAddress) return;
    const [homeNode, homePort] = newLifPort.split("|");
    createLif.mutate(
      {
        clusterId,
        payload: {
          svm_name: svmName, name: newLifName, address: newLifAddress, netmask: newLifNetmask,
          broadcast_domain: domain.name, home_node: homeNode, home_port: homePort,
        },
      },
      {
        onSuccess: (lif) => {
          notifications.show({ title: "Interface angelegt", message: `${lif.name} (${lif.address})`, color: "green" });
          setShowCreateLif(false);
          setSelectedLifAddress(lif.address);
          setSelectedLifName(lif.name);
          refetchLifs();
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Interface konnte nicht angelegt werden."), color: "red" }),
      },
    );
  }

  function handleSetup() {
    if (!clusterId || !svmName || !selectedLifAddress) return;
    setupInfra.mutate(
      {
        clusterId,
        payload: {
          svm_name: svmName, iscsi_lif_name: selectedLifName, iscsi_lif_address: selectedLifAddress,
          iscsi_lif_port: 3260, igroup_name: igroupName,
        },
      },
      {
        onSuccess: () => {
          notifications.show({ title: "Restore-Setup abgeschlossen", message: `${svmName} ist fuer Restores eingerichtet.`, color: "green" });
          onClose();
        },
        onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Einrichtung fehlgeschlagen."), color: "red" }),
      },
    );
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Restore-Infrastruktur einrichten" size="xl">
      <Stepper active={active} onStepClick={setActive} size="sm">
        <Stepper.Step label="Proxy-Host" description="Windows-iSCSI-Initiator">
          <Stack mt="md">
            <TextInput
              label="Adresse"
              placeholder="z.B. 10.93.70.13 oder hostname"
              value={proxyAddress}
              onChange={(e) => setProxyAddress(e.currentTarget.value)}
            />
            <TextInput
              label="Benutzername"
              placeholder="z.B. .\Administrator oder domain\user"
              value={proxyUsername}
              onChange={(e) => setProxyUsername(e.currentTarget.value)}
            />
            <PasswordInput
              label="Passwort"
              placeholder={proxyHost?.configured ? "leer lassen, um bestehendes Passwort zu behalten" : ""}
              value={proxyPassword}
              onChange={(e) => setProxyPassword(e.currentTarget.value)}
            />
            <Switch
              label="WinRM ueber HTTPS"
              checked={proxyUseHttps}
              onChange={(e) => setProxyUseHttps(e.currentTarget.checked)}
            />
            <Group justify="flex-start">
              <Button
                variant="default"
                onClick={handleSaveProxyHost}
                loading={saveProxyHost.isPending}
                disabled={!proxyAddress || !proxyUsername}
              >
                Speichern
              </Button>
            </Group>

            {initiatorLoading && <Loader size="sm" />}
            {initiator?.configured ? (
              <Text size="sm" ff="monospace">
                {initiator.iqn}
              </Text>
            ) : (
              <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
                {initiator?.error ?? "Restore-Proxy-Host nicht erreichbar oder nicht konfiguriert."}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button onClick={() => setActive(1)} disabled={!initiator?.configured}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Netzwerk" description="SVM & iSCSI-Interface">
          <Stack mt="md">
            <Select label="NetApp-Cluster" data={clusterOptions} value={clusterId} onChange={(v) => { setClusterId(v); setSvmName(null); }} />
            <Select label="SVM" data={svmOptions} value={svmName} onChange={setSvmName} searchable />
            <Button
              variant="default"
              onClick={() => refetchLifs()}
              loading={lifsLoading}
              disabled={!clusterId || !svmName}
              style={{ alignSelf: "flex-start" }}
            >
              Netzwerk prüfen
            </Button>
            {lifCandidates && (
              <Stack gap="xs">
                {lifCandidates.map((lif) => (
                  <Group key={lif.address} justify="space-between">
                    <Text size="sm">
                      {lif.name} ({lif.address})
                    </Text>
                    {lif.reachable ? (
                      <Button
                        size="xs"
                        color={selectedLifAddress === lif.address ? "green" : undefined}
                        variant={selectedLifAddress === lif.address ? "filled" : "light"}
                        onClick={() => { setSelectedLifAddress(lif.address); setSelectedLifName(lif.name); }}
                      >
                        {selectedLifAddress === lif.address ? "Ausgewählt" : "Auswählen"}
                      </Button>
                    ) : (
                      <Badge color="gray" variant="light">
                        nicht erreichbar
                      </Badge>
                    )}
                  </Group>
                ))}
                {lifCandidates.every((l) => !l.reachable) && (
                  <Alert icon={<IconAlertTriangle size={16} />} color="orange" variant="light">
                    Kein vorhandenes Interface dieser SVM ist erreichbar. Neues Interface anlegen:
                  </Alert>
                )}
              </Stack>
            )}

            {(showCreateLif || (lifCandidates && lifCandidates.every((l) => !l.reachable))) && (
              <Stack gap="xs" p="sm" style={{ border: "1px solid var(--mantine-color-default-border)", borderRadius: 8 }}>
                <Text size="sm" fw={600}>
                  Neues iSCSI-Interface anlegen
                </Text>
                <Group grow>
                  <TextInput label="Name" value={newLifName} onChange={(e) => setNewLifName(e.currentTarget.value)} />
                  <TextInput label="IP-Adresse" value={newLifAddress} onChange={(e) => setNewLifAddress(e.currentTarget.value)} placeholder="z.B. 10.93.70.221" />
                  <TextInput label="Netzmaske" value={newLifNetmask} onChange={(e) => setNewLifNetmask(e.currentTarget.value)} placeholder="z.B. 24" />
                </Group>
                <Select
                  label="Broadcast-Domain"
                  data={(broadcastDomains ?? []).map((d) => ({ value: d.name, label: `${d.name} (${d.ipspace})` }))}
                  value={newLifDomain}
                  onChange={(v) => { setNewLifDomain(v); setNewLifPort(null); }}
                  searchable
                />
                <Select label="Node / Port" data={domainPortOptions} value={newLifPort} onChange={setNewLifPort} disabled={!domain} />
                <Button
                  onClick={handleCreateLif}
                  loading={createLif.isPending}
                  disabled={!newLifAddress || !newLifDomain || !newLifPort}
                  style={{ alignSelf: "flex-start" }}
                >
                  Interface anlegen
                </Button>
              </Stack>
            )}

            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(0)}>
                Zurück
              </Button>
              <Button onClick={() => setActive(2)} disabled={!selectedLifAddress}>
                Weiter
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>

        <Stepper.Step label="Einrichten" description="Security & Igroup">
          <Stack mt="md">
            <Text size="sm">
              SVM: <strong>{svmName}</strong> über <strong>{selectedLifAddress}</strong>
            </Text>
            <TextInput label="Igroup-Name" value={igroupName} onChange={(e) => setIgroupName(e.currentTarget.value)} />
            <Text size="xs" c="dimmed">
              Legt eine iSCSI-Zugriffsberechtigung für den Proxy-Host-Initiator sowie die Igroup auf der SVM an.
            </Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setActive(1)}>
                Zurück
              </Button>
              <Button onClick={handleSetup} loading={setupInfra.isPending} disabled={!igroupName}>
                Einrichten
              </Button>
            </Group>
          </Stack>
        </Stepper.Step>
      </Stepper>
    </Modal>
  );
}
