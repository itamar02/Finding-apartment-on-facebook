import { useState } from "react";
import { api, Card } from "../api";
import { useApp } from "../App";
import { money } from "../format";

/** The two lines at the top of every listing: location (as precise as the post allows; editable) and price. */
export default function KeyLines({ card, onChanged }: { card: Card; onChanged?: (loc: Card["location"]) => void }) {
  const app = useApp();
  const [loc, setLoc] = useState(card.location);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const [price, setPrice] = useState(card.price);
  const [bills, setBills] = useState(card.bills);
  const [billsDraft, setBillsDraft] = useState<"included" | "excluded" | null>(null);
  const [editingPrice, setEditingPrice] = useState(false);
  const [priceDraft, setPriceDraft] = useState("");
  const rent = money(price.rent);
  const total = !price.mine && price.total && price.total !== price.rent ? money(price.total) : null;

  const BILL_TEXT = { included: "כולל חשבונות", excluded: "לא כולל חשבונות", unknown: "חשבונות לא צוינו" } as const;
  const KIND: Record<string, string> = { arnona: "ארנונה", vaad_bayit: "ועד בית", utilities: "מים/חשמל", bills_bundle: "חשבונות", internet: "אינטרנט" };

  async function savePrice(value: number | null, billsValue: "included" | "excluded" | null) {
    try {
      const r = await api.setPrice(card.post_id, value, billsValue);
      setPrice(r.price);
      setBills(r.bills);
      setEditingPrice(false);
      app.toast({ text: value ? "המחיר עודכן" : "חזר למחיר מהמודעה" });
    } catch (e) {
      app.fail(e);
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
            <input autoFocus inputMode="numeric" dir="ltr" value={priceDraft} placeholder="שכירות חודשית בשקלים" onChange={(e) => setPriceDraft(e.target.value.replace(/\D/g, ""))} />
            <span className="row">
              <button className="btn primary" disabled={!priceDraft && !billsDraft} onClick={() => {
                  // An unchanged rent is not an override; only a different number is stored.
                  const typed = priceDraft ? Number(priceDraft) : null;
                  savePrice(typed !== null && typed === price.rent && !price.mine ? null : typed, billsDraft);
                }}>
                שמור
              </button>
              <button className="btn" onClick={() => setEditingPrice(false)}>
                ביטול
              </button>
              {(price.mine || bills.mine) && (
                <button className="btn" onClick={() => savePrice(null, null)}>
                  חזור למודעה
                </button>
              )}
            </span>
            <span className="pills bills-pick">
              {(["included", "excluded"] as const).map((b) => (
                <button key={b} className={"pill" + (billsDraft === b ? " on" : "")} onClick={() => setBillsDraft(billsDraft === b ? null : b)}>
                  {BILL_TEXT[b]}
                </button>
              ))}
            </span>
          </span>
        ) : (
          <>
            <span className="grow">
              {rent ? (total ? `${rent} (סה״כ ${total} עם הוצאות)` : rent) : "לא צוין"}
              {price.mine && <span className="tag mine">עודכן ידנית</span>}
              {price.mine && price.ai_rent && <span className="small muted"> (במודעה: {money(price.ai_rent)})</span>}
              {rent && (
                <span className={"bills " + bills.state}>
                  {" · "}
                  {BILL_TEXT[bills.state]}
                  {bills.mine && " (עודכן ידנית)"}
                </span>
              )}
              {rent && bills.state === "excluded" && bills.extras.length > 0 && (
                <span className="small muted"> ({bills.extras.map((e) => `${KIND[e.kind] ?? e.kind} ${e.amount.toLocaleString("he-IL")} ₪`).join(", ")})</span>
              )}
            </span>
            <button
              className="icon small-icon"
              aria-label="עדכון מחיר"
              title="המחיר השתנה"
              onClick={() => {
                setPriceDraft(price.rent ? String(price.rent) : "");
                setBillsDraft(bills.mine ? (bills.state === "unknown" ? null : bills.state) : null);
                setEditingPrice(true);
              }}
            >
              ✏️
            </button>
          </>
        )}
      </div>
    </div>
  );
}
