import { Alert, Button, Container, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { IconAlertCircle, IconServer2 } from "@tabler/icons-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiClient } from "@/api/client";
import { useAuthStore } from "@/store/authStore";

export function LoginPage() {
  const navigate = useNavigate();
  const setSession = useAuthStore((s) => s.setSession);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const loginResponse = await apiClient.post("/auth/login", { username, password });
      const token = loginResponse.data.access_token as string;
      const meResponse = await apiClient.get("/auth/me", {
        headers: { Authorization: `Bearer ${token}` },
      });
      setSession(token, meResponse.data);
      navigate("/", { replace: true });
    } catch {
      setError("Anmeldung fehlgeschlagen. Benutzername/Passwort pruefen.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Container size={420} style={{ paddingTop: "12vh" }}>
      <Stack align="center" gap={4} mb="lg">
        <IconServer2 size={40} stroke={1.5} />
        <Title order={2}>Hyper-V NetApp Backup</Title>
        <Text c="dimmed" size="sm">
          Anmelden mit lokalem Konto oder Active-Directory-Benutzer
        </Text>
      </Stack>
      <Paper p="xl" radius="md">
        <form onSubmit={handleSubmit}>
          <Stack>
            {error && (
              <Alert color="red" icon={<IconAlertCircle size={16} />}>
                {error}
              </Alert>
            )}
            <TextInput
              label="Benutzername"
              placeholder="admin oder DOMAIN-User"
              required
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
            />
            <PasswordInput
              label="Passwort"
              required
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
            />
            <Button type="submit" loading={loading} fullWidth mt="sm">
              Anmelden
            </Button>
          </Stack>
        </form>
      </Paper>
    </Container>
  );
}
