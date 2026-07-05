"use client";
import { createContext, useContext } from "react";

interface TenantConfig {
  id: string;
  name: string;
  logo_url: string | null;
  primary_color: string;
}

const TenantContext = createContext<TenantConfig | null>(null);

export function TenantProvider({ children }: { children: React.ReactNode }) {
  // ponytail: Will resolve tenant from hostname/subdomain
  return <TenantContext.Provider value={null}>{children}</TenantContext.Provider>;
}

export const useTenant = () => useContext(TenantContext);
