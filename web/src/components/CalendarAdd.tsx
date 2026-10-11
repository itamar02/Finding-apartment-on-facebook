import { useEffect, useState } from "react";
import { api, Detail } from "../api";
import { useApp } from "../App";

/** Add a viewing I arranged to Google Calendar: one event, only when I tap the button. */
export default function CalendarAdd({ d }: { d: Detail }) {
  const app = useApp();
  const [ready, setReady] = useState<boolean | null>(null);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("18:00");
  const [minutes, setMinutes] = useState(45);
  const [address, setAddress] = useState(d.location.text ?? "");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    api.calendarStatus().then((s) => setReady(s.signed_in)).catch(() => setReady(false));
  }, []);

  async function add() {
    if (!confirm(`להוסיף ליומן גוגל?\n${date} ${time}\n${address || "ללא כתובת"}`)) return;
    setBusy(true);
    try {
      const r = await api.addToCalendar(d.post_id, { date, time, address: address || null, minutes, notes: null });
      setDone(r.event_link ?? "");
      app.statusChanged(d.post_id, { status: "meeting_scheduled", note: `${date} ${time}${address ? `, ${address}` : ""}`, source: "human", updated_at: new Date().toISOString() });
      app.toast({ text: "נוסף ליומן והסטטוס עודכן" });
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="section cal">
      <summary>📅 הוסף ליומן גוגל</summary>
      {done !== null ? (
        <p className="small">
          נוסף ליומן.{" "}
          {done && (
            <a href={done} target="_blank" rel="noopener noreferrer">
              פתח ביומן
            </a>
          )}
        </p>
      ) : (
        <div className="form">
          {ready === false && (
            <p className="small warning">
              יומן גוגל עוד לא מחובר. במחשב: שמרו את קובץ ה-OAuth בתיקייה <code>secrets</code> והריצו <code dir="ltr">python app_server.py --google-login</code>{" "}
              (ההוראות ב-README).
            </p>
          )}
          <div className="row">
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
            <label className="check small">
              <input className="short" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(Number(e.target.value) || 45)} />
              דק׳
            </label>
          </div>
          <input dir="auto" placeholder="כתובת" value={address} onChange={(e) => setAddress(e.target.value)} />
          <p className="muted small">האירוע כולל את הכתובת, הטלפון, הקישור למודעה וההערות שלך. הסטטוס יהפוך ל״נקבעה פגישה״.</p>
          <button className="btn primary" disabled={busy || !date || !time || ready === false} onClick={add}>
            הוסף ליומן
          </button>
        </div>
      )}
    </details>
  );
}
