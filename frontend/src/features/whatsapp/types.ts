/** WhatsApp message TypeScript interfaces */
export interface WhatsAppMessage {
  id: string;
  leadId: string;
  direction: 'inbound' | 'outbound';
  content: string;
  status: 'sent' | 'delivered' | 'read' | 'failed';
  handlerType: 'ai' | 'human';
  sentAt: string;
}
