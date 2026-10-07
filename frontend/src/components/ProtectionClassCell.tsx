import { Badge, Menu, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconAlertTriangle, IconCheck, IconX } from "@tabler/icons-react";

import { useAssignProtectionClass, useGroupFit, useProtectionClasses, useProtectionStatus } from "@/api/hooks.protectionClasses";
import type { ProtectionObjectStatus, ProtectionObjectType } from "@/api/hooks.protectionClasses";
import { useAuthStore } from "@/store/authStore";
import { apiErrorMessage } from "@/utils/errors";

// Schutzklasse eines Objekts als Badge (Backlog #86): Farbe der Klasse,
// Warnsymbol + Gruende bei Verstoss; Klick oeffnet die Auswahl zum Zuweisen
// (Recht backup:create). In den Inventory-Tabellen und unter Backup >
// Schutzklassen verwendet.

export function ProtectionClassBadge({ status }: { status?: ProtectionObjectStatus }) {
  if (!status || status.status === "unassigned") {
    return (
      <Badge variant="outline" color="gray" size="sm">
        keine
      </Badge>
    );
  }
  const violated = status.status === "violation";
  const badge = (
    <Badge
      variant="light"
      color={status.class_color ?? "blue"}
      size="sm"
      leftSection={violated ? <IconAlertTriangle size={12} color="var(--mantine-color-red-6)" /> : <IconCheck size={12} />}
      style={violated ? { outline: "1px solid var(--mantine-color-red-5)" } : undefined}
    >
      {status.class_name}
    </Badge>
  );
  const notes = status.notes ?? [];
  if (!violated) {
    if (notes.length === 0) return <Tooltip label="Schutzklasse erfüllt">{badge}</Tooltip>;
    return (
      <Tooltip
        multiline
        maw={460}
        label={
          <>
            <Text size="xs" fw={600}>
              Schutzklasse {status.class_name} laut Konfiguration erfüllt
            </Text>
            {notes.map((n) => (
              <Text size="xs" key={n}>
                • {n}
              </Text>
            ))}
          </>
        }
      >
        {badge}
      </Tooltip>
    );
  }
  return (
    <Tooltip
      multiline
      maw={460}
      label={
        <>
          <Text size="xs" fw={600}>
            Schutzklasse {status.class_name} nicht erfüllt:
          </Text>
          {status.violations.map((v) => (
            <Text size="xs" key={v}>
              • {v}
            </Text>
          ))}
          {notes.map((n) => (
            <Text size="xs" key={n}>
              • {n}
            </Text>
          ))}
          {status.suggested_groups.length > 0 && (
            <Text size="xs" mt={4}>
              Passende Protection Group: {status.suggested_groups.join(", ")}
            </Text>
          )}
        </>
      }
    >
      {badge}
    </Tooltip>
  );
}

export function ProtectionClassCell({
  objectType,
  clusterId,
  name,
}: {
  objectType: ProtectionObjectType;
  clusterId?: string | null;
  // VM-Name, CSV-Name bzw. 'server|share'
  name: string;
}) {
  const canManage = useAuthStore((s) => s.hasPermission)("backup:create");
  const { data: statuses } = useProtectionStatus();
  const { data: classes = [] } = useProtectionClasses();
  const assign = useAssignProtectionClass();
  const status = statuses?.find((s) => s.object_type === objectType && s.cluster_id === clusterId && s.name === name);

  function set(classId: string | null) {
    if (!clusterId) return;
    assign.mutate(
      { class_id: classId, objects: [{ object_type: objectType, cluster_id: clusterId, name }] },
      { onError: (err) => notifications.show({ title: "Fehler", message: apiErrorMessage(err, "Zuweisung fehlgeschlagen."), color: "red" }) },
    );
  }

  if (!canManage || !clusterId) return <ProtectionClassBadge status={status} />;
  return (
    <span onClick={(e) => e.stopPropagation()}>
      <Menu position="bottom-start" withinPortal>
        <Menu.Target>
          <span style={{ cursor: "pointer", display: "inline-block" }}>
            <ProtectionClassBadge status={status} />
          </span>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Label>Schutzklasse zuweisen</Menu.Label>
          {classes.length === 0 && (
            <Menu.Item disabled>Noch keine Klassen -- unter Backup &gt; Schutzklassen anlegen</Menu.Item>
          )}
          {classes.map((c) => (
            <Menu.Item
              key={c.id}
              onClick={() => set(c.id)}
              rightSection={status?.class_id === c.id ? <IconCheck size={14} /> : undefined}
              leftSection={<Badge size="xs" circle color={c.color} />}
            >
              {c.name}
            </Menu.Item>
          ))}
          {status?.class_id && (
            <>
              <Menu.Divider />
              <Menu.Item leftSection={<IconX size={14} />} onClick={() => set(null)}>
                Keine Schutzklasse
              </Menu.Item>
            </>
          )}
        </Menu.Dropdown>
      </Menu>
    </span>
  );
}

/** Welche Schutzklassen eine Protection Group mit ihren Policies und
 *  Zeitplaenen erfuellt -- berechnet (Backup > Protection Groups). Erfuellte
 *  Klassen farbig, nicht erfuellte blass mit den Gruenden im Tooltip. */
export function GroupClassFitCell({ groupId }: { groupId: string }) {
  const { data } = useGroupFit();
  const fit = data?.find((g) => g.group_id === groupId);
  if (!fit || fit.classes.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        –
      </Text>
    );
  }
  return (
    <span style={{ display: "inline-flex", gap: 4, flexWrap: "wrap" }}>
      {fit.classes.map((c) =>
        c.fits ? (
          <Tooltip key={c.class_id} label={`erfüllt ${c.class_name}`}>
            <Badge size="sm" variant="light" color={c.class_color ?? "blue"} leftSection={<IconCheck size={12} />}>
              {c.class_name}
            </Badge>
          </Tooltip>
        ) : (
          <Tooltip
            key={c.class_id}
            multiline
            maw={460}
            label={
              <>
                <Text size="xs" fw={600}>
                  {c.class_name} nicht erfüllt:
                </Text>
                {c.reasons.map((r) => (
                  <Text size="xs" key={r}>
                    • {r}
                  </Text>
                ))}
              </>
            }
          >
            <Badge size="sm" variant="outline" color="gray" style={{ opacity: 0.6 }}>
              {c.class_name}
            </Badge>
          </Tooltip>
        ),
      )}
    </span>
  );
}
