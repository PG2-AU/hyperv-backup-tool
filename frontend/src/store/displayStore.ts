import { create } from "zustand";
import { persist } from "zustand/middleware";

// Anzeige-Einstellungen sind eine reine Geraete-/Browser-Praeferenz (wie das
// Farbschema), daher bewusst nur im localStorage des jeweiligen Browsers
// persistiert -- kein Server-/Benutzer-Datensatz noetig, analog zu
// useAuthStore.

export type ContentFontSize = "small" | "normal" | "large";

// Mantine-Standard-Skala (theme.ts setzt keine eigene fontSizes-Skala):
// xs=0.75rem, sm=0.875rem, md=1rem, lg=1.125rem, xl=1.25rem. Der Scope-
// Faktor skaliert diese Basiswerte, statt fixe Groessen zu definieren --
// so bleiben die relativen Groessenunterschiede zwischen den Mantine-
// Stufen (Tabellen, Badges, Buttons etc. nutzen unterschiedliche Stufen)
// erhalten.
export const CONTENT_FONT_SCALE: Record<ContentFontSize, number> = {
  small: 0.875,
  normal: 1,
  large: 1.15,
};

export const LOG_FONT_SIZE_OPTIONS = [10, 11, 12, 13, 14, 16] as const;

interface DisplayState {
  contentFontSize: ContentFontSize;
  logFontSizePx: number;
  setContentFontSize: (value: ContentFontSize) => void;
  setLogFontSizePx: (value: number) => void;
}

export const useDisplayStore = create<DisplayState>()(
  persist(
    (set) => ({
      contentFontSize: "normal",
      logFontSizePx: 12,
      setContentFontSize: (value) => set({ contentFontSize: value }),
      setLogFontSizePx: (value) => set({ logFontSizePx: value }),
    }),
    { name: "hvnb-display" },
  ),
);
