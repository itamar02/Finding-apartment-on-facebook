// Hebrew labels and small formatting helpers.
import type { Card, Decision, Facts } from "./api";

export const DECISION_LABEL: Record<Decision, string> = {
  PASS: "מתאים",
  NEEDS_REVIEW: "לבדיקה",
  REJECT: "נפסל",
  PENDING: "טרם נבדק",
};

export const DECISION_COLOR: Record<Decision, string> = {
  PASS: "var(--pass)",
  NEEDS_REVIEW: "var(--review)",
  REJECT: "var(--reject)",
  PENDING: "var(--muted)",
};

export const TYPE_LABEL: Record<string, string> = {
  whole_apartment: "דירה שלמה",
  roommate_search: "שותפים",
  room_replacement: "החלפת שותף",
  sublet: "סאבלט",
  other: "אחר",
  unknown: "לא ידוע",
};

export const SOURCE_LABEL: Record<string, string> = { human: "אני", gpt: "צ׳אט AI", system: "מערכת" };

/** Statuses that mean "I (or an AI chat) approved it" - the verdict pill shows that, not the status chip. */
const APPROVED = new Set(["interested", "needs_details", "contacted", "awaiting_reply", "meeting_scheduled", "visited", "top_candidate"]);
export const isApproved = (status: string | undefined) => !!status && APPROVED.has(status);

/** The statuses that only say approved / rejected; every other status is a step of the process (the second pill). */
export const VERDICT_ONLY = new Set(["interested", "not_relevant"]);

export interface Verdict {
  text: string;
  color: string;
  decided: boolean; // by a person or an AI chat; false = the AI bot's own verdict
}

/** Pill 1: was it approved or rejected, and by whom. Falls back to the AI bot's verdict only while nobody decided. */
export function verdictOf(ai: { decision: Decision } | null, status: { status: string; source: string } | null): Verdict {
  if (status) {
    const by = SOURCE_LABEL[status.source] ?? status.source;
    if (status.status === "not_relevant") return { text: `✗ לא רלוונטי · ${by}`, color: "var(--reject)", decided: true };
    if (status.status === "taken") return { text: `נתפסה · ${by}`, color: "var(--muted)", decided: true };
    if (APPROVED.has(status.status)) return { text: `✓ אושרה · ${by}`, color: "var(--pass)", decided: true };
  }
  const d: Decision = ai?.decision ?? "PENDING";
  return { text: `AI: ${DECISION_LABEL[d]}`, color: DECISION_COLOR[d], decided: false };
}

export function money(n: number | null | undefined): string | null {
  return n == null ? null : `${n.toLocaleString("he-IL")} ₪`;
}

export function age(iso: string | null): string {
  if (!iso) return "";
  const mins = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 60) return `לפני ${Math.round(mins)} דק׳`;
  const hours = mins / 60;
  if (hours < 24) return `לפני ${Math.round(hours)} שע׳`;
  const days = Math.round(hours / 24);
  return days === 1 ? "אתמול" : `לפני ${days} ימים`;
}

export function dateTime(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("he-IL", { dateStyle: "short", timeStyle: "short" });
}

export function area(f: Facts | null): string | null {
  if (!f) return null;
  return [f.street, f.neighborhood, f.city].filter(Boolean).join(", ") || f.location_summary_en || null;
}

/** Short fact chips for a card: rent, area, rooms, type, move-in. */
export function factChips(c: Card): string[] {
  const f = c.facts;
  if (!f) return [];
  const chips: string[] = [];
  if (f.rooms) chips.push(`${f.rooms} חד׳`);
  if (f.listing_type && f.listing_type !== "unknown") chips.push(TYPE_LABEL[f.listing_type] ?? f.listing_type);
  if (f.move_in_date) chips.push(`כניסה: ${f.move_in_date}`);
  return chips;
}
