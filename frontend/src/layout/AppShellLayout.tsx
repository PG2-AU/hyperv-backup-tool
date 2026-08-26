import {
  ActionIcon,
  AppShell,
  Box,
  Burger,
  Drawer,
  Group,
  Menu,
  NavLink,
  ScrollArea,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
  useMantineColorScheme,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { IconChevronDown, IconLogout, IconMoon, IconSun, IconTerminal2, IconUserCircle } from "@tabler/icons-react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";

import logo from "@/assets/logo.png";
import { ContextSearch } from "@/components/ContextSearch";
import { LogViewer } from "@/components/LogViewer";
import { RunningJobsIndicator } from "@/components/RunningJobsIndicator";
import { VersionFooter } from "@/components/VersionFooter";
import { NAV_ITEMS, resolveSearchContext } from "@/layout/navConfig";
import { useAuthStore } from "@/store/authStore";

export function AppShellLayout() {
  const [navOpened, { toggle: toggleNav }] = useDisclosure();
  const [logsOpened, { open: openLogs, close: closeLogs }] = useDisclosure(false);
  const location = useLocation();
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const { colorScheme, toggleColorScheme } = useMantineColorScheme();
  const isDark = colorScheme === "dark";

  const currentContext = resolveSearchContext(location.pathname, location.search);

  function isActive(path?: string) {
    if (!path) return false;
    const currentPath = location.pathname + location.search;
    if (path.includes("?")) return currentPath === path;
    if (path === "/") return location.pathname === "/";
    return location.pathname.startsWith(path);
  }

  return (
    <AppShell
      header={{ height: 60 }}
      navbar={{ width: 280, breakpoint: "sm", collapsed: { mobile: !navOpened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group wrap="nowrap">
            <Burger opened={navOpened} onClick={toggleNav} hiddenFrom="sm" size="sm" />
            <Box bg="white" px={8} py={4} style={{ borderRadius: 6, lineHeight: 0 }}>
              <img src={logo} alt="Advanced Unibyte" height={24} style={{ display: "block" }} />
            </Box>
            <Title order={4} visibleFrom="sm">
              Hyper-V NetApp Backup
            </Title>
          </Group>

          <Group wrap="nowrap">
            <ContextSearch />
            <RunningJobsIndicator />
            <Tooltip label="Troubleshooting-Log">
              <ActionIcon variant="default" size="lg" onClick={openLogs}>
                <IconTerminal2 size={18} />
              </ActionIcon>
            </Tooltip>
            <Menu shadow="md" width={200} position="bottom-end">
              <Menu.Target>
                <UnstyledButton>
                  <Group gap={6} wrap="nowrap">
                    <IconUserCircle size={22} />
                    <Text size="sm" visibleFrom="sm">
                      {user?.display_name ?? user?.username}
                    </Text>
                    <IconChevronDown size={14} />
                  </Group>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={isDark ? <IconSun size={16} /> : <IconMoon size={16} />} onClick={() => toggleColorScheme()}>
                  {isDark ? "Heller Modus" : "Dunkler Modus"}
                </Menu.Item>
                <Menu.Divider />
                <Menu.Item
                  leftSection={<IconLogout size={16} />}
                  onClick={() => {
                    logout();
                    navigate("/login", { replace: true });
                  }}
                >
                  Abmelden
                </Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs">
        <AppShell.Section grow component={ScrollArea}>
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            if (!item.children) {
              return (
                <NavLink
                  key={item.label}
                  label={item.label}
                  leftSection={<Icon size={18} stroke={1.5} />}
                  component={Link}
                  to={item.path!}
                  active={isActive(item.path)}
                />
              );
            }
            const anyChildActive = item.children.some((c) => isActive(c.path));
            return (
              <NavLink
                key={item.label}
                label={item.label}
                leftSection={<Icon size={18} stroke={1.5} />}
                defaultOpened={anyChildActive}
                childrenOffset={28}
              >
                {item.children.map((child) => (
                  <NavLink
                    key={child.path}
                    label={child.label}
                    component={Link}
                    to={child.path}
                    active={isActive(child.path)}
                  />
                ))}
              </NavLink>
            );
          })}
        </AppShell.Section>
        <AppShell.Section style={{ borderTop: "1px solid var(--mantine-color-default-border)" }}>
          <VersionFooter />
        </AppShell.Section>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>

      <Drawer
        opened={logsOpened}
        onClose={closeLogs}
        position="bottom"
        size="45%"
        title={`Troubleshooting-Log${currentContext ? ` – ${currentContext}` : ""}`}
      >
        <LogViewer context={undefined} />
      </Drawer>
    </AppShell>
  );
}
