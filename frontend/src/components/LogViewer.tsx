import {
  ActionIcon,
  Badge,
  Button,
  Group,
  ScrollArea,
  Select,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconCopy, IconRefresh } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { apiClient } from "@/api/client";

type LogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR";

interface LogEntry {
  timestamp: string;
  level: LogLevel;
  source: string;
  context?: string | null;
  message: string;
}

const LEVEL_COLOR: Record<LogLevel, string> = {
  DEBUG: "gray",
  INFO: "blue",
  WARNING: "yellow",
  ERROR: "red",
};

function formatEntry(e: LogEntry): string {
  const ts = new Date(e.timestamp).toISOString();
  return `[${ts}] ${e.level.padEnd(7)} ${e.source}${e.context ? `(${e.context})` : ""}: ${e.message}`;
}

export function LogViewer({ context }: { context?: string }) {
  const [level, setLevel] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const { data: entries, isFetching, refetch } = useQuery({
    queryKey: ["logs", context, level, search],
    queryFn: async () => {
      const response = await apiClient.get<LogEntry[]>("/logs", {
        params: {
          context: context || undefined,
          level: level || undefined,
          search: search || undefined,
        },
      });
      return response.data;
    },
    refetchInterval: 15_000,
  });

  const formattedText = useMemo(() => (entries ?? []).map(formatEntry).join("\n"), [entries]);

  async function copyAll() {
    await navigator.clipboard.writeText(formattedText);
    notifications.show({
      title: "Log kopiert",
      message: `${entries?.length ?? 0} Eintraege in die Zwischenablage kopiert`,
      color: "green",
    });
  }

  async function copyEntry(entry: LogEntry) {
    await navigator.clipboard.writeText(formatEntry(entry));
    notifications.show({ message: "Zeile kopiert", color: "green", autoClose: 1500 });
  }

  return (
    <Stack h="100%" gap="sm">
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs" wrap="nowrap">
          <Select
            placeholder="Level"
            data={["DEBUG", "INFO", "WARNING", "ERROR"]}
            value={level}
            onChange={setLevel}
            clearable
            w={130}
          />
          <TextInput
            placeholder="Log durchsuchen..."
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            w={220}
          />
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Tooltip label="Aktualisieren">
            <ActionIcon variant="default" onClick={() => refetch()} loading={isFetching}>
              <IconRefresh size={16} />
            </ActionIcon>
          </Tooltip>
          <Button leftSection={<IconCopy size={16} />} variant="light" onClick={copyAll}>
            Alles kopieren
          </Button>
        </Group>
      </Group>

      <ScrollArea style={{ flex: 1 }} type="auto">
        <Stack gap={4}>
          {(entries ?? []).map((entry, idx) => (
            <Group
              key={idx}
              gap="xs"
              wrap="nowrap"
              align="flex-start"
              style={{
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: 12,
                cursor: "pointer",
              }}
              onClick={() => copyEntry(entry)}
            >
              <Text c="dimmed" style={{ whiteSpace: "nowrap" }}>
                {new Date(entry.timestamp).toLocaleTimeString("de-DE")}
              </Text>
              <Badge color={LEVEL_COLOR[entry.level]} size="xs" variant="filled" w={70}>
                {entry.level}
              </Badge>
              <Text style={{ whiteSpace: "nowrap" }} c="dimmed">
                {entry.source}
              </Text>
              <Text style={{ wordBreak: "break-word" }}>{entry.message}</Text>
            </Group>
          ))}
          {entries?.length === 0 && (
            <Text c="dimmed" size="sm">
              Keine Log-Eintraege fuer die aktuellen Filter.
            </Text>
          )}
        </Stack>
      </ScrollArea>
    </Stack>
  );
}
