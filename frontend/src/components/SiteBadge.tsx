import { Badge } from "@mantine/core";

// Standort-Badge (Settings > Standorte) -- Inventory und Standort-Tab.
export function SiteBadgeView({ site }: { site: { name: string; color: string } }) {
  return (
    <Badge size="sm" variant="light" color={site.color}>
      {site.name}
    </Badge>
  );
}
