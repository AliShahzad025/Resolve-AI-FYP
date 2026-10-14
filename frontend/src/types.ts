export type Page = 'overview' | 'orders' | 'complaints' | 'policies' | 'settings';
export type Tier = 'High' | 'Medium' | 'Low' | 'Unavailable';
export type CaseStatus = 'Open' | 'In progress' | 'Escalated' | 'Resolved';
export type Urgency = 'High' | 'Medium' | 'Low';
export interface Order {
  id: string; customer: string; email: string; initials: string; city: string;
  date: string; amount: number; payment: string; outcome: string; courier: string;
  score: number | null; history: number; priorRefunds: number; items: string;
}
export interface Message { id: string; body: string; incoming: boolean; time: string; }
export interface Complaint {
  id: string; customer: string; initials: string; subject: string; preview: string;
  category: string; urgency: Urgency; sentiment: string; confidence: number;
  status: CaseStatus; assigned: string; orderId: string; language: string;
  updated: string; unread: boolean; takeover: boolean; messages: Message[]; draft: string;
}
export interface PolicyVersion { version: number; content: string; date: string; }
export interface Policy { id: string; title: string; description: string; activeVersion: number | null; versions: PolicyVersion[]; }
export interface Staff { id: string; name: string; email: string; role: 'Owner' | 'Manager' | 'Agent'; active: boolean; }
export interface Settings {
  store: string; domain: string; shopifyConnected: boolean; whatsappConnected: boolean;
  phoneId: string; autoReply: boolean; highUrgency: boolean; confidence: number; lastSynced: string;
}
