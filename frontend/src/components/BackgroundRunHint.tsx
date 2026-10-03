import { Button, Group, Text } from "@mantine/core";
import { IconLayoutBottombarExpand } from "@tabler/icons-react";

// Laufende Ablaeufe blockieren die App nicht mehr (Nutzer-Vorgabe 2026-10-03):
// der Dialog laesst sich schliessen, der Ablauf laeuft auf dem Server weiter
// und bleibt in der Aktivitaeten-Fusszeile sichtbar (Protokoll per Klick).
export function BackgroundRunHint({ onClose }: { onClose: () => void }) {
  return (
    <Group justify="space-between" mt="md" wrap="nowrap" gap="sm">
      <Text size="xs" c="dimmed">
        Der Ablauf läuft auf dem Server weiter, auch wenn du den Dialog schließt -- Fortschritt und Protokoll findest du unten in
        der Aktivitäten-Leiste.
      </Text>
      <Button variant="default" size="xs" leftSection={<IconLayoutBottombarExpand size={14} />} onClick={onClose} style={{ flexShrink: 0 }}>
        Schließen (läuft weiter)
      </Button>
    </Group>
  );
}
