import { Menu } from "@mantine/core";
import { useState } from "react";

interface Position {
  x: number;
  y: number;
}

interface ContextMenuState<T> {
  position: Position;
  data: T;
}

export function useContextMenu<T>() {
  const [state, setState] = useState<ContextMenuState<T> | null>(null);

  function open(event: React.MouseEvent, data: T) {
    event.preventDefault();
    event.stopPropagation();
    setState({ position: { x: event.clientX, y: event.clientY }, data });
  }

  function close() {
    setState(null);
  }

  return { state, open, close };
}

export function ContextMenuDropdown({
  position,
  opened,
  onClose,
  children,
}: {
  position: Position | null;
  opened: boolean;
  onClose: () => void;
  children: React.ReactNode;
}) {
  if (!position) return null;
  return (
    <Menu opened={opened} onClose={onClose} shadow="md" width={230} position="bottom-start" withinPortal>
      <Menu.Target>
        <div style={{ position: "fixed", top: position.y, left: position.x, width: 0, height: 0 }} />
      </Menu.Target>
      <Menu.Dropdown>{children}</Menu.Dropdown>
    </Menu>
  );
}
