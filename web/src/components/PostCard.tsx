import { useState } from "react";
import type { Card } from "../api";
import { useApp } from "../App";
import { age, DECISION_LABEL, factChips, isApproved, verdictOf, VERDICT_ONLY } from "../format";
import StatusPicker, { StatusChip, useStatusActions } from "./StatusPicker";
import Gallery from "./Gallery";
import KeyLines from "./KeyLines";
import MeetingLine from "./MeetingLine";
import { money } from "../format";
import type { PricePoint } from "../api";

/** Earlier prices of the same apartment (reposts), oldest first; a drop is highlighted. */
export function PriceHistory({ points }: { points: PricePoint[] }) {
  // Only the changes: the same price posted again many times says nothing (the reposts are listed on the card).
  const prices = (points ?? []).map((p) => p.rent_ils).filter((r): r is number => r != null);
  const changes = prices.filter((r, i) => i === 0 || r !== prices[i - 1]);
  if (changes.length < 2) return null;
  const last = changes[changes.length - 1];
  const before = changes[changes.length - 2];
  return (
    <div className={"price-hist" + (last < before ? " drop" : "")}>
      {last < before ? "↓ ירידת מחיר: " : "המחיר השתנה: "}
      {changes.map((r) => money(r)).join(" ← ")}
    </div>
  );
}

/** 972533724550 -> 053-3724550: the number as it is written in the post (the link needs the international form). */
function localNumber(n?: string): string {
  if (!n) return "";
  const local = n.startsWith("972") ? "0" + n.slice(3) : n;
  return local.length === 10 ? `${local.slice(0, 3)}-${local.slice(3)}` : local.length === 9 ? `${local.slice(0, 2)}-${local.slice(2)}` : local;
}

export function ContactButton({ card }: { card: Card }) {
  const { contact } = useStatusActions();
  const a = card.action;
  if (a.kind === "none" || !a.url) return null;
  return (
    <button
      className={"btn " + (a.kind === "whatsapp" ? "whatsapp" : "")}
      title={a.guessed ? `המספר בפוסט (${a.typed}) לא תקין. מנחשים: ${a.guess}` : undefined}
      onClick={() => {
        if (a.guessed && !confirm(`המספר בפוסט נכתב ${a.typed}, וזה לא מספר תקין.
הניחוש: ${a.guess}.
לפתוח וואטסאפ למספר הזה?`)) return;
        contact(card.post_id, a.url!, card.status);
      }}
    >
      {a.kind === "whatsapp" ? `WhatsApp ${localNumber(a.number)}${a.guessed ? " (ניחוש)" : ""}` : "פרופיל פייסבוק"}
    </button>
  );
}

export default function PostCard({ card }: { card: Card }) {
  const app = useApp();
  const { change } = useStatusActions();
  const [expanded, setExpanded] = useState(false);
  const [picking, setPicking] = useState(false);
  const [askMeeting, setAskMeeting] = useState(false); // right after choosing "meeting scheduled": ask when
  const decision = card.ai?.decision ?? "PENDING";
  const mine = card.status?.status;
  const liked = isApproved(mine);
  const disliked = mine === "not_relevant";
  const backup = mine === "backup";
  const def = app.statusOf(mine);
  // Pill 1: approved / rejected and by whom (the AI bot's verdict only while nobody decided).
  // Pill 2: the process status (contacted, meeting...); plain approved / rejected live in pill 1.
  const verdict = verdictOf(card.ai, card.status);
  const stepStatus = card.status && !VERDICT_ONLY.has(card.status.status) ? card.status : null;
  const chips = factChips(card);
  const long = card.text.length > 280;

  return (
    <article className="card" style={def ? { borderInlineStartColor: def.color, borderInlineStartWidth: 4 } : undefined}>
      <div className="card-head">
        <button className="badge" style={{ background: verdict.color }} title="הקש כדי לקבוע את ההחלטה שלך" onClick={() => setPicking(true)}>
          {verdict.text} ▾
        </button>
        <StatusChip status={stepStatus} onClick={() => setPicking(true)} />
        {card.edited && <span className="tag">נערכה</span>}
        <span className="muted small grow-end">
          {card.group_name} · {age(card.posted_at ?? card.first_seen_at)}
        </span>
      </div>

      {(card.copies?.length ?? 0) > 0 && (
        <div className="small muted" dir="auto">
          פורסם גם ב-{card.copies!.length} קבוצות נוספות:{" "}
          {card.copies!.slice(0, 4).map((c, i) => (
            <span key={c.post_id}>
              {i > 0 && " · "}
              <a href={c.url} target="_blank" rel="noreferrer">
                {c.group_name}
              </a>
            </span>
          ))}
          {card.copies!.length > 4 && ` · ועוד ${card.copies!.length - 4}`}
        </div>
      )}

      {card.status?.note && card.status.status !== "meeting_scheduled" && (
        <div className="status-note small" dir="auto">
          {card.status.status === "needs_details" ? "צריך לברר: " : "📝 "}
          {card.status.note}
        </div>
      )}

      <MeetingLine card={card} startEditing={askMeeting} onDone={() => setAskMeeting(false)} />
      <KeyLines card={card} />

      {chips.length > 0 && (
        <div className="facts">
          {chips.map((c) => (
            <span key={c} className="fact" dir="auto">
              {c}
            </span>
          ))}
        </div>
      )}
      <PriceHistory points={card.price_history} />
      {card.ai?.reason && !verdict.decided && (
        <p className="reason clamp3" dir="auto">
          {card.ai.reason}
        </p>
      )}
      {card.status?.note && <p className="status-note">📝 {card.status.note}</p>}

      <p className={"post-text" + (expanded || !long ? "" : " clamped")} dir="auto">
        {card.text}
      </p>
      {long && (
        <button className="link" onClick={() => setExpanded(!expanded)}>
          {expanded ? "פחות" : "עוד"}
        </button>
      )}

      {card.photos.items.length > 0 && <Gallery photos={card.photos} />}

      <div className="card-foot">
        <span className="muted small">{card.author ?? "אנונימי"}</span>
        <div className="row">
          <button
            className={"btn vote" + (liked ? " on-good" : "")}
            aria-label="מתאים לי"
            title="מתאים לי (הקשה נוספת מבטלת)"
            // On a listing already further along (contacted, meeting...), 👍 must not wipe that step: open the picker.
            onClick={() => (liked && mine !== "interested" ? setPicking(true) : change(card.post_id, liked ? null : "interested"))}
          >
            👍
          </button>
          <button
            className={"btn vote" + (backup ? " on-mid" : "")}
            aria-label="גיבוי: לא מתלהב, אבל לא לזרוק"
            title="גיבוי: לא דירת החלומות, אבל לא זורק (הקשה נוספת מבטלת)"
            onClick={() => change(card.post_id, backup ? null : "backup")}
          >
            🤷
          </button>
          <button
            className={"btn vote" + (disliked ? " on-bad" : "")}
            aria-label="לא רלוונטי, להסיר מהרשימה"
            title="לא רלוונטי: מסיר מהרשימה (אפשר לבטל)"
            onClick={() => change(card.post_id, disliked ? null : "not_relevant")}
          >
            🗑️
          </button>
          <ContactButton card={card} />
          <a className="btn" href={card.url} target="_blank" rel="noopener noreferrer">
            לפוסט
          </a>
          <button className="btn" onClick={() => app.openPost(card.post_id)}>
            פרטים{card.notes_count ? ` (${card.notes_count})` : ""}
          </button>
        </div>
      </div>

      {picking && (
        <StatusPicker
          aiLabel={verdict.decided ? undefined : DECISION_LABEL[decision]}
          current={card.status}
          onClose={() => setPicking(false)}
          onPick={(s, note) => {
            setPicking(false);
            change(card.post_id, s, note);
            if (s === "meeting_scheduled" && !card.meeting) setAskMeeting(true);
          }}
        />
      )}
    </article>
  );
}
