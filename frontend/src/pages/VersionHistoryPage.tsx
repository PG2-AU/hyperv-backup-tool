import { useState } from "react";
import { Badge, Code, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";

import { useVersionHistory, type CommitInfo } from "@/api/hooks.settings";

function CommitRow({ commit }: { commit: CommitInfo }) {
  const [expanded, setExpanded] = useState(false);
  const hasBody = !!commit.body;

  return (
    <Paper withBorder p="sm">
      <Group
        justify="space-between"
        wrap="nowrap"
        align="flex-start"
        onClick={() => hasBody && setExpanded((v) => !v)}
        style={{ cursor: hasBody ? "pointer" : "default" }}
      >
        <Group gap="xs" wrap="nowrap" align="flex-start" style={{ minWidth: 0 }}>
          {hasBody ? (
            expanded ? (
              <IconChevronDown size={14} style={{ flexShrink: 0, marginTop: 4 }} />
            ) : (
              <IconChevronRight size={14} style={{ flexShrink: 0, marginTop: 4 }} />
            )
          ) : (
            <span style={{ width: 14, flexShrink: 0 }} />
          )}
          <Text size="sm" style={{ minWidth: 0 }}>
            {commit.subject}
          </Text>
        </Group>
        <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
          <Badge variant="light" color="gray" ff="monospace" size="sm">
            {commit.short_hash}
          </Badge>
          <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
            {new Date(commit.date).toLocaleString("de-DE")}
          </Text>
        </Group>
      </Group>
      {expanded && hasBody && (
        <Code block mt="xs" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {commit.body}
        </Code>
      )}
    </Paper>
  );
}

export function VersionHistoryPage() {
  const { data: commits, isLoading } = useVersionHistory(150);

  return (
    <Stack>
      <Title order={3}>Versionshistorie</Title>
      <Text size="sm" c="dimmed">
        Alle Commits, die per Push auf das verfolgte Repository ausgeliefert wurden (neueste zuerst) — Klick auf
        einen Eintrag mit Pfeil zeigt die ausführliche Beschreibung.
      </Text>

      {isLoading && <Text size="sm" c="dimmed">Lädt…</Text>}
      {!isLoading && (commits?.length ?? 0) === 0 && (
        <Text size="sm" c="dimmed">
          Keine Versionshistorie verfügbar.
        </Text>
      )}

      <Stack gap={6}>
        {commits?.map((commit) => (
          <CommitRow key={commit.hash} commit={commit} />
        ))}
      </Stack>
    </Stack>
  );
}
