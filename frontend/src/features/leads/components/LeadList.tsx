import { useLeads } from "../api";
import { LeadCard } from "./LeadCard";

export function LeadList() {
  const { data: leads, isLoading } = useLeads();
  if (isLoading) return <div>Loading...</div>;
  return (
    <div className="space-y-2">
      {leads?.map((lead) => <LeadCard key={lead.id} lead={lead} />)}
    </div>
  );
}
