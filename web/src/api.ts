// Thin client for the server's JSON API (see apartment_bot/server.py).

export type Decision = "PASS" | "NEEDS_REVIEW" | "REJECT" | "PENDING";

export interface StatusDef {
  key: string;
  label: string;
  color: string;
  sort_order: number;
  needs_note: number;
  built_in: number;
}

export interface MyStatus {
  status: string;
  note: string | null;
  source: "human" | "gpt" | "system";
  updated_at: string;
}

export interface Contact {
  name: string | null;
  facebook_profile: string | null;
  phones: string[];
  whatsapp: string[];
  best: string;
}

export interface Photos {
  items: { idx: number; src: string; local: boolean }[];
  more_on_facebook: number;
}

export interface Facts {
  listing_type?: string | null;
  rent_ils?: number | null;
  total_monthly_ils_known?: number | null;
  city?: string | null;
  neighborhood?: string | null;
  street?: string | null;
  location_summary_en?: string | null;
  rooms?: number | null;
  size_sqm?: number | null;
  floor?: string | null;
  move_in_date?: string | null;
  furnished?: string | null;
  broker?: string | null;
}

export interface Card {
  post_id: string;
  url: string;
  text: string;
  text_truncated: boolean;
  posted_at: string | null;
  first_seen_at: string;
  group_id: string;
  group_name: string;
  author: string | null;
  contact: Contact;
  action: { kind: "whatsapp" | "facebook" | "none"; url: string | null; number?: string };
  ai: { decision: Decision; reason: string | null; rule: string | null; decided_by: string | null } | null;
  facts: Facts | null;
  partial_facts: boolean;
  status: MyStatus | null;
  notes_count: number;
  photos: Photos;
}

export interface Note {
  id: number;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface HistoryRow {
  id: number;
  status: string | null;
  note: string | null;
  source: string;
  previous_status: string | null;
  changed_at: string;
}

export interface Detail extends Card {
  full_facts: Record<string, unknown> | null;
  checks: { rule: string; status: string; detail: string }[];
  notes: Note[];
  history: HistoryRow[];
}

export interface Settings {
  whatsapp_template: string;
  recent_days: number | null;
}

export interface Meta {
  user_id: string;
  statuses: StatusDef[];
  groups: { id: string; name: string }[];
  decisions: Decision[];
  settings: Settings;
  recent_days: number;
}

export interface Filters {
  decision: Decision[];
  status: string[];
  group: string[];
  price_min?: number;
  price_max?: number;
  area?: string;
  since?: string;
  include_old: boolean;
  has_phone?: boolean;
  source?: string;
  sort: "newest" | "price_asc" | "price_desc";
}

export class NotPaired extends Error {}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  if (init.method && init.method !== "GET") {
    headers["X-ApartmentBot"] = "1";
    if (init.body) headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (resp.status === 401) throw new NotPaired();
  if (!resp.ok) {
    let msg = `${resp.status}`;
    try {
      msg = (await resp.json()).detail ?? msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export function filtersToQuery(f: Filters, offset: number, limit: number): string {
  const q = new URLSearchParams();
  f.decision.forEach((d) => q.append("decision", d));
  f.status.forEach((s) => q.append("status", s));
  f.group.forEach((g) => q.append("group", g));
  if (f.price_min != null) q.set("price_min", String(f.price_min));
  if (f.price_max != null) q.set("price_max", String(f.price_max));
  if (f.area) q.set("area", f.area);
  if (f.since) q.set("since", f.since);
  if (f.include_old) q.set("include_old", "true");
  if (f.has_phone != null) q.set("has_phone", String(f.has_phone));
  if (f.source) q.set("source", f.source);
  q.set("sort", f.sort);
  q.set("offset", String(offset));
  q.set("limit", String(limit));
  return q.toString();
}

export const api = {
  pair: (code: string) => call<{ ok: boolean }>("/api/pair", send("POST", { code })),
  logout: () => call<{ ok: boolean }>("/api/logout", send("POST")),
  meta: () => call<Meta>("/api/meta"),
  posts: (f: Filters, offset = 0, limit = 30) =>
    call<{ total: number; items: Card[] }>(`/api/posts?${filtersToQuery(f, offset, limit)}`),
  post: (id: string) => call<Detail>(`/api/posts/${id}`),
  setStatus: (id: string, status: string | null, note?: string | null) =>
    call<{ previous: MyStatus | null; status: MyStatus | null }>(
      `/api/posts/${id}/status`,
      send("PUT", { status, note: note ?? null })
    ),
  undoStatus: (id: string) => call<{ status: MyStatus | null }>(`/api/posts/${id}/status/undo`, send("POST")),
  contacted: (id: string) =>
    call<{ previous: MyStatus | null; status: MyStatus | null }>(`/api/posts/${id}/contacted`, send("POST")),
  addNote: (id: string, body: string) => call<Note>(`/api/posts/${id}/notes`, send("POST", { body })),
  editNote: (noteId: number, body: string) => call(`/api/notes/${noteId}`, send("PUT", { body })),
  deleteNote: (noteId: number) => call(`/api/notes/${noteId}`, send("DELETE")),
  saveSettings: (values: Partial<Settings>) => call<Settings>("/api/settings", send("PUT", values)),
  addStatus: (label: string, color: string, needs_note: boolean) =>
    call<{ key: string; statuses: StatusDef[] }>("/api/statuses", send("POST", { label, color, needs_note })),
  editStatus: (key: string, label: string, color: string, needs_note: boolean) =>
    call<StatusDef[]>(`/api/statuses/${key}`, send("PUT", { label, color, needs_note })),
  reorderStatuses: (keys: string[]) => call<StatusDef[]>("/api/statuses/order", send("PUT", { keys })),
  deleteStatus: (key: string) => call<StatusDef[]>(`/api/statuses/${key}`, send("DELETE")),
};
