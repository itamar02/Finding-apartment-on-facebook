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
  action: {
    kind: "whatsapp" | "facebook" | "none";
    url: string | null;
    number?: string;
    guessed?: boolean;
    typed?: string;
    guess?: string;
  };
  ai: { decision: Decision; reason: string | null; rule: string | null; decided_by: string | null } | null;
  facts: Facts | null;
  partial_facts: boolean;
  status: MyStatus | null;
  notes_count: number;
  photos: Photos;
  edited: boolean;
  price_history: PricePoint[];
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
  versions: { id: number; kind: "edit" | "expanded"; seen_at: string; text: string }[];
}

export interface QuietHours {
  start: string;
  end: string;
}

export interface NotificationSettings {
  enabled: boolean;
  channel: "push" | "telegram" | "both";
  decisions: Decision[];
  price_ceiling: number | null;
  quiet_hours: QuietHours | null;
  edits: boolean;
  followups: boolean;
  price_drop: boolean;
  price_drop_min: number;
}

export interface SchedulerSettings {
  enabled: boolean;
  interval_minutes: number;
  quiet_hours: QuietHours | null;
  analyze: boolean;
  analyze_limit: number;
}

export interface Settings {
  whatsapp_template: string;
  recent_days: number | null;
  group_search_queries: string[] | null;
  followup_hours: Record<string, number>;
  notifications: NotificationSettings;
  scheduler: SchedulerSettings;
  app_url: string | null;
  whatsapp_my_name: string | null;
  learning: { mode: string; last_run: string | null };
}

export interface ConfigProposal {
  id: number;
  path: string;
  op: "add" | "remove" | "set";
  value: unknown;
  reason: string;
  evidence: string[];
  created_at: string;
}

export interface ProviderConf {
  id: string;
  label?: string;
  base_url: string;
  model: string;
  json_mode?: "json_schema" | "json_object";
  per_minute?: number | null;
  per_day?: number | null;
  price_in?: number | null;
  price_out?: number | null;
}

export interface AiState {
  ai_providers: ProviderConf[];
  ai_stages: Record<string, string>;
  anthropic_limits: { per_minute?: number | null; per_day?: number | null };
  status: { id: string; has_key: boolean; remaining_today: number | null }[];
  presets: ProviderConf[];
  stages: string[];
  anthropic_model: string;
}

export interface SchedulerState {
  running: boolean;
  step: string | null;
  last_start: string | null;
  last_end: string | null;
  last_result: string | null;
  next_run: string | null;
  job: "download" | "analyze" | "all" | null;
  stopping: boolean;
  unchecked: number;
  settings: SchedulerSettings;
}

export interface PricePoint {
  post_id: string;
  rent_ils: number | null;
  posted_at: string | null;
}

export interface FollowUp {
  post_id: string;
  status: string;
  note: string | null;
  since: string;
  overdue_hours: number;
  card: Card | null;
}

export interface Group {
  id: string;
  name: string | null;
  url: string;
  member_count: number | null;
  privacy: "public" | "private" | null;
  is_member: number | null;
  description: string | null;
  relevance_score: number | null;
  relevance_reason: string | null;
  selected: number;
  source: "config" | "my_groups" | "search" | "manual";
  search_query: string | null;
}

export interface GptChange {
  post_id: string;
  status: string;
  label: string;
  note: string | null;
  current: { status: string; label: string; source: string; note: string | null } | null;
}

export interface GptPreview {
  changes: GptChange[];
  conflicts: GptChange[];
  unchanged: number;
  errors: string[];
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
  show_closed?: boolean;   // also show listings I marked not relevant / taken
  has_phone?: boolean;
  source?: string;
  sort: "newest" | "price_asc" | "price_desc";
}

export interface Visit {
  rating: number | null;
  notes: string | null;
  visited_at: string | null;
  checklist: { item_id: number; label: string; state: "ok" | "issue" | "na" | null; note: string | null }[];
  photos: { id: number; src: string; created_at: string }[];
}

export interface VisitRow {
  post_id: string;
  status: string | null;
  rating: number | null;
  notes: string | null;
  rent_ils: number | null;
  total_monthly_ils_known: number | null;
  area: string | null;
  rooms: number | null;
  issues: { label: string; note: string | null }[];
  ok: string[];
  photos: number;
}

export interface Proposal {
  id: number;
  chat_id: number;
  post_id: string | null;
  found: number;
  date: string | null;
  time: string | null;
  address: string | null;
  evidence: string[];
  explanation?: string | null;
  status?: "proposed" | "confirmed" | "discarded";
  event_link?: string | null;
  chat_title?: string | null;
  created_at?: string;
}

export interface ChatResult {
  chat_id: number;
  post_id: string | null;
  phones: string[];
  candidates: { post_id: string; posted_at: string | null; author: string | null; snippet: string }[];
  proposal: Proposal;
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
  if (f.status.length === 0) q.append("with_status", "true"); // listings with a status of mine stay, whatever the AI said
  if (!f.show_closed && f.status.length === 0) {
    q.append("exclude_status", "not_relevant");
    q.append("exclude_status", "taken");
  }
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
  groups: () => call<{ groups: Group[]; queries: string[] }>("/api/groups"),
  selectGroup: (id: string, selected: boolean) => call(`/api/groups/${encodeURIComponent(id)}`, send("PUT", { selected })),
  addGroup: (url: string) => call<{ id: string }>("/api/groups", send("POST", { url })),
  startDiscovery: (score: boolean) => call("/api/groups/discover", send("POST", { score })),
  discovery: () => call<{ running: boolean; exit_code: number | null; log: string[] }>("/api/groups/discover"),
  scheduler: () => call<SchedulerState>("/api/scheduler"),
  runScheduler: (job: "download" | "analyze" | "all" = "all") => call("/api/scheduler/run", send("POST", { job })),
  stopScheduler: () => call<{ stopping: boolean }>("/api/scheduler/stop", send("POST")),
  notifications: () =>
    call<{
      items: { id: number; kind: string; post_id: string | null; title: string; body: string; created_at: string; sent_at: string | null; error: string | null }[];
      telegram: { configured: boolean; from_watchdog: boolean };
      push_devices: number;
    }>("/api/notifications"),
  pushKey: () => call<{ public_key: string }>("/api/push/key"),
  pushSubscribe: (sub: PushSubscriptionJSON) => call("/api/push/subscribe", send("POST", sub)),
  pushUnsubscribe: (sub: PushSubscriptionJSON) => call("/api/push/unsubscribe", send("POST", sub)),
  saveTelegram: (token: string, chat_id: string) => call("/api/telegram", send("PUT", { token, chat_id })),
  testNotification: () => call<{ sent: number; error: string | null; note?: string }>("/api/notifications/test", send("POST")),
  followups: () => call<FollowUp[]>("/api/followups"),
  snooze: (id: string, hours: number) => call(`/api/followups/${id}/snooze`, send("POST", { hours })),
  dismissFollowup: (id: string) => call(`/api/followups/${id}/dismiss`, send("POST")),
  ai: () => call<AiState>("/api/ai"),
  saveAi: (v: Pick<AiState, "ai_providers" | "ai_stages" | "anthropic_limits">) => call<AiState>("/api/ai", send("PUT", v)),
  saveAiKey: (id: string, key: string) => call(`/api/ai/keys/${encodeURIComponent(id)}`, send("PUT", { key })),
  visit: (id: string) => call<Visit>(`/api/posts/${id}/visit`),
  saveVisit: (id: string, v: { rating: number | null; notes: string | null; checks: { item_id: number; state: string | null; note: string | null }[] }) =>
    call<Visit>(`/api/posts/${id}/visit`, send("PUT", v)),
  uploadVisitPhoto: async (id: string, file: File) => {
    const resp = await fetch(`/api/posts/${id}/visit/photos`, {
      method: "POST",
      body: file,
      headers: { "X-ApartmentBot": "1", "Content-Type": file.type || "image/jpeg" },
      credentials: "same-origin",
    });
    if (resp.status === 401) throw new NotPaired();
    if (!resp.ok) throw new Error((await resp.json().catch(() => ({}))).detail ?? String(resp.status));
    return resp.json();
  },
  deleteVisitPhoto: (photoId: number) => call(`/api/visit-photos/${photoId}`, send("DELETE")),
  visits: () => call<VisitRow[]>("/api/visits"),
  visitItems: () => call<{ id: number; label: string }[]>("/api/visit-items"),
  saveVisitItems: (labels: string[]) => call("/api/visit-items", send("PUT", { labels })),
  addChat: (text: string, source: string, title?: string) => call<ChatResult>("/api/chats", send("POST", { text, source, title })),
  linkChat: (chatId: number, postId: string) => call(`/api/chats/${chatId}/link`, send("POST", { post_id: postId })),
  propose: (chatId: number) => call<Proposal>(`/api/chats/${chatId}/propose`, send("POST")),
  meetings: () => call<{ items: Proposal[]; calendar: { client_file: boolean; signed_in: boolean } }>("/api/meetings"),
  confirmMeeting: (id: number, v: { date: string; time: string; address: string | null; minutes: number; notes: string | null }) =>
    call<{ event_link: string | null; post_id: string }>(`/api/meetings/${id}/confirm`, send("POST", v)),
  discardMeeting: (id: number) => call(`/api/meetings/${id}/discard`, send("POST")),
  profile: () => call<{ values: Record<string, unknown>; kinds: Record<string, unknown> }>("/api/profile"),
  saveProfile: (values: Record<string, unknown>) => call<{ values: Record<string, unknown> }>("/api/profile", send("PUT", { values })),
  learning: () => call<{ proposals: ConfigProposal[]; settings: { mode: string; last_run: string | null } }>("/api/learning"),
  runLearning: () => call<{ added: number; proposals: ConfigProposal[] }>("/api/learning/run", send("POST")),
  decideLearning: (id: number, accept: boolean) =>
    call<{ proposals: ConfigProposal[] }>(`/api/learning/${id}/${accept ? "accept" : "decline"}`, send("POST")),
  gptExport: (includeOld: boolean) =>
    call<{ text: string; count: number; posts: unknown[]; prompt: string }>(`/api/gpt/export?include_old=${includeOld}`),
  gptPreview: (text: string) => call<GptPreview>("/api/gpt/preview", send("POST", { text })),
  gptApply: (text: string, override_human: boolean) =>
    call<{ applied: number; skipped: number; errors: string[] }>("/api/gpt/apply", send("POST", { text, override_human })),
};
