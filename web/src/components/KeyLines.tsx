import { useState } from "react";
import { api, Card, Commute } from "../api";
import { useApp } from "../App";
import { money } from "../format";

/** Puts " ₪" after every number that doesn't have a shekel sign yet (a price note is mostly amounts). */
export function addShekel(text: string): string {
  return text.replace(/(\d+(?:,\d{3})*(?:\.\d+)?)(?!\d)(?!\s*(?:₪|ש"ח|ש״ח|שח|שקל))/g, "$1 ₪");
}

/** The two lines at the top of every listing: location (as precise as the post allows; editable) and price. */
export default function KeyLines({ card, onChanged }: { card: Card; onChanged?: (loc: Card["location"]) => void }) {
  const app = useApp();
  const [loc, setLoc] = useState(card.location);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const [price, setPrice] = useState(card.price);
  const [bills, setBills] = useState(card.bills);
  const [billsDraft, setBillsDraft] = useState<"included" | "excluded" | null>(null);
  const [note, setNote] = useState(card.price_note);
  const [commutes, setCommutes] = useState<Commute[]>(card.commutes ?? []);
  const [checking, setChecking] = useState(false);
  const [editingPrice, setEditingPrice] = useState(false);
  const [textDraft, setTextDraft] = useState("");
  const rent = money(price.rent);
  const total = !price.mine && price.total && price.total !== price.rent ? money(price.total) : null;
  const defaultText = price.rent ? `${price.rent.toLocaleString("he-IL")} ₪` : "";

  /** The rent in what I typed: the first amount of at least 500 (so "ארנונה 400" isn't taken for the rent). */
  function rentIn(text: string): number | null {
    for (const m of text.match(/\d+(?:,\d{3})*(?:\.\d+)?/g) ?? []) {
      const n = Number(m.replace(/,/g, ""));
      if (n >= 500) return Math.round(n);
    }
    return null;
  }

  const BILL_TEXT = { included: "כולל חשבונות", excluded: "לא כולל חשבונות", unknown: "חשבונות לא צוינו" } as const;
  const KIND: Record<string, string> = { arnona: "ארנונה", vaad_bayit: "ועד בית", utilities: "מים/חשמל", bills_bundle: "חשבונות", internet: "אינטרנט" };

  async function savePrice(value: number | null, billsValue: "included" | "excluded" | null, noteValue: string | null) {
    try {
      const r = await api.setPrice(card.post_id, value, billsValue, noteValue);
      setPrice(r.price);
      setBills(r.bills);
      setNote(r.note);
      setEditingPrice(false);
      app.toast({ text: value || noteValue || billsValue ? "המחיר עודכן" : "חזר למחיר מהמודעה" });
    } catch (e) {
      app.fail(e);
    }
  }

  async function checkCommute() {
    setChecking(true);
    try {
      setCommutes((await api.checkCommute(card.post_id)).commutes);
    } catch (e) {
      app.fail(e);
    } finally {
      setChecking(false);
    }
  }

  async function save(value: string | null) {
    try {
      const r = await api.setLocation(card.post_id, value);
      setLoc(r.location);
      setEditing(false);
      onChanged?.(r.location);
      app.toast({ text: value ? "המיקום עודכן" : "חזר למיקום מהמודעה" });
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <div className="keylines">
      <div className="keyline">
        <span className="key">מיקום:</span>
        {editing ? (
          <span className="grow">
            <input autoFocus dir="auto" value={draft} placeholder="כתובת או שכונה מדויקת" onChange={(e) => setDraft(e.target.value)} />
            <span className="row">
              <button className="btn primary" disabled={!draft.trim()} onClick={() => save(draft.trim())}>
                שמור
              </button>
              <button className="btn" onClick={() => setEditing(false)}>
                ביטול
              </button>
              {loc.mine && (
                <button className="btn" onClick={() => save(null)}>
                  חזור למודעה
                </button>
              )}
            </span>
          </span>
        ) : (
          <>
            <span className="grow" dir="auto">
              {loc.text ?? "לא צוין"}
              {loc.mine && <span className="tag mine">עודכן ידנית</span>}
            </span>
            <button
              className="icon small-icon"
              aria-label="עדכון מיקום"
              title="יש לי מיקום מדויק יותר"
              onClick={() => {
                setDraft(loc.text ?? "");
                setEditing(true);
              }}
            >
              ✏️
            </button>
          </>
        )}
      </div>
      {!editing && loc.hints.length > 0 && (
        <div className="small muted keyhints" dir="auto">
          {loc.hints.join(" · ")}
        </div>
      )}
      <div className="keyline">
        <span className="key">מחיר:</span>
        {editingPrice ? (
          <span className="grow">
            <textarea
              autoFocus
              rows={3}
              dir="auto"
              className="price-note-input"
              placeholder="כתבו חופשי: 3200 לא כולל חשבונות, ארנונה 400 … (מספרים מקבלים ₪ אוטומטית)"
              value={textDraft}
              onChange={(e) => {
                const v = e.target.value;
                // A finished number (followed by a space, comma or line break, with the cursor at the end) gets its shekel sign.
                setTextDraft(/[\s,;]$/.test(v) && e.target.selectionStart === v.length ? addShekel(v) : v);
              }}
              onBlur={() => setTextDraft((v) => addShekel(v))}
            />
            <span className="small muted">המחיר לסינון ולמיון הוא הסכום הראשון מ-500 ומעלה.</span>
            <span className="pills bills-pick">
              {(["included", "excluded"] as const).map((b) => (
                <button key={b} className={"pill" + (billsDraft === b ? " on" : "")} onClick={() => setBillsDraft(billsDraft === b ? null : b)}>
                  {BILL_TEXT[b]}
                </button>
              ))}
            </span>
            <span className="row">
              <button
                className="btn primary"
                onClick={() => {
                  const text = addShekel(textDraft).trim();
                  const typedRent = rentIn(text);
                  // Text that just repeats the post's own price is not an edit.
                  const same = !price.mine && !note && text === defaultText;
                  savePrice(same ? null : typedRent !== null && typedRent === price.rent && !price.mine ? null : typedRent, billsDraft, same ? null : text || null);
                }}
              >
                שמור
              </button>
              <button className="btn" onClick={() => setEditingPrice(false)}>
                ביטול
              </button>
              {(price.mine || bills.mine || note) && (
                <button className="btn" onClick={() => savePrice(null, null, null)}>
                  חזור למודעה
                </button>
              )}
            </span>
          </span>
        ) : (
          <>
            <span className="grow" dir="auto">
              {note ? note : rent ? (total ? `${rent} (סה״כ ${total} עם הוצאות)` : rent) : "לא צוין"}
              {(price.mine || note) && <span className="tag mine">עודכן ידנית</span>}
              {(price.mine || note) && price.ai_rent && price.ai_rent !== price.rent && <span className="small muted"> (במודעה: {money(price.ai_rent)})</span>}
              {rent && (!note || bills.mine) && !(bills.state === "excluded" && (total || bills.extras.length > 0)) && (
                <span className={"bills " + bills.state}>
                  {" · "}
                  {BILL_TEXT[bills.state]}
                  {bills.mine && " (עודכן ידנית)"}
                </span>
              )}
              {/* "not including bills" is clear from the total and the amounts, so it is not spelled out */}
              {rent && !note && bills.state === "excluded" && bills.extras.length > 0 && (
                <span className="small muted"> · {bills.extras.map((e) => `${KIND[e.kind] ?? e.kind} ${e.amount.toLocaleString("he-IL")} ₪`).join(", ")}</span>
              )}
            </span>
            <button
              className="icon small-icon"
              aria-label="עדכון מחיר"
              title="המחיר השתנה"
              onClick={() => {
                setTextDraft(note ?? defaultText);
                setBillsDraft(bills.mine ? (bills.state === "unknown" ? null : bills.state) : null);
                setEditingPrice(true);
              }}
            >
              ✏️
            </button>
          </>
        )}
      </div>
      {commutes.length > 0 && (
        <>
          {commutes.map((c, i) => (
            <div key={c.destination}>
              <div className="keyline">
                <span className="key">{i === 0 ? "נסיעה:" : ""}</span>
                <span className="grow" dir="auto">
                  {c.minutes ? (
                    <>
                      <b className={c.within_limit === false ? "commute-bad" : c.within_limit ? "commute-ok" : ""}>~{c.minutes} דק׳</b>
                      {" ל"}
                      {c.destination}
                      {c.arrive_by && <span className="small muted"> (עד {c.arrive_by})</span>}
                      {c.scooter && <span title="כולל קורקינט שכור"> 🛴{c.scooter_minutes ? ` ${c.scooter_minutes} דק׳` : ""}</span>}
                      <span className="small muted"> · הערכה{c.confidence === "low" ? " לא בטוחה" : ""}</span>
                    </>
                  ) : (
                    <span className="muted">לא הצלחתי להעריך נסיעה ל{c.destination} (אין מיקום ברור)</span>
                  )}
                </span>
                {i === 0 && (
                  <button className="icon small-icon" aria-label="בדיקת זמן נסיעה" title="בדיקה מחדש" disabled={checking} onClick={checkCommute}>
                    {checking ? "…" : "🔄"}
                  </button>
                )}
              </div>
              {c.route && (
                <div className="small muted keyhints" dir="auto">
                  {c.station && c.walk_minutes ? `🚶 ${c.walk_minutes} דק׳ ל${c.station} · ` : ""}
                  {c.from ? `מ${c.from}: ` : ""}
                  {c.route}
                </div>
              )}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
