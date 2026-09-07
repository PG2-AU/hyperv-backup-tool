import type { ReactNode } from "react";
import { modals } from "@mantine/modals";

interface ConfirmActionOptions {
  title: string;
  // Meist ein einfacher String, aber gelegentlich mehrzeiliger Inhalt
  // (z.B. eine Liste konkret betroffener Objekte) -- Mantines eigenes
  // openConfirmModal nimmt fuer "children" ohnehin schon ReactNode an.
  message: ReactNode;
  confirmLabel?: string;
  color?: string;
  onConfirm: () => void;
}

/** Ersetzt window.confirm() durch einen In-App-Dialog (Mantine-Modal) im
 * gleichen Look wie der Rest der Anwendung, statt dem nativen
 * Browser-Bestaetigungsfenster. */
export function confirmAction({ title, message, confirmLabel = "Bestätigen", color = "red", onConfirm }: ConfirmActionOptions) {
  modals.openConfirmModal({
    title,
    children: message,
    labels: { confirm: confirmLabel, cancel: "Abbrechen" },
    confirmProps: { color },
    onConfirm,
  });
}
