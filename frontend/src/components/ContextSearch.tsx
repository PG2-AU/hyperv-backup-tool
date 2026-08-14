import { Combobox, Loader, Text, TextInput, useCombobox } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconSearch } from "@tabler/icons-react";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { apiClient } from "@/api/client";
import { resolveSearchContext } from "@/layout/navConfig";

interface SearchResult {
  type: string;
  id: string;
  label: string;
  subtitle: string;
  route: string;
}

export function ContextSearch() {
  const location = useLocation();
  const navigate = useNavigate();
  const combobox = useCombobox();
  const [query, setQuery] = useState("");
  const [debouncedQuery] = useDebouncedValue(query, 200);
  const context = resolveSearchContext(location.pathname);

  const { data: results, isFetching } = useQuery({
    queryKey: ["search", debouncedQuery, context],
    queryFn: async () => {
      const response = await apiClient.get<SearchResult[]>("/search", {
        params: { q: debouncedQuery, context },
      });
      return response.data;
    },
    enabled: debouncedQuery.length > 0,
  });

  const options = (results ?? []).map((r) => (
    <Combobox.Option value={r.route} key={`${r.type}-${r.id}`}>
      <Text size="sm" fw={500}>
        {r.label}
      </Text>
      <Text size="xs" c="dimmed">
        {r.type} · {r.subtitle}
      </Text>
    </Combobox.Option>
  ));

  return (
    <Combobox
      onOptionSubmit={(value) => {
        navigate(value);
        setQuery("");
        combobox.closeDropdown();
      }}
      store={combobox}
      withinPortal
    >
      <Combobox.Target>
        <TextInput
          placeholder={context ? `Suche in ${context}...` : "Schnellsuche..."}
          leftSection={<IconSearch size={16} />}
          rightSection={isFetching ? <Loader size={14} /> : null}
          value={query}
          w={320}
          onChange={(event) => {
            setQuery(event.currentTarget.value);
            combobox.openDropdown();
          }}
          onClick={() => combobox.openDropdown()}
          onFocus={() => combobox.openDropdown()}
        />
      </Combobox.Target>

      <Combobox.Dropdown>
        <Combobox.Options>
          {debouncedQuery.length === 0 ? (
            <Combobox.Empty>Suchbegriff eingeben...</Combobox.Empty>
          ) : options.length === 0 && !isFetching ? (
            <Combobox.Empty>Keine Treffer</Combobox.Empty>
          ) : (
            options
          )}
        </Combobox.Options>
      </Combobox.Dropdown>
    </Combobox>
  );
}
