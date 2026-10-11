import { useEffect, useState } from "react";
import { api, Detail } from "../api";
import { useApp } from "../App";

/** Add a viewing I arranged to Google Calendar: one event, only when I tap the button. */
export default function CalendarAdd({ d, onChanged }: { d: Detail; onChanged?: () => void }) {
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

  /** No setup needed: open Google Calendar's own "new event" page, filled in; the user taps Save there. */
  function openInGoogle() {
    const pad = (n: number) => String(n).padStart(2, "0");
    const start = new Date(`${date}T${time}:00`);
    const end = new Date(start.getTime() + minutes * 60000);
    const stamp = (x: Date) => `${x.getFullYear()}${pad(x.getMonth() + 1)}${pad(x.getDate())}T${pad(x.getHours())}${pad(x.getMinutes())}00`;
    const price = d.price.rent ? `מחיר: ${d.price.rent.toLocaleString("he-IL")} ₪` : null;
    const details = [
      d.author ? `מפרסם/ת: ${d.author}` : null,
      d.contact.phones.length ? `טלפון: ${d.contact.phones.join(", ")}` : null,
      price,
      `מודעה: ${d.url}`,
      ...d.notes.map((n) => `הערה: ${n.body}`),
    ]
      .filter(Boolean)
      .join("\n");
    const q = new URLSearchParams({
      action: "TEMPLATE",
      text: `צפייה בדירה${address ? `: ${address}` : ""}`,
      dates: `${stamp(start)}/${stamp(end)}`,
      ctz: "Asia/Jerusalem",
      location: address,
      details,
    });
    window.open(`https://calendar.google.com/calendar/render?${q.toString()}`, "_blank", "noopener");
    api
      .setMeeting(d.post_id, `${date}T${time}`, address || null)
      .then((r) => {
        if (r.status) app.statusChanged(d.post_id, r.status);
        onChanged?.();
      })
      .catch(app.fail);
    app.toast({ text: "נפתח ביומן גוגל: לחץ שם על שמירה" });
  }

  async function add() {
    if (!confirm(`להוסיף ליומן גוגל?\n${date} ${time}\n${address || "ללא כתובת"}`)) return;
    setBusy(true);
    try {
      const r = await api.addToCalendar(d.post_id, { date, time, address: address || null, minutes, notes: null });
      setDone(r.event_link ?? "");
      onChanged?.();
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
            <p className="small muted">
              בלי חיבור מיוחד: הכפתור הראשון פותח את יומן גוגל שלך במילוי מראש, ואתה רק לוחץ שם על שמירה. החיבור האוטומטי (הכפתור השני) דורש הגדרה חד־פעמית במחשב, ראו README.
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
          <div className="row">
            <button className="btn primary" disabled={!date || !time} onClick={openInGoogle}>
              פתח ביומן גוגל (בלי הגדרות)
            </button>
            <button className="btn" disabled={busy || !date || !time || ready === false} onClick={add} title={ready === false ? "דורש חיבור חד־פעמי" : undefined}>
              הוסף אוטומטית
            </button>
          </div>
        </div>
      )}
    </details>
  );
}
