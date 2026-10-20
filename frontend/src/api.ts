const API_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000';

export type StoreAccess = { id: string; name: string; role: string };
export type Session = { access_token: string; token_type: string; user: { id: string; name: string; email: string }; store: StoreAccess; available_stores: StoreAccess[] };
export type ApiMember = { id: string; name: string; email: string; role: 'Owner' | 'Manager' | 'Agent'; active: boolean };
export type ShopifyStatus = { connected: boolean; shop_domain: string | null; connection_status: string; last_synced_at: string | null; webhooks_registered: boolean; webhook_error: string | null; scopes: string[] };
export type ApiOrder = { id: string; customer: string; email: string; date: string; amount: number; currency: string; payment: string; outcome: string; city: string; region: string; items: { title: string; quantity: number; sku?: string | null; unit_price?: string | null }[]; fulfillment: string | null };

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
