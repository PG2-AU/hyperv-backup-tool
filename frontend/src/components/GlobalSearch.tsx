import { useDeferredValue, useMemo, useRef, useState } from "react";
import { Combobox, Group, Highlight, Kbd, Loader, Text, TextInput, useCombobox } from "@mantine/core";
import { useHotkeys } from "@mantine/hooks";
import { IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "@/api/client";

// Globale Suche in der Kopfzeile (Backlog #78, Nutzer-Vorgabe 2026-10-03):
// ueber VMs, CSVs, Freigaben, Volumes, LUNs und Backup-Snapshots. Vorgabe
// "maximal performant, Treffer sofort beim Tippen": der Index (GET
// /search/index) wird einmal geladen und im Hintergrund aufgefrischt, das
// Filtern laeuft lokal ohne Server-Aufruf je Tastendruck.

interface SearchEntry {
  type: string;
  title: string;
  subtitle?: string | null;
  link: string;
}

interface IndexedEntry extends SearchEntry {
  haystack: string;
}

const GROUPS: { type: string; label: string; listLink?: (q: string) => string }[] = [
  { type: "vm", label: "Virtuelle Maschinen", listLink: (q) => `/vms?tab=vms&q=${encodeURIComponent(q)}` },
  { type: "csv", label: "Cluster Shared Volumes", listLink: (q) => `/vms?tab=csv&q=${encodeURIComponent(q)}` },
  { type: "smb", label: "SMB3-Freigaben (Hyper-V)", listLink: (q) => `/vms?tab=smb&q=${encodeURIComponent(q)}` },
  { type: "volume", label: "Volumes", listLink: (q) => `/storage?tab=volumes&q=${encodeURIComponent(q)}` },
  { type: "lun", label: "LUNs", listLink: (q) => `/storage?tab=luns&q=${encodeURIComponent(q)}` },
  { type: "cifs", label: "CIFS-Freigaben (NetApp)", listLink: (q) => `/storage?tab=cifs-shares&q=${encodeURIComponent(q)}` },
  { type: "snapshot", label: "Backup-Snapshots" },
];
const PER_GROUP = 6;

// '*' und '?' als Platzhalter (wie im WAC Virtualization Mode), sonst Teilstring.
function buildMatcher(query: string): ((text: string) => boolean) | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  if (/[*?]/.test(q)) {
    const pattern = q.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".");
    const regex = new RegExp(pattern);
    return (text) => regex.test(text);
  }
  return (text) => text.includes(q);
}

export function GlobalSearch() {
  const navigate = useNavigate();
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() });
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  // Haelt das Tippen fluessig, falls das Filtern bei sehr grossen Indizes
  // einmal laenger braucht als ein Tastenanschlag.
  const deferredQuery = useDeferredValue(query);

  // Gleich beim Laden der App holen, damit schon der erste Buchstabe Treffer
  // liefert; alle 5 Minuten bzw. beim Zurueckkehren ins Fenster auffrischen.
  const { data, isLoading } = useQuery({
    queryKey: ["search-index"],
    queryFn: async () => (await apiClient.get<SearchEntry[]>("/search/index")).data,
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    refetchOnWindowFocus: true,
  });
  const indexed = useMemo<IndexedEntry[]>(
    () => (data ?? []).map((e) => ({ ...e, haystack: `${e.title}\n${e.subtitle ?? ""}`.toLowerCase() })),
    [data],
  );

  const groups = useMemo(() => {
    const match = buildMatcher(deferredQuery);
    if (!match) return [];
    const byType = new Map<string, IndexedEntry[]>();
    for (const entry of indexed) {
      if (!match(entry.haystack)) continue;
      const list = byType.get(entry.type);
      if (list) list.push(entry);
      else byType.set(entry.type, [entry]);
    }
    return GROUPS.filter((g) => byType.has(g.type)).map((g) => {
      const all = byType.get(g.type)!;
      // Treffer, deren Titel mit dem Suchbegriff beginnt, zuerst.
      const q = deferredQuery.trim().toLowerCase();
      const sorted = [...all].sort(
        (a, b) => Number(!a.title.toLowerCase().startsWith(q)) - Number(!b.title.toLowerCase().startsWith(q)),
      );
      return { ...g, total: all.length, hits: sorted.slice(0, PER_GROUP) };
    });
  }, [indexed, deferredQuery]);

  useHotkeys([
    ["mod+K", () => inputRef.current?.focus()],
    ["/", () => inputRef.current?.focus()],
  ]);

  function go(link: string) {
    navigate(link);
    setQuery("");
    combobox.closeDropdown();
    inputRef.current?.blur();
  }

  const highlight = /[*?]/.test(deferredQuery) ? "" : deferredQuery.trim();
  const trimmed = query.trim();

  return (
    <Combobox
      store={combobox}
      onOptionSubmit={(value) => go(value)}
      width={520}
      position="bottom-start"
      withinPortal
    >
      <Combobox.Target>
        <TextInput
          ref={inputRef}
          w={{ base: 160, sm: 280, lg: 380 }}
          placeholder="Suchen: VM, CSV, Volume, LUN, Snapshot…"
          leftSection={<IconSearch size={16} />}
          rightSection={isLoading ? <Loader size="xs" /> : <Kbd size="xs">Strg K</Kbd>}
          rightSectionWidth={isLoading ? 32 : 64}
          value={query}
          onChange={(e) => {
            setQuery(e.currentTarget.value);
            combobox.openDropdown();
            combobox.updateSelectedOptionIndex();
          }}
          onFocus={() => combobox.openDropdown()}
          onBlur={() => combobox.closeDropdown()}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setQuery("");
              combobox.closeDropdown();
              inputRef.current?.blur();
            }
          }}
        />
      </Combobox.Target>

      <Combobox.Dropdown hidden={!trimmed}>
        <Combobox.Options mah="70vh" style={{ overflowY: "auto" }}>
          {isLoading && !data ? (
            <Combobox.Empty>Suchindex wird geladen…</Combobox.Empty>
          ) : groups.length === 0 ? (
            <Combobox.Empty>Keine Treffer für „{trimmed}“</Combobox.Empty>
          ) : (
            groups.map((g) => (
              <Combobox.Group key={g.type} label={`${g.label} (${g.total})`}>
                {g.hits.map((hit, index) => (
                  <Combobox.Option key={`${g.type}-${index}-${hit.link}`} value={hit.link}>
                    <Highlight highlight={highlight} size="sm" fw={500}>
                      {hit.title}
                    </Highlight>
                    {hit.subtitle && (
                      <Text size="xs" c="dimmed" lineClamp={1}>
                        {hit.subtitle}
                      </Text>
                    )}
                  </Combobox.Option>
                ))}
                {g.total > g.hits.length && g.listLink && (
                  <Combobox.Option value={g.listLink(trimmed)}>
                    <Group gap={4}>
                      <Text size="xs" c="blue">
                        Alle {g.total} in der Liste anzeigen
                      </Text>
                    </Group>
                  </Combobox.Option>
                )}
                {g.total > g.hits.length && !g.listLink && (
                  <Text size="xs" c="dimmed" px="sm" pb={4}>
                    … und {g.total - g.hits.length} weitere -- Suchbegriff genauer fassen
                  </Text>
                )}
              </Combobox.Group>
            ))
          )}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
