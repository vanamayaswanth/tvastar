import { create } from "zustand";
import type { AuthState } from "../types";

export const useAuth = create<AuthState>(() => ({
  user: null,
  token: null,
  isAuthenticated: false,
}));
