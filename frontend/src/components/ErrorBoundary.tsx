import { Component, type ErrorInfo, type ReactNode } from "react";
import { Alert, Button, Stack, Text } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/** Faengt Rendering-Fehler innerhalb des Hauptinhalts ab (z.B. den echt
 * aufgetretenen Mantine-"Duplicate options"-Absturz bei einer doppelt
 * discoverten VM, siehe hyperv_clusters.py) -- ohne diesen Boundary reisst
 * ein einzelner Rendering-Fehler IRGENDWO in der App die komplette Seite
 * auf ein leeres weisses Fenster (live beobachtet: kein Navigieren mehr
 * moeglich, React haengt den gesamten Baum aus). Wird bewusst NUR um
 * <Outlet /> gelegt (siehe AppShellLayout.tsx), nicht um die gesamte App
 * inkl. Navbar/Header -- Navigation bleibt so nutzbar, der Nutzer kann zu
 * einer anderen Seite wechseln statt neu laden zu muessen. Ueber
 * `key={location.pathname}` in AppShellLayout.tsx setzt sich der Boundary
 * bei jedem Seitenwechsel automatisch zurueck (React-Klassenkomponenten
 * sind fuer error boundaries Pflicht -- es gibt keinen Hook-Ersatz dafuer). */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] Rendering-Fehler abgefangen:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <Stack p="xl" maw={640} mx="auto">
          <Alert icon={<IconAlertTriangle size={18} />} color="red" title="Diese Seite konnte nicht angezeigt werden" variant="light">
            <Stack gap="xs">
              <Text size="sm">
                Ein unerwarteter Fehler ist aufgetreten. Die Navigation links funktioniert weiterhin — wechsle zu einer
                anderen Seite, oder lade die Anwendung neu.
              </Text>
              <Text size="xs" c="dimmed" ff="monospace">
                {this.state.error.message}
              </Text>
              <Button size="xs" variant="light" onClick={() => this.setState({ error: null })} w="fit-content">
                Erneut versuchen
              </Button>
            </Stack>
          </Alert>
        </Stack>
      );
    }
    return this.props.children;
  }
}
