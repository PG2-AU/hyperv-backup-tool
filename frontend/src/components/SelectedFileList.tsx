import { ActionIcon, Group, ScrollArea, Stack, Text } from "@mantine/core";
import { IconFile, IconFolder, IconX } from "@tabler/icons-react";

interface SelectedFileListProps {
  rootPath: string;
  selected: Map<string, boolean>;
  onRemove: (path: string) => void;
}

/** Liste der fuer den Datei-Restore ausgewaehlten Elemente, unterhalb des
 * FileBrowser/FileTree angezeigt -- zeigt den Pfad relativ zur Mount-Wurzel
 * (der absolute Proxy-Pfad ist fuer den Nutzer irrelevant) und erlaubt das
 * Abwaehlen einzelner Elemente, ohne erneut im Baum danach suchen zu muessen. */
export function SelectedFileList({ rootPath, selected, onRemove }: SelectedFileListProps) {
  if (selected.size === 0) {
    return (
      <Text size="sm" c="dimmed">
        Noch keine Dateien/Ordner ausgewählt.
      </Text>
    );
  }

  const paths = Array.from(selected.keys()).sort();

  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        {selected.size} Element(e) ausgewählt
      </Text>
      <ScrollArea.Autosize mah={180} type="auto">
        <Stack gap={4}>
          {paths.map((path) => {
            const relative = path.slice(rootPath.length).replace(/^\\+/, "");
            return (
              <Group key={path} gap="xs" wrap="nowrap" justify="space-between">
                <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                  {selected.get(path) ? <IconFolder size={14} style={{ flexShrink: 0 }} /> : <IconFile size={14} style={{ flexShrink: 0 }} />}
                  <Text size="sm" ff="monospace" truncate>
                    {relative || path}
                  </Text>
                </Group>
                <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => onRemove(path)}>
                  <IconX size={14} />
                </ActionIcon>
              </Group>
            );
          })}
        </Stack>
      </ScrollArea.Autosize>
    </Stack>
  );
}
