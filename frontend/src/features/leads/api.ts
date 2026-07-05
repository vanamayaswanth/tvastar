import useSWR from "swr";
import { apiClient } from "@/shared/lib/api-client";
import type { Lead } from "./types";

export function useLeads() {
  return useSWR<Lead[]>("/api/leads", apiClient.get);
}

export function useLead(id: string) {
  return useSWR<Lead>(`/api/leads/${id}`, apiClient.get);
}
