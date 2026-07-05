/** Call record TypeScript interfaces */
export interface CallRecord {
  id: string;
  leadId: string;
  direction: 'outbound' | 'inbound';
  disposition: 'completed' | 'rnr' | 'voicemail' | 'busy' | 'failed' | 'transferred';
  durationSeconds: number;
  startedAt: string;
}
