import { Stack, Text, Tooltip } from "@mantine/core";

import { useVersion } from "@/api/hooks.settings";

export function VersionFooter() {
  const { data: version } = useVersion();

  if (!version) return null;

  const deployedLabel = version.last_deploy_at ? new Date(version.last_deploy_at).toLocaleString("de-DE") : "unbekannt";

  return (
    <Tooltip label={version.commit ?? "unbekannter Commit"} position="top-start" openDelay={300}>
      <Stack gap={0} px="xs" py={6}>
        <Text size="xs" c="dimmed">
          Version {version.commit_short ?? "?"}
        </Text>
        <Text size="xs" c="dimmed">
          Deployed: {deployedLabel}
        </Text>
      </Stack>
    </Tooltip>
  );
}
