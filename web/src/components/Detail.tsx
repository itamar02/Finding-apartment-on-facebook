import { useEffect, useState } from "react";
import { api, Detail as DetailT } from "../api";
import { useApp } from "../App";
import { age, dateTime, DECISION_LABEL, money, SOURCE_LABEL, TYPE_LABEL, verdictOf, VERDICT_ONLY } from "../format";
import Gallery from "./Gallery";
import { ContactButton, PriceHistory } from "./PostCard";
import StatusPicker, { StatusChip, useStatusActions } from "./StatusPicker";
import VisitPanel from "./VisitPanel";

const VISIT_STATUSES = ["meeting_scheduled", "visited", "top_candidate"];

const FACT_LABELS: [string, string][] = [
  ["listing_type", "סוג"],
  ["rent_ils", "שכירות"],
  ["total_monthly_ils_known", "סה״כ חודשי ידוע"],
  ["city", "עיר"],
  ["neighborhood", "שכונה"],
  ["street", "רחוב"],
  ["rooms", "חדרים"],
  ["size_sqm", "מ״ר"],
  ["floor", "קומה"],
  ["elevator", "מעלית"],
  ["balcony", "מרפסת"],
  ["parking", "חניה"],
  ["shelter", "ממ״ד / מקלט"],
  ["furnished", "ריהוט"],
  ["broker", "תיווך"],
  ["move_in_date", "כניסה"],
  ["lease_duration", "תקופת חוזה"],
  ["deposit_and_guarantees", "ערבויות"],
  ["pets", "חיות"],
];

const VALUE_LABEL: Record<string, string> = {
  yes: "כן",
  no: "לא",
  no_broker: "בלי תיווך",
  broker: "יש תיווך",
  furnished: "מרוהטת",
  partially_furnished: "מרוהטת חלקית",
  unfurnished: "לא מרוהטת",
  mamad: "ממ״ד",
  mamak: "ממ״ק",
  building_shelter: "מקלט בבניין",
  none: "אין",
  allowed: "מותר",
  not_allowed: "אסור",
  negotiable: "לדבר על זה",
};

function show(key: string, v: unknown): string | null {
  if (v == null || v === "" || v === "unknown") return null;
  if (key === "listing_type") return TYPE_LABEL[String(v)] ?? String(v);
  if (typeof v === "string" && VALUE_LABEL[v]) return VALUE_LABEL[v];
  if (key === "rent_ils" || key === "total_monthly_ils_known") return money(Number(v));
  return String(v);
}

export default function Detail({ postId, onClose }: { postId: string; onClose: () => void }) {
  const app = useApp();
  const { change } = useStatusActions();
  const [d, setD] = useState<DetailT | null>(null);
  const [picking, setPicking] = useState(false);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState<{ id: number; body: string } | null>(null);

  const reload = () => api.post(postId).then(setD).catch(app.fail);

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postId]);

  useEffect(
    () => app.onStatusChange((id, status) => id === postId && setD((p) => (p ? { ...p, status } : p))),
    [app, postId]
  );

  async function addNote() {
    if (!draft.trim()) return;
    try {
      await api.addNote(postId, draft);
      setDraft("");
      reload();
    } catch (e) {
      app.fail(e);
    }
  }

  async function saveEdit() {
    if (!editing) return;
    try {
      await api.editNote(editing.id, editing.body);
      setEditing(null);
      reload();
    } catch (e) {
      app.fail(e);
    }
  }

  async function removeNote(id: number) {
    if (!confirm("למחוק את ההערה?")) return;
    try {
      await api.deleteNote(id);
      reload();
    } catch (e) {
      app.fail(e);
    }
  }

  const decision = d?.ai?.decision ?? "PENDING";
  const verdict = verdictOf(d?.ai ?? null, d?.status ?? null); // pill 1: approved / rejected and by whom
  const stepStatus = d?.status && !VERDICT_ONLY.has(d.status.status) ? d.status : null; // pill 2: the process step
  const facts = d?.full_facts ?? null;

  return (
    <div className="fullscreen">
      <header className="topbar">
        <button className="btn" onClick={onClose}>
          → חזרה
        </button>
        {d && <span className="muted small">{d.group_name}</span>}
      </header>
      {!d ? (
        <p className="center muted">טוען…</p>
      ) : (
        <div className="detail">
          <div className="card-head">
            <button className="badge" style={{ background: verdict.color }} title="הקש כדי לקבוע את ההחלטה שלך" onClick={() => setPicking(true)}>
              {verdict.text} ▾
            </button>
            <StatusChip status={stepStatus} onClick={() => setPicking(true)} />
            <span className="muted small grow-end">{age(d.posted_at ?? d.first_seen_at)}</span>
          </div>
          <PriceHistory points={d.price_history} />
          {d.ai?.reason && !verdict.decided && (
            <p className="reason" dir="auto">
              {d.ai.reason}
            </p>
          )}
          {d.status?.note && <p className="status-note">📝 {d.status.note}</p>}

          <div className="row">
            <ContactButton card={d} />
            <a className="btn" href={d.url} target="_blank" rel="noopener noreferrer">
              לפוסט בפייסבוק
            </a>
          </div>
          <p className="muted small">
            {d.contact.name ?? "אנונימי"}
            {d.contact.phones.length > 0 && <span dir="ltr"> · {d.contact.phones.join(", ")}</span>}
          </p>

          {d.photos.items.length > 0 && <Gallery photos={d.photos} />}

          <p className="post-text" dir="auto">
            {d.text}
          </p>
          {d.text_truncated && <p className="muted small">הטקסט נקטע בסריקה; הנוסח המלא בפייסבוק.</p>}
          {d.versions.some((v) => v.kind === "edit") && (
            <details className="versions">
              <summary className="small">המודעה נערכה ({d.versions.filter((v) => v.kind === "edit").length}). נוסחים קודמים</summary>
              {[...d.versions].reverse().slice(1).map((v) => (
                <div key={v.id} className="note">
                  <div className="small muted">{dateTime(v.seen_at)}</div>
                  <p dir="auto">{v.text}</p>
                </div>
              ))}
            </details>
          )}

          {facts && (
            <>
              <h3>פרטים</h3>
              {d.partial_facts && <p className="muted small">נבדק רק בבדיקה מהירה, חלק מהפרטים חסרים.</p>}
              <dl className="facts-table">
                {FACT_LABELS.map(([k, label]) => {
                  const v = show(k, facts[k]);
                  return v ? (
                    <div key={k}>
                      <dt>{label}</dt>
                      <dd dir="auto">{v}</dd>
                    </div>
                  ) : null;
                })}
              </dl>
            </>
          )}

          {VISIT_STATUSES.includes(d.status?.status ?? "") && <VisitPanel postId={d.post_id} />}

          <h3>הערות</h3>
          {d.notes.map((n) =>
            editing?.id === n.id ? (
              <div key={n.id} className="note">
                <textarea rows={3} value={editing.body} onChange={(e) => setEditing({ id: n.id, body: e.target.value })} />
                <div className="row">
                  <button className="btn primary" onClick={saveEdit}>
                    שמור
                  </button>
                  <button className="btn" onClick={() => setEditing(null)}>
                    ביטול
                  </button>
                </div>
              </div>
            ) : (
              <div key={n.id} className="note">
                <p dir="auto">{n.body}</p>
                <div className="row small muted">
                  <span>{dateTime(n.updated_at)}</span>
                  <button className="link" onClick={() => setEditing({ id: n.id, body: n.body })}>
                    עריכה
                  </button>
                  <button className="link" onClick={() => removeNote(n.id)}>
                    מחיקה
                  </button>
                </div>
              </div>
            )
          )}
          <textarea rows={2} placeholder="הערה חדשה…" value={draft} onChange={(e) => setDraft(e.target.value)} />
          <button className="btn" disabled={!draft.trim()} onClick={addNote}>
            הוסף הערה
          </button>

          {d.history.length > 0 && (
            <>
              <h3>היסטוריית סטטוס</h3>
              <ul className="history">
                {[...d.history].reverse().map((h) => (
                  <li key={h.id}>
                    <span>{h.status ? app.statusOf(h.status)?.label ?? h.status : "ללא סטטוס"}</span>
                    <span className="muted small">
                      {" "}
                      · {SOURCE_LABEL[h.source] ?? h.source} · {dateTime(h.changed_at)}
                    </span>
                    {h.note && <div className="small">{h.note}</div>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {picking && d && (
        <StatusPicker
          aiLabel={verdict.decided ? undefined : DECISION_LABEL[decision]}
          current={d.status}
          onClose={() => setPicking(false)}
          onPick={(s, note) => {
            setPicking(false);
            change(d.post_id, s, note);
          }}
        />
      )}
    </div>
  );
}
