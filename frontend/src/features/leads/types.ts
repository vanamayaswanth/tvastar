export type LeadStage =
  | "received"
  | "calling"
  | "qualified"
  | "assigned"
  | "callback_tracking"
  | "site_visit_booked"
  | "unreachable"
  | "requiring_manual_review";

export type LeadClassification = "hot" | "warm" | "cold";

export interface Lead {
  id: string;
  tenant_id: string;
  project_id: string;
  prospect_name: string;
  phone_number: string;
  stage: LeadStage;
  consent_status: "pending" | "granted" | "revoked";
  engagement_locked: boolean;
  lead_score: number | null;
  classification: LeadClassification | null;
  created_at: string;
}
