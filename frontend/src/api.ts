// In development, use Vite's same-origin proxy so login/API calls don't depend
// on the browser's CORS configuration or which local port Vite selected.
const API_URL = import.meta.env.DEV ? '' : (import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000');

export type StoreAccess = { id: string; name: string; role: string };
export type Session = { access_token: string; token_type: string; user: { id: string; name: string; email: string }; store: StoreAccess; available_stores: StoreAccess[] };
export type ApiMember = { id: string; name: string; email: string; role: 'Owner' | 'Manager' | 'Agent'; active: boolean };
export type ShopifyStatus = { connected: boolean; shop_domain: string | null; connection_status: string; last_synced_at: string | null; webhooks_registered: boolean; webhook_error: string | null; scopes: string[] };
export type ApiOrder = { id: string; customer: string; email: string; date: string; amount: number; currency: string; payment: string; outcome: string; city: string; region: string; items: { title: string; quantity: number; sku?: string | null; unit_price?: string | null }[]; fulfillment: string | null };
export type ApiCaseMessage = { id: string; body: string; incoming: boolean; sender_name: string | null; sent_at: string; delivery_status: string; delivery_error: string | null };
export type WhatsAppStatus = { connected: boolean; phone_number_id: string | null; waba_id: string | null; display_phone_number: string | null; verified_name: string | null; last_error: string | null; last_webhook_at: string | null; webhook_url: string | null; app_secret_configured: boolean; verify_token_configured: boolean; worker_enabled: boolean; queue: { pending: number; failed: number }; warning?: string | null };
export type ApiAudit = { id: string; action: string; change_summary: Record<string, unknown>; actor_name: string | null; occurred_at: string };
export type ApiCase = { id: string; case_ref: string; customer: string; email: string | null; subject: string; preview: string; category: string; urgency: string; sentiment: string; confidence: number; status: string; assigned: string; assigned_user_id: string | null; order_id: string | null; language: string; updated: string; unread: boolean; takeover: boolean; channel: 'whatsapp' | 'manual'; customer_phone: string | null; analysed: boolean; analysis_confidence: Record<string, number>; model_versions: Record<string, string>; routing_reason: string | null; suggestion: ApiSuggestion | null; messages: ApiCaseMessage[]; audit: ApiAudit[] };
export type ApiEvidence = { chunk_id: string; title: string; version: number; text: string; score: number | null; used: boolean };
export type ApiSuggestion = { id: string; text: string; model: string; supported: boolean; note: string | null; created_at: string; stale: boolean; evidence: ApiEvidence[] };
export type DraftState = 'ready' | 'no_message' | 'no_policy' | 'no_evidence' | 'unavailable' | 'llm_unavailable' | 'llm_error';
export type DraftResponse = { state: DraftState; message: string; evidence: ApiEvidence[]; case: ApiCase };
export type ApiPolicyVersion = { id: string; version: number; content: string; index_status: 'pending' | 'ready' | 'failed'; active: boolean; passages: number; uploaded_at: string };
export type ApiPolicy = { title: string; active_version: number | null; versions: ApiPolicyVersion[] };
export type RetrievalStatus = { embedding: { state: string; error: string | null; model: string }; vector_store: { mode: string; error: string | null }; min_score: number; top_k: number };
export type PoliciesResponse = { policies: ApiPolicy[]; retrieval?: RetrievalStatus };
export type PolicySearchResponse = { state: 'ok' | 'no_policy' | 'no_evidence' | 'unavailable'; message: string; best_score: number | null; min_score: number; passages: (ApiEvidence & { chunk_index: number })[] };
export type ClassifierStatus = { state: 'not_loaded' | 'loading' | 'ready' | 'unavailable' | 'error'; error: string | null; model_versions: Record<string, string> };

export async function request<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const apiBaseUrl = API_URL;
