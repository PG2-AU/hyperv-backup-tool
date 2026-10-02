import { useEffect, useState } from "react";
import { Alert, Button, Divider, Group, Kbd, Loader, Modal, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconDeviceDesktop, IconDownload, IconInfoCircle } from "@tabler/icons-react";

import { downloadVmRdp, useVmConsoleInfo } from "@/api/hooks.vmConsole";
import { apiErrorMessage } from "@/utils/errors";

const VIA_KEY = "hvnb.vmConsole.via";
// Zuletzt genutzte Benutzernamen (Konto auf dem Knoten bzw. im Gast) -- nur
// der Name, ein Kennwort kann eine .rdp-Datei nicht mitgeben.
const HOST_USER_KEY = "hvnb.vmConsole.hostUser";
const GUEST_USER_KEY = "hvnb.vmConsole.guestUser";

interface VmConsoleModalProps {
  vm: { name: string; cluster_id?: string | null } | null;
  onClose: () => void;
}

// Remote-Sitzung auf eine VM (Backlog #75): die App liefert .rdp-Dateien fuer
// den Remotedesktop-Client -- Konsole ueber den Hyper-V-Knoten (Port 2179,
// wie VMConnect) oder RDP direkt ins Gastsystem. Angemeldet wird im Client.
export function VmConsoleModal({ vm, onClose }: VmConsoleModalProps) {
  const opened = vm !== null;
  const { data: info, isLoading, error } = useVmConsoleInfo(vm?.cluster_id, vm?.name, opened);
  const [address, setAddress] = useState<string | null>(null);
  const [busy, setBusy] = useState<"console" | "guest" | null>(null);
  // Knoten per Name oder per IP ansprechen -- ein PC ausserhalb der Domaene
  // kann den Namen nicht aufloesen. Die Wahl bleibt im Browser gespeichert.
  const [via, setVia] = useState<"name" | "ip">(() => (localStorage.getItem(VIA_KEY) === "ip" ? "ip" : "name"));
  const [hostUser, setHostUser] = useState(() => localStorage.getItem(HOST_USER_KEY) ?? "");
  const [guestUser, setGuestUser] = useState(() => localStorage.getItem(GUEST_USER_KEY) ?? "");
  function chooseVia(value: string) {
    const next = value === "ip" ? "ip" : "name";
    setVia(next);
    localStorage.setItem(VIA_KEY, next);
  }

  useEffect(() => {
    setAddress(info?.ip_addresses[0] ?? null);
  }, [info]);

  function download(kind: "console" | "guest") {
    if (!vm?.cluster_id) return;
    setBusy(kind);
    localStorage.setItem(HOST_USER_KEY, hostUser.trim());
    localStorage.setItem(GUEST_USER_KEY, guestUser.trim());
    downloadVmRdp(
      vm.cluster_id,
      vm.name,
      kind,
      kind === "guest" ? (address ?? undefined) : effectiveVia,
      kind === "guest" ? guestUser : hostUser,
    )
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Die Datei konnte nicht erzeugt werden."), color: "red" }),
      )
      .finally(() => setBusy(null));
  }

  const effectiveVia = via === "ip" && info?.host_address ? "ip" : "name";

  return (
    <Modal opened={opened} onClose={onClose} title={`Remote-Sitzung: ${vm?.name ?? ""}`} size={620}>
      <Stack gap="md">
        {isLoading && (
          <Group gap="xs">
            <Loader size="xs" />
            <Text size="sm">Knoten, VM-ID und IP-Adressen werden live abgefragt…</Text>
          </Group>
        )}
        {error && <Alert color="red">{apiErrorMessage(error, "Abfrage fehlgeschlagen.")}</Alert>}
        {info && (
          <>
            <Stack gap={6}>
              <Group justify="space-between" align="flex-start" wrap="nowrap">
                <Stack gap={2}>
                  <Text fw={600} size="sm">
                    Konsole der VM
                  </Text>
                  <Text size="xs" c="dimmed">
                    Bildschirm der VM über den Knoten {info.host} (Port {info.console_port}), wie in der Hyper-V-Konsole --
                    funktioniert auch ohne Netzwerk in der VM. Anmelden mit einem Konto, das auf dem Knoten Hyper-V-Rechte hat.
                  </Text>
                </Stack>
                <Button
                  leftSection={<IconDeviceDesktop size={16} />}
                  onClick={() => download("console")}
                  loading={busy === "console"}
                  style={{ flexShrink: 0 }}
                >
                  Konsole öffnen
                </Button>
              </Group>
              {info.host_address && (
                <Group gap="xs">
                  <Text size="xs">Verbinden über</Text>
                  <SegmentedControl
                    size="xs"
                    value={effectiveVia}
                    onChange={chooseVia}
                    data={[
                      { value: "name", label: `Name (${info.host})` },
                      { value: "ip", label: `IP-Adresse (${info.host_address})` },
                    ]}
                  />
                  <Text size="xs" c="dimmed">
                    IP-Adresse wählen, wenn der PC den Knotennamen nicht auflösen kann (z. B. nicht in der Domäne).
                  </Text>
                </Group>
              )}
              <TextInput
                size="xs"
                label="Benutzername (optional)"
                description="Konto mit Hyper-V-Rechten auf dem Knoten -- wird in der Datei vorbelegt, das Kennwort fragt der Client ab"
                placeholder="DOMÄNE\Benutzer"
                value={hostUser}
                onChange={(e) => setHostUser(e.currentTarget.value)}
                w={360}
              />
              {info.state !== "Running" && (
                <Alert color="yellow" py={6} icon={<IconInfoCircle size={16} />}>
                  Die VM ist nicht eingeschaltet (Status {info.state}) -- die Konsole zeigt dann nur einen leeren Bildschirm.
                </Alert>
              )}
            </Stack>

            <Divider />

            <Group justify="space-between" align="flex-start" wrap="nowrap">
              <Stack gap={2} style={{ flex: 1 }}>
                <Text fw={600} size="sm">
                  RDP ins Gastsystem
                </Text>
                <Text size="xs" c="dimmed">
                  Direkte Remotedesktop-Verbindung zur IP-Adresse der VM. Braucht Netzwerk und aktiviertes RDP im Gast (Windows).
                </Text>
                {info.ip_addresses.length > 0 ? (
                  <Group mt={4} align="flex-end">
                    <Select size="xs" label="IP-Adresse" data={info.ip_addresses} value={address} onChange={setAddress} allowDeselect={false} w={200} />
                    <TextInput
                      size="xs"
                      label="Benutzername (optional)"
                      placeholder="DOMÄNE\Benutzer"
                      value={guestUser}
                      onChange={(e) => setGuestUser(e.currentTarget.value)}
                      w={240}
                    />
                  </Group>
                ) : (
                  <Text size="xs" c="orange" mt={4}>
                    Die VM meldet keine IP-Adresse (ausgeschaltet, kein Netzwerk oder keine Integrationsdienste).
                  </Text>
                )}
              </Stack>
              <Button
                variant="light"
                leftSection={<IconDownload size={16} />}
                onClick={() => download("guest")}
                loading={busy === "guest"}
                disabled={!address}
                style={{ flexShrink: 0 }}
              >
                RDP-Datei
              </Button>
            </Group>

            <Alert color="blue" variant="light" py={6} icon={<IconInfoCircle size={16} />}>
              Strg+Alt+Entf in der Sitzung: <Kbd>Strg</Kbd> + <Kbd>Alt</Kbd> + <Kbd>Ende</Kbd> drücken.
            </Alert>
            <Text size="xs" c="dimmed">
              Es wird jeweils eine .rdp-Datei heruntergeladen -- per Doppelklick öffnet sie die Remotedesktopverbindung. Die App
              gibt keine Zugangsdaten weiter; der Download wird im System-Log vermerkt.
            </Text>
          </>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Schließen
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
