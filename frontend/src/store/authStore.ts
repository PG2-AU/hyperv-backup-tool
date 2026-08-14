import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface CurrentUser {
  id: string;
  username: string;
  display_name: string;
  permissions: string[];
}

interface AuthState {
  token: string | null;
  user: CurrentUser | null;
  setSession: (token: string, user: CurrentUser) => void;
  logout: () => void;
  hasPermission: (permission: string) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      setSession: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
      hasPermission: (permission) => get().user?.permissions.includes(permission) ?? false,
    }),
    { name: "hvnb-auth" },
  ),
);
