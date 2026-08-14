import { createTheme, MantineColorsTuple } from "@mantine/core";

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
  },
});
