import { useEffect, useState } from "react";
import { Button, Group, NumberInput, Paper, Select, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useEmailConfig, useSendTestEmail, useUpdateEmailConfig } from "@/api/hooks";
import type { EmailConfigWritePayload } from "@/api/types";
import { apiErrorMessage } from "@/utils/errors";

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: `${String(h).padStart(2, "0")}:00 Uhr` }));

export function EmailSettingsTab() {
  const { data: config } = useEmailConfig();
  const updateConfig = useUpdateEmailConfig();
  const sendTestEmail = useSendTestEmail();

  const [enabled, setEnabled] = useState(false);
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState<number | string>(587);
  const [smtpEncryption, setSmtpEncryption] = useState<"none" | "starttls" | "ssl">("starttls");
  const [smtpUsername, setSmtpUsername] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [fromAddress, setFromAddress] = useState("");
  const [fromName, setFromName] = useState("Hyper-V NetApp Backup");
  const [recipients, setRecipients] = useState("");
  const [notifyOnRestoreFailure, setNotifyOnRestoreFailure] = useState(true);
  const [dailySummaryEnabled, setDailySummaryEnabled] = useState(false);
  const [dailySummaryHour, setDailySummaryHour] = useState("7");
  const [testRecipient, setTestRecipient] = useState("");

  useEffect(() => {
    if (!config) return;
    setEnabled(config.enabled);
    setSmtpHost(config.smtp_host);
    setSmtpPort(config.smtp_port);
    setSmtpEncryption(config.smtp_encryption);
    setSmtpUsername(config.smtp_username ?? "");
    setFromAddress(config.from_address);
    setFromName(config.from_name);
    setRecipients(config.recipients);
    setNotifyOnRestoreFailure(config.notify_on_restore_failure);
    setDailySummaryEnabled(config.daily_summary_enabled);
    setDailySummaryHour(String(config.daily_summary_hour));
  }, [config]);

  function handleSave() {
    const payload: EmailConfigWritePayload = {
      enabled,
      smtp_host: smtpHost,
      smtp_port: Number(smtpPort),
      smtp_encryption: smtpEncryption,
      smtp_username: smtpUsername || null,
      smtp_password: smtpPassword || null,
      from_address: fromAddress,
      from_name: fromName,
      recipients,
      notify_on_restore_failure: notifyOnRestoreFailure,
      daily_summary_enabled: dailySummaryEnabled,
      daily_summary_hour: Number(dailySummaryHour),
    };
    updateConfig
      .mutateAsync(payload)
      .then(() => {
        setSmtpPassword("");
        notifications.show({ title: "Gespeichert", message: "E-Mail-Konfiguration wurde aktualisiert", color: "green" });
      })
      .catch((err) =>
        notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Konfiguration konnte nicht gespeichert werden."), color: "red" }),
      );
  }

  function handleSendTest() {
    sendTestEmail
      .mutateAsync(testRecipient)
      .then(() => notifications.show({ title: "Test-Mail verschickt", message: `An ${testRecipient} zugestellt`, color: "green" }))
      .catch((err) =>
        notifications.show({ title: "Test-Mail fehlgeschlagen", message: apiErrorMessage(err, "SMTP-Verbindung fehlgeschlagen."), color: "red" }),
      );
  }

  return (
    <Paper p="md" maw={560}>
      <Title order={5} mb={4}>
        E-Mail
      </Title>
      <Text size="xs" c="dimmed" mb="md">
        SMTP-Relay fuer Alert-Mails. Bei welchen Backup-Policies ein Fehlschlag gemeldet wird, wird pro Policy unter Backup &gt; Policies
        festgelegt.
      </Text>
      <Stack gap="md">
        <Switch label="E-Mail-Versand aktiviert" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} />

        <Group grow align="flex-start">
          <TextInput label="SMTP-Host" placeholder="mail.example.com" value={smtpHost} onChange={(e) => setSmtpHost(e.currentTarget.value)} />
          <NumberInput label="Port" min={1} max={65535} value={smtpPort} onChange={setSmtpPort} />
        </Group>

        <Select
          label="Verschlüsselung"
          data={[
            { value: "none", label: "Keine (Klartext)" },
            { value: "starttls", label: "STARTTLS" },
            { value: "ssl", label: "SSL/TLS (implizit, z.B. Port 465)" },
          ]}
          value={smtpEncryption}
          onChange={(v) => v && setSmtpEncryption(v as typeof smtpEncryption)}
          allowDeselect={false}
        />

        <Group grow align="flex-start">
          <TextInput
            label="SMTP-Benutzername"
            placeholder="optional, falls Auth erforderlich"
            value={smtpUsername}
            onChange={(e) => setSmtpUsername(e.currentTarget.value)}
          />
          <TextInput
            label="SMTP-Kennwort"
            type="password"
            placeholder={config?.has_password ? "•••••••• (unverändert lassen)" : "optional"}
            value={smtpPassword}
            onChange={(e) => setSmtpPassword(e.currentTarget.value)}
          />
        </Group>

        <Group grow align="flex-start">
          <TextInput label="Absenderadresse" placeholder="backup-alert@example.com" value={fromAddress} onChange={(e) => setFromAddress(e.currentTarget.value)} />
          <TextInput label="Absendername" value={fromName} onChange={(e) => setFromName(e.currentTarget.value)} />
        </Group>

        <TextInput
          label="Empfänger"
          description="Komma-getrennte Liste, gilt global für alle aktivierten Alert-Typen"
          placeholder="admin@example.com, backup-team@example.com"
          value={recipients}
          onChange={(e) => setRecipients(e.currentTarget.value)}
        />

        <Switch
          label="Bei fehlgeschlagenen Restore-/VM-Neuerstellungs-Läufen benachrichtigen"
          checked={notifyOnRestoreFailure}
          onChange={(e) => setNotifyOnRestoreFailure(e.currentTarget.checked)}
        />

        <Switch
          label="Tageszusammenfassung verschicken"
          description="Übersicht aller Backup-/Restore-/VM-Neuerstellungs-Läufe der letzten 24 Stunden"
          checked={dailySummaryEnabled}
          onChange={(e) => setDailySummaryEnabled(e.currentTarget.checked)}
        />
        {dailySummaryEnabled && (
          <Select
            label="Uhrzeit der Tageszusammenfassung"
            description="Lokale Zeitzone des Servers (HVNB_SCHEDULE_TIMEZONE)"
            data={HOUR_OPTIONS}
            value={dailySummaryHour}
            onChange={(v) => v && setDailySummaryHour(v)}
            allowDeselect={false}
            w={260}
          />
        )}

        <Group justify="flex-end">
          <Button onClick={handleSave} loading={updateConfig.isPending}>
            Speichern
          </Button>
        </Group>

        <Group align="flex-end" gap="xs" mt="md" pt="md" style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
          <TextInput
            label="Test-Mail senden an"
            placeholder="test@example.com"
            value={testRecipient}
            onChange={(e) => setTestRecipient(e.currentTarget.value)}
            style={{ flex: 1 }}
          />
          <Button variant="default" onClick={handleSendTest} loading={sendTestEmail.isPending} disabled={!testRecipient}>
            Test-Mail senden
          </Button>
        </Group>
        {config?.last_test_at && (
          <Text size="xs" c={config.last_test_error ? "red" : "dimmed"}>
            Letzter Test: {new Date(config.last_test_at).toLocaleString("de-DE")}
            {config.last_test_error ? ` — Fehler: ${config.last_test_error}` : " — erfolgreich"}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
