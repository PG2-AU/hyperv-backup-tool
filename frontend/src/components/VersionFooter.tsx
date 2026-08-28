import { Stack, Text, Tooltip } from "@mantine/core";

import { useVersion } from "@/api/hooks.settings";

function formatTimestamp(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString("de-DE") : "noch nicht gelaufen";
}

export function VersionFooter() {
  const { data: version } = useVersion();

  if (!version) return null;

  const deployedLabel = version.last_deploy_at ? new Date(version.last_deploy_at).toLocaleString("de-DE") : "unbekannt";

  return (
    <Tooltip label={version.commit ?? "unbekannter Commit"} position="top-start" openDelay={300}>
      <Stack gap={0} px="xs" py={6}>
        <Text size="xs" c="dimmed">
          Version {version.commit_short ?? "?"} (Iteration {version.commit_count ?? "?"})
        </Text>
        <Text size="xs" c="dimmed">
          Deployed: {deployedLabel}
        </Text>
        <Text size="xs" c="dimmed">
          Health-Check: {formatTimestamp(version.last_health_check_at)}
        </Text>
        <Text size="xs" c="dimmed">
          Discovery: {formatTimestamp(version.last_discovery_at)}
        </Text>
        <Text size="xs" c="dimmed">
          Snapshot-Abgleich: {formatTimestamp(version.last_snapshot_reconciliation_at)}
        </Text>
        <Text size="xs" c="dimmed">
          Retention-Cleanup: {formatTimestamp(version.last_retention_cleanup_at)}
        </Text>
        <Text size="xs" c="dimmed">
          Datei-Restore-Sicherheitsnetz: {formatTimestamp(version.last_file_restore_expiry_at)}
        </Text>
      </Stack>
    </Tooltip>
  );
}
