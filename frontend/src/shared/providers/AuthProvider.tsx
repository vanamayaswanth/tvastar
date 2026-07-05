"use client";
import { createContext, useContext } from "react";
import type { User } from "@/features/auth/types";

const AuthContext = createContext<{ user: User | null }>({ user: null });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // ponytail: Will hydrate from token on mount
  return <AuthContext.Provider value={{ user: null }}>{children}</AuthContext.Provider>;
}

export const useAuthContext = () => useContext(AuthContext);
