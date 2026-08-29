import { Fragment, useState } from "react";
import { ActionIcon, Checkbox, Loader, ScrollArea, Table, Text } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconFile, IconFolder } from "@tabler/icons-react";

import { useBrowseFileRestore } from "@/api/hooks";
import { formatBytes } from "@/utils/format";
import type { FileEntry } from "@/api/types";

interface FileTreeNodeProps {
  runId: string;
  path: string;
  entry: FileEntry;
  depth: number;
  selected: Map<string, boolean>;
  onToggleSelect: (path: string, isDirectory: boolean, checked: boolean) => void;
}

/** Ein Knoten (Datei oder Ordner) im Datei-Baum. Ordner werden erst beim
 * Aufklappen abgefragt (lazy) und bleiben danach dank React-Query-Cache
 * (queryKey inkl. Pfad, siehe useBrowseFileRestore) beim Zu-/Aufklappen
 * ohne erneuten Netzwerk-Roundtrip sichtbar -- kein Verlust der bereits
 * gesehenen Struktur wie bei einer reinen Drill-Down-Navigation. */
function FileTreeNode({ runId, path, entry, depth, selected, onToggleSelect }: FileTreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const { data: children, isLoading } = useBrowseFileRestore(runId, path, entry.is_directory && expanded);
  const sortedChildren = [...(children ?? [])].sort((a, b) => {
    if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <>
      <Table.Tr>
        <Table.Td style={{ paddingLeft: depth * 20 + 8, whiteSpace: "nowrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {entry.is_directory ? (
              <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => setExpanded((v) => !v)}>
                {expanded ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
              </ActionIcon>
            ) : (
              <span style={{ display: "inline-block", width: 22 }} />
            )}
            <Checkbox
              checked={selected.has(path)}
              onChange={(e) => onToggleSelect(path, entry.is_directory, e.currentTarget.checked)}
            />
            {entry.is_directory ? <IconFolder size={16} /> : <IconFile size={16} />}
            <Text size="sm">{entry.name}</Text>
          </span>
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
      {entry.is_directory && expanded && isLoading && (
        <Table.Tr>
          <Table.Td colSpan={3} style={{ paddingLeft: (depth + 1) * 20 + 8 }}>
            <Loader size="xs" />
          </Table.Td>
        </Table.Tr>
      )}
      {entry.is_directory && expanded && !isLoading && sortedChildren.length === 0 && (
        <Table.Tr>
          <Table.Td colSpan={3} style={{ paddingLeft: (depth + 1) * 20 + 8 }}>
            <Text size="sm" c="dimmed">
              Ordner ist leer.
            </Text>
          </Table.Td>
        </Table.Tr>
      )}
      {entry.is_directory &&
        expanded &&
        sortedChildren.map((child) => (
          <Fragment key={child.name}>
            <FileTreeNode
              runId={runId}
              path={`${path}\\${child.name}`}
              entry={child}
              depth={depth + 1}
              selected={selected}
              onToggleSelect={onToggleSelect}
            />
          </Fragment>
        ))}
    </>
  );
}

interface FileBrowserProps {
  runId: string;
  rootPath: string;
  selected: Map<string, boolean>;
  onToggleSelect: (path: string, isDirectory: boolean, checked: boolean) => void;
}

/** Datei-Baum fuer eine gemountete VHDX (Datei-Restore-Session). Zeigt die
 * Wurzel bereits aufgeklappt, weitere Ordner werden per Klick auf den Pfeil
 * ein-/ausgeklappt statt die Ansicht komplett zu wechseln (fruehere Version
 * nutzte Breadcrumb+Drill-Down mit vollem Ansichtswechsel je Ordner). */
export function FileBrowser({ runId, rootPath, selected, onToggleSelect }: FileBrowserProps) {
  const { data: rootEntries, isLoading } = useBrowseFileRestore(runId, rootPath, true);
  const sortedRoot = [...(rootEntries ?? [])].sort((a, b) => {
    if (a.is_directory !== b.is_directory) return a.is_directory ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <ScrollArea.Autosize mah={360} type="auto">
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Name</Table.Th>
            <Table.Th>Größe</Table.Th>
            <Table.Th>Geändert</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {isLoading && (
            <Table.Tr>
              <Table.Td colSpan={3}>
                <Loader size="sm" />
              </Table.Td>
            </Table.Tr>
          )}
          {!isLoading && sortedRoot.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={3}>
                <Text size="sm" c="dimmed">
                  Ordner ist leer.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
          {sortedRoot.map((entry) => (
            <Fragment key={entry.name}>
              <FileTreeNode
                runId={runId}
                path={`${rootPath}\\${entry.name}`}
                entry={entry}
                depth={0}
                selected={selected}
                onToggleSelect={onToggleSelect}
              />
            </Fragment>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea.Autosize>
  );
}
