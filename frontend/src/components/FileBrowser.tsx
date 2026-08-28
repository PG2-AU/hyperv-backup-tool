import { useState } from "react";
import { Anchor, Breadcrumbs, Checkbox, Group, Loader, ScrollArea, Table, Text } from "@mantine/core";
import { IconFile, IconFolder } from "@tabler/icons-react";

import { useBrowseFileRestore } from "@/api/hooks";
import { formatBytes } from "@/utils/format";

interface FileBrowserProps {
  runId: string;
  rootPath: string;
  selected: Set<string>;
  onToggleSelect: (path: string, checked: boolean) => void;
}

/** Datei-Browser fuer eine gemountete VHDX (Datei-Restore-Session, siehe
 * FileRestoreWizardModal-Teil in RestoreWizardModal.tsx). Haelt selbst nur
 * den aktuellen Ordner (currentPath); die Auswahl bleibt beim Aufrufer, damit
 * sie ueber Ordnerwechsel hinweg erhalten bleibt (Nutzer-Vorgabe: mehrere
 * Dateien/Ordner aus verschiedenen Stellen des Dateisystems auswaehlen). */
export function FileBrowser({ runId, rootPath, selected, onToggleSelect }: FileBrowserProps) {
  const [currentPath, setCurrentPath] = useState(rootPath);
  const { data: entries, isLoading } = useBrowseFileRestore(runId, currentPath, true);

  // Breadcrumbs relativ zur Mount-Wurzel -- der Nutzer soll den absoluten
  // Proxy-Pfad (C:\hvnb_filerestore\...) nicht sehen muessen.
  const relative = currentPath.slice(rootPath.length).replace(/^\\+/, "");
  const segments = relative ? relative.split("\\") : [];

  function pathFor(depth: number): string {
    return depth === 0 ? rootPath : `${rootPath}\\${segments.slice(0, depth).join("\\")}`;
  }

  const sortedEntries = [...(entries ?? [])].sort((a, b) => {
    if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <>
      <Breadcrumbs separator="›">
        <Anchor size="sm" onClick={() => setCurrentPath(pathFor(0))}>
          Wurzel
        </Anchor>
        {segments.map((seg, i) => (
          <Anchor key={i} size="sm" onClick={() => setCurrentPath(pathFor(i + 1))}>
            {seg}
          </Anchor>
        ))}
      </Breadcrumbs>

      {isLoading && <Loader size="sm" mt="xs" />}

      {!isLoading && (
        <ScrollArea.Autosize mah={360} type="auto" mt="xs">
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36} />
              <Table.Th>Name</Table.Th>
              <Table.Th>Größe</Table.Th>
              <Table.Th>Geändert</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {sortedEntries.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={4}>
                  <Text size="sm" c="dimmed">
                    Ordner ist leer.
                  </Text>
                </Table.Td>
              </Table.Tr>
            )}
            {sortedEntries.map((entry) => {
              const fullPath = `${currentPath}\\${entry.name}`;
              return (
                <Table.Tr key={entry.name}>
                  <Table.Td>
                    <Checkbox
                      checked={selected.has(fullPath)}
                      onChange={(e) => onToggleSelect(fullPath, e.currentTarget.checked)}
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap="xs" wrap="nowrap">
                      {entry.is_directory ? <IconFolder size={16} /> : <IconFile size={16} />}
                      {entry.is_directory ? (
                        <Anchor size="sm" onClick={() => setCurrentPath(fullPath)}>
                          {entry.name}
                        </Anchor>
                      ) : (
                        <Text size="sm">{entry.name}</Text>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {entry.is_directory ? "-" : formatBytes(entry.size_bytes)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Text size="sm" c="dimmed">
                      {entry.modified_at ? new Date(entry.modified_at).toLocaleString("de-DE") : "-"}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
        </ScrollArea.Autosize>
      )}
    </>
  );
}
