import { ActionIcon, TextInput } from "@mantine/core";
import { IconSearch, IconX } from "@tabler/icons-react";

interface SearchInputProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  w?: number;
}

// Volltextsuche mit Loeschen-Button ("X"), sobald ein Suchbegriff eingegeben
// wurde -- gemeinsame Komponente, damit alle Tabellen (Storage-Reiter etc.)
// dasselbe Verhalten haben statt es an jeder Stelle einzeln nachzubauen.
export function SearchInput({ value, onChange, placeholder = "Suchen…", w = 280 }: SearchInputProps) {
  return (
    <TextInput
      placeholder={placeholder}
      leftSection={<IconSearch size={14} />}
      rightSection={
        value ? (
          <ActionIcon variant="subtle" color="gray" size="sm" onClick={() => onChange("")}>
            <IconX size={14} />
          </ActionIcon>
        ) : null
      }
      value={value}
      onChange={(e) => onChange(e.currentTarget.value)}
      w={w}
    />
  );
}
