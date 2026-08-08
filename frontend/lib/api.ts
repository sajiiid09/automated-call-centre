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

export type DialingMode = "simulated" | "twilio";

export interface Campaign {
  id: string;
  name: string;
  goal: string | null;
  script_prompt: string | null;
  status: "draft" | "running" | "stopped" | "completed";
  created_at: string;
  total_contacts: number;
  called_contacts: number;
  /** "twilio" means starting this campaign places real phone calls. */
  dialing_mode: DialingMode;
}

export interface CampaignContactRow {
  contact: Contact;
  status: "pending" | "calling" | "done" | "failed";
  disposition: string | null;
  disposition_summary: string | null;
  call_id: string | null;
  /** live status of this contact's call: initiated | ringing | in_progress | … */
  call_status: string | null;
  /** null in simulated mode; false when the number is not allowlisted */
  dialable: boolean | null;
}

export interface CampaignDetail extends Campaign {
  contact_rows: CampaignContactRow[];
}

export interface Call {
  id: string;
  direction: "inbound" | "outbound";
  twilio_sid: string | null;
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

export interface AgentProfile {
  company_name: string;
  /** Free text; $company_name and $contact_name are substituted. */
  greeting_template: string;
  persona: string | null;
  /** Similarity a caller's question must reach to skip the LLM entirely. */
  faq_threshold: number;
  rag_top_k: number;
  rag_min_score: number;
  updated_at: string;
}

export interface Faq {
  id: string;
  question: string;
  /** Spoken to the caller verbatim on a match — no LLM rewording. */
  answer: string;
  enabled: boolean;
  hit_count: number;
  created_at: string;
  /** false while the question has no embedding; such rows never match. */
  indexed: boolean;
}

export interface KbDocument {
  id: string;
  title: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "pending" | "processing" | "ready" | "failed";
  error: string | null;
  chunk_count: number;
  created_at: string;
}

export interface KnowledgeSearchResult {
  faq: { id: string; question: string; answer: string; score: number } | null;
  threshold: number;
  /** true when this question would be answered without calling the LLM. */
  would_bypass_llm: boolean;
  chunks: { title: string; content: string; score: number }[];
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

/** In real dialing mode the backend requires confirm_real before it will dial. */
export const startCampaign = (id: string, opts?: { confirm_real?: boolean }) =>
  request<Campaign>(`/api/campaigns/${id}/start`, {
    method: "POST",
    ...(opts ? { body: JSON.stringify(opts) } : {}),
  });

export const stopCampaign = (id: string) =>
  request<Campaign>(`/api/campaigns/${id}/stop`, { method: "POST" });

// --- Calls ---

export const listCalls = (filters?: {
  direction?: string;
  campaign_id?: string;
  disposition?: string;
}) => {
  const query = new URLSearchParams(
    Object.entries(filters ?? {}).filter(([, v]) => v) as [string, string][],
  ).toString();
  return request<Call[]>(`/api/calls${query ? `?${query}` : ""}`);
};

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

// --- Knowledge ---

export const getAgentProfile = () => request<AgentProfile>("/api/knowledge/profile");

export const updateAgentProfile = (data: Partial<Omit<AgentProfile, "updated_at">>) =>
  request<AgentProfile>("/api/knowledge/profile", {
    method: "PATCH",
    body: JSON.stringify(data),
  });

export const listFaqs = () => request<Faq[]>("/api/knowledge/faqs");

export const createFaq = (data: { question: string; answer: string }) =>
  request<Faq>("/api/knowledge/faqs", { method: "POST", body: JSON.stringify(data) });

export const updateFaq = (
  id: string,
  data: { question?: string; answer?: string; enabled?: boolean },
) => request<Faq>(`/api/knowledge/faqs/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const deleteFaq = (id: string) =>
  request<void>(`/api/knowledge/faqs/${id}`, { method: "DELETE" });

export const listKbDocuments = () => request<KbDocument[]>("/api/knowledge/documents");

/** Returns immediately with status "pending"; poll the list until "ready". */
export const uploadKbDocument = (file: File) => {
  const form = new FormData();
  form.append("file", file);
  return request<KbDocument>("/api/knowledge/documents", { method: "POST", body: form });
};

export const reindexKbDocument = (id: string) =>
  request<KbDocument>(`/api/knowledge/documents/${id}/reindex`, { method: "POST" });

export const deleteKbDocument = (id: string) =>
  request<void>(`/api/knowledge/documents/${id}`, { method: "DELETE" });

export const searchKnowledge = (query: string, top_k?: number) =>
  request<KnowledgeSearchResult>("/api/knowledge/search", {
    method: "POST",
    body: JSON.stringify({ query, top_k }),
  });
