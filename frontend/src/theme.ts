import { Badge, createTheme, MantineColorsTuple } from "@mantine/core";

const brand: MantineColorsTuple = [
  "#eef4ff",
  "#dce6fb",
  "#b6c9f2",
  "#8ea9ea",
  "#6c8fe3",
  "#567de0",
  "#4a74df",
  "#3b63c6",
  "#3157b1",
  "#22499c",
];

export const theme = createTheme({
  primaryColor: "brand",
  colors: { brand },
  defaultRadius: "md",
  fontFamily:
    "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  headings: { fontWeight: "600" },
  components: {
    Paper: {
      defaultProps: { withBorder: true },
    },
    // Mantine's Badge schneidet seinen Text per eingebautem
    // overflow:hidden/text-overflow:ellipsis ab, sobald er in einem engeren
    // Flex-/Tabellen-Kontext landet (z.B. schmale Dashboard-Spalten) --
    // live als "APP-K...", "PR...", "SUCCE..." beobachtet. Zentral hier
    // abgeschaltet statt an jeder einzelnen Badge-Stelle im Code, da kein
    // einziges Badge im Projekt eine eigene Breiten-Beschraenkung setzt
    // (gepreuft) -- Badges zeigen jetzt immer ihren vollstaendigen Text,
    // draengen ihren Container bei Bedarf stattdessen weiter auf (die
    // jeweilige Tabelle/ScrollArea faengt das ueber horizontales Scrollen
    // auf, wo vorhanden).
    Badge: Badge.extend({
      styles: {
        root: { overflow: "visible", flexShrink: 0 },
        label: { overflow: "visible", textOverflow: "unset", whiteSpace: "nowrap" },
      },
    }),
  },
});
