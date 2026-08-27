import { modals } from "@mantine/modals";

interface ConfirmActionOptions {
  title: string;
  message: string;
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
