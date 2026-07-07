const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers:
      init?.body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// --- Types (mirror backend/app/schemas.py) ---

export interface Contact {
  id: string;
  name: string;
  phone: string;
  notes: string | null;
  created_at: string;
}

export interface ImportResult {
  imported: number;
  skipped: number;
  errors: string[];
}

export interface Campaign {
  id: string;
  name: string;
  goal: string | null;
  script_prompt: string | null;
  status: "draft" | "running" | "stopped" | "completed";
  created_at: string;
  total_contacts: number;
  called_contacts: number;
}

export interface CampaignContactRow {
  contact: Contact;
  status: "pending" | "calling" | "done" | "failed";
  disposition: string | null;
  disposition_summary: string | null;
  call_id: string | null;
}

export interface CampaignDetail extends Campaign {
  contact_rows: CampaignContactRow[];
}

export interface Call {
  id: string;
  direction: "inbound" | "outbound";
  status: string;
  disposition: string | null;
  disposition_summary: string | null;
  contact_id: string | null;
  campaign_id: string | null;
  contact_name: string | null;
  campaign_name: string | null;
  from_number: string | null;
  to_number: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
}

export interface TranscriptTurn {
  id: number;
  role: "agent" | "caller";
  content: string;
  ts: string;
}

export interface CallDetail extends Call {
  turns: TranscriptTurn[];
}

// --- Contacts ---

export const listContacts = (search = "") =>
  request<Contact[]>(`/api/contacts?search=${encodeURIComponent(search)}`);

export const createContact = (data: { name: string; phone: string; notes?: string }) =>
  request<Contact>("/api/contacts", { method: "POST", body: JSON.stringify(data) });

export const deleteContact = (id: string) =>
  request<void>(`/api/contacts/${id}`, { method: "DELETE" });

export const importContacts = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<ImportResult>("/api/contacts/import", { method: "POST", body: form });
};

// --- Campaigns ---

export const listCampaigns = () => request<Campaign[]>("/api/campaigns");

export const createCampaign = (data: {
  name: string;
  goal?: string;
  script_prompt?: string;
  contact_ids: string[];
}) => request<Campaign>("/api/campaigns", { method: "POST", body: JSON.stringify(data) });

export const getCampaign = (id: string) => request<CampaignDetail>(`/api/campaigns/${id}`);

export const deleteCampaign = (id: string) =>
  request<void>(`/api/campaigns/${id}`, { method: "DELETE" });

export const startCampaign = (id: string) =>
  request<Campaign>(`/api/campaigns/${id}/start`, { method: "POST" });

export const stopCampaign = (id: string) =>
  request<Campaign>(`/api/campaigns/${id}/stop`, { method: "POST" });

// --- Calls ---

export const listCalls = () => request<Call[]>("/api/calls");

export const getCall = (id: string) => request<CallDetail>(`/api/calls/${id}`);

// --- Stats ---

export interface Stats {
  total_calls: number;
  total_contacts: number;
  active_campaigns: number;
  avg_duration_seconds: number | null;
  dispositions: Record<string, number>;
}

export const getStats = () => request<Stats>("/api/stats");
