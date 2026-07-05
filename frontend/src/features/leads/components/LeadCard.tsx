import type { Lead } from "../types";

export function LeadCard({ lead }: { lead: Lead }) {
  return (
    <div className="rounded border p-4">
      <h3 className="font-medium">{lead.prospect_name}</h3>
      <p className="text-sm text-gray-500">{lead.stage}</p>
    </div>
  );
}
