import { useState } from "react";
import type { Card } from "../api";
import { useApp } from "../App";
import { age, DECISION_COLOR, DECISION_LABEL, factChips } from "../format";
import StatusPicker, { StatusChip, useStatusActions } from "./StatusPicker";
import Gallery from "./Gallery";
import { money } from "../format";
import type { PricePoint } from "../api";

/** Earlier prices of the same apartment (reposts), oldest first; a drop is highlighted. */
export function PriceHistory({ points }: { points: PricePoint[] }) {
  if (!points || points.length < 2) return null;
  const last = points[points.length - 1].rent_ils ?? 0;
  const before = points[points.length - 2].rent_ils ?? 0;
  return (
    <div className={"price-hist" + (last < before ? " drop" : "")}>
      {last < before ? "↓ ירידת מחיר: " : "פורסמה שוב: "}
      {points.map((p) => money(p.rent_ils)).join(" ← ")}
    </div>
  );
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
      {a.kind === "whatsapp" ? (a.guessed ? "WhatsApp (ניחוש)" : "WhatsApp") : "פרופיל פייסבוק"}
    </button>
  );
}

export default function PostCard({ card }: { card: Card }) {
  const app = useApp();
  const { change } = useStatusActions();
  const [expanded, setExpanded] = useState(false);
  const [picking, setPicking] = useState(false);
  const decision = card.ai?.decision ?? "PENDING";
  const mine = card.status?.status;
  const liked = mine === "interested" || mine === "top_candidate";
  const disliked = mine === "not_relevant";
  const def = app.statusOf(mine);
  const chips = factChips(card);
  const long = card.text.length > 280;

  return (
    <article className="card" style={def ? { borderInlineStartColor: def.color, borderInlineStartWidth: 4 } : undefined}>
      <div className="card-head">
        <span className={"badge" + (mine ? " dim" : "")} style={{ background: DECISION_COLOR[decision] }} title="ההחלטה של ה-AI">
          {mine ? "AI: " : ""}
          {DECISION_LABEL[decision]}
        </span>
        <StatusChip status={card.status} onClick={() => setPicking(true)} />
        {card.edited && <span className="tag">נערכה</span>}
        <span className="muted small grow-end">
          {card.group_name} · {age(card.posted_at ?? card.first_seen_at)}
        </span>
      </div>

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
      {card.ai?.reason && (
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
            onClick={() => change(card.post_id, liked ? null : "interested")}
          >
            👍
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
          current={card.status}
          onClose={() => setPicking(false)}
          onPick={(s, note) => {
            setPicking(false);
            change(card.post_id, s, note);
          }}
        />
      )}
    </article>
  );
}
