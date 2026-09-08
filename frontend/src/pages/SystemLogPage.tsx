import { Paper, Title } from "@mantine/core";

import { LogViewer } from "@/components/LogViewer";

// Eigene, dedizierte Seite statt des vorherigen Kopfzeilen-Drawers (fixe
// 45%-Hoehe, kein eigener Pfad/Link) -- LogViewer selbst ist unveraendert,
// wird aber jetzt mit vollem verfuegbarem Platz gerendert statt in einem
// Overlay. Die zweite Verwendungsstelle (JobsPage.tsx, LogViewer mit
// context=<Lauf/Policy-ID> fuer "Log anzeigen" auf einem konkreten
// Backup-Lauf) bleibt bewusst unangetastet -- anderer, kontextgebundener
// Anwendungsfall, keine Dopplung mit diesem globalen System Log.
export function SystemLogPage() {
  return (
    <Paper p="md" style={{ height: "calc(100vh - 112px)", display: "flex", flexDirection: "column" }}>
      <Title order={5} mb="sm">
        System Log
      </Title>
      {/* min-height:0 noetig, damit LogViewers eigenes h="100%" innerhalb
          dieses Flex-Elements eine echte Pixel-Hoehe bekommt (klassische
          Flexbox-Falle) statt sich auf die Inhaltsgroesse aufzublaehen. */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <LogViewer context={undefined} />
      </div>
    </Paper>
  );
}
