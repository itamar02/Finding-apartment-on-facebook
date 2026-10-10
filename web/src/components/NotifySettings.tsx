import { useEffect, useState } from "react";
import { api, Decision, NotificationSettings, QuietHours, SchedulerState } from "../api";
import { useApp } from "../App";
import { dateTime, DECISION_LABEL } from "../format";

function b64ToBytes(b64: string): Uint8Array {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function QuietHoursInput({ value, onChange }: { value: QuietHours | null; onChange: (v: QuietHours | null) => void }) {
  return (
    <div className="row">
      <label className="check">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked ? { start: "23:00", end: "07:00" } : null)} />
        שעות שקטות
      </label>
      {value && (
        <>
          <input className="time" type="time" value={value.start} onChange={(e) => onChange({ ...value, start: e.target.value })} />
          <span>עד</span>
          <input className="time" type="time" value={value.end} onChange={(e) => onChange({ ...value, end: e.target.value })} />
        </>
      )}
    </div>
  );
}

/** Notifications (push / Telegram), follow-up hours and the background scheduler. */
export default function NotifySettings() {
  const app = useApp();
  const s = app.meta.settings;
  const [n, setN] = useState<NotificationSettings>(s.notifications);
  const [hours, setHours] = useState<Record<string, number>>(s.followup_hours);
  const [appUrl, setAppUrl] = useState(s.app_url ?? "");
  const [sched, setSched] = useState(s.scheduler);
  const [state, setState] = useState<SchedulerState | null>(null);
  const [info, setInfo] = useState<{ telegram: { configured: boolean; from_watchdog: boolean }; push_devices: number } | null>(null);
  const [tg, setTg] = useState({ token: "", chat_id: "" });
  const [pushOn, setPushOn] = useState<boolean | null>(null);

  async function refresh() {
    try {
      setState(await api.scheduler());
      setInfo(await api.notifications());
      const reg = "serviceWorker" in navigator ? await navigator.serviceWorker.getRegistration() : undefined;
      setPushOn(!!(reg && (await reg.pushManager.getSubscription())));
    } catch (e) {
      app.fail(e);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    try {
      await api.saveSettings({ notifications: n, followup_hours: hours, scheduler: sched, app_url: appUrl.trim() || null });
      await app.reloadMeta();
      app.toast({ text: "נשמר" });
      refresh();
    } catch (e) {
      app.fail(e);
    }
  }

  async function enablePush() {
    try {
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        app.toast({ text: "הדפדפן לא תומך בהתראות. באייפון: הוסף קודם למסך הבית ופתח משם." });
        return;
      }
      if ((await Notification.requestPermission()) !== "granted") {
        app.toast({ text: "לא ניתנה הרשאה להתראות" });
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const { public_key } = await api.pushKey();
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(public_key) });
      await api.pushSubscribe(sub.toJSON());
      app.toast({ text: "התראות הופעלו בטלפון הזה" });
      refresh();
    } catch (e) {
      app.fail(e);
    }
  }

  async function disablePush() {
    try {
      const reg = await navigator.serviceWorker.getRegistration();
      const sub = reg && (await reg.pushManager.getSubscription());
      if (sub) {
        await api.pushUnsubscribe(sub.toJSON());
        await sub.unsubscribe();
      }
      refresh();
    } catch (e) {
      app.fail(e);
    }
  }

  async function test() {
    try {
      const r = await api.testNotification();
      app.toast({ text: r.sent ? "נשלחה התראת בדיקה" : r.note ?? `לא נשלח: ${r.error ?? "?"}` });
    } catch (e) {
      app.fail(e);
    }
  }

  const toggleDecision = (d: Decision) =>
    setN({ ...n, decisions: n.decisions.includes(d) ? n.decisions.filter((x) => x !== d) : [...n.decisions, d] });

  return (
    <>
      <h2>התראות</h2>
      <label className="check">
        <input type="checkbox" checked={n.enabled} onChange={(e) => setN({ ...n, enabled: e.target.checked })} />
        לשלוח התראות
      </label>
      <label className="label">ערוץ</label>
      <div className="pills">
        {(["push", "telegram", "both"] as const).map((c) => (
          <button key={c} className={"pill" + (n.channel === c ? " on" : "")} onClick={() => setN({ ...n, channel: c })}>
            {c === "push" ? "התראה בטלפון" : c === "telegram" ? "טלגרם" : "שניהם"}
          </button>
        ))}
      </div>
      {n.channel !== "telegram" && (
        <div className="row">
          <span className="small muted">
            {pushOn ? "פעיל בטלפון הזה" : "לא פעיל בטלפון הזה"} · {info?.push_devices ?? 0} מכשירים
          </span>
          {pushOn ? (
            <button className="btn" onClick={disablePush}>
              כבה כאן
            </button>
          ) : (
            <button className="btn" onClick={enablePush}>
              הפעל בטלפון הזה
            </button>
          )}
        </div>
      )}
      {n.channel !== "push" && (
        <>
          <p className="small muted">
            {info?.telegram.configured
              ? info.telegram.from_watchdog
                ? "משתמש בבוט הטלגרם של ה-watchdog."
                : "טלגרם מוגדר."
              : "טלגרם לא מוגדר: צור בוט עם BotFather, שלח לו הודעה, והזן כאן את הטוקן ואת מזהה הצ׳אט."}
          </p>
          <div className="row nowrap">
            <input dir="ltr" placeholder="bot token" value={tg.token} onChange={(e) => setTg({ ...tg, token: e.target.value })} />
            <input dir="ltr" placeholder="chat id" value={tg.chat_id} onChange={(e) => setTg({ ...tg, chat_id: e.target.value })} />
            <button
              className="btn"
              disabled={!tg.token || !tg.chat_id}
              onClick={async () => {
                try {
                  await api.saveTelegram(tg.token, tg.chat_id);
                  setTg({ token: "", chat_id: "" });
                  refresh();
                } catch (e) {
                  app.fail(e);
                }
              }}
            >
              שמור
            </button>
          </div>
        </>
      )}
      <label className="label">על אילו מודעות חדשות</label>
      <div className="pills">
        {(["PASS", "NEEDS_REVIEW", "REJECT"] as Decision[]).map((d) => (
          <button key={d} className={"pill" + (n.decisions.includes(d) ? " on" : "")} onClick={() => toggleDecision(d)}>
            {DECISION_LABEL[d]}
          </button>
        ))}
      </div>
      <label className="check">
        רק עד מחיר
        <input
          className="short"
          inputMode="numeric"
          value={n.price_ceiling ?? ""}
          placeholder="ללא"
          onChange={(e) => setN({ ...n, price_ceiling: e.target.value ? Number(e.target.value.replace(/\D/g, "")) : null })}
        />
        ₪
      </label>
      <label className="check">
        <input type="checkbox" checked={n.edits} onChange={(e) => setN({ ...n, edits: e.target.checked })} />
        מודעה שראיתי נערכה
      </label>
      <label className="check">
        <input type="checkbox" checked={n.followups} onChange={(e) => setN({ ...n, followups: e.target.checked })} />
        תזכורות מעקב
      </label>
      <label className="check">
        <input type="checkbox" checked={n.price_drop} onChange={(e) => setN({ ...n, price_drop: e.target.checked })} />
        ירידת מחיר של לפחות
        <input className="short" inputMode="numeric" value={n.price_drop_min} onChange={(e) => setN({ ...n, price_drop_min: Number(e.target.value.replace(/\D/g, "")) || 0 })} />₪
      </label>
      <QuietHoursInput value={n.quiet_hours} onChange={(q) => setN({ ...n, quiet_hours: q })} />
      <label className="label">כתובת האפליקציה (לקישורים בטלגרם)</label>
      <input dir="ltr" placeholder="https://my-pc.tail1234.ts.net" value={appUrl} onChange={(e) => setAppUrl(e.target.value)} />

      <h2>תזכורות מעקב</h2>
      <p className="muted small">אחרי כמה שעות בסטטוס להזכיר (ריק = בלי תזכורת).</p>
      {app.meta.statuses.map((st) => (
        <label key={st.key} className="check">
          <span className="grow">{st.label}</span>
          <input
            className="short"
            inputMode="numeric"
            value={hours[st.key] ?? ""}
            onChange={(e) => {
              const v = e.target.value.replace(/\D/g, "");
              const next = { ...hours };
              if (v) next[st.key] = Number(v);
              else delete next[st.key];
              setHours(next);
            }}
          />
          שעות
        </label>
      ))}

      <h2>סריקה אוטומטית</h2>
      <p className="muted small">השרת במחשב סורק את הקבוצות, מנתח מודעות חדשות ושולח התראות. במקום ה-watchdog.</p>
      <label className="check">
        <input type="checkbox" checked={sched.enabled} onChange={(e) => setSched({ ...sched, enabled: e.target.checked })} />
        לסרוק כל
        <input className="short" inputMode="numeric" value={sched.interval_minutes} onChange={(e) => setSched({ ...sched, interval_minutes: Number(e.target.value.replace(/\D/g, "")) || 30 })} />
        דקות
      </label>
      <label className="check">
        <input type="checkbox" checked={sched.analyze} onChange={(e) => setSched({ ...sched, analyze: e.target.checked })} />
        לנתח עם AI אחרי כל סריקה, עד
        <input className="short" inputMode="numeric" value={sched.analyze_limit} onChange={(e) => setSched({ ...sched, analyze_limit: Number(e.target.value.replace(/\D/g, "")) })} />
        מודעות (0 = הכל)
      </label>
      <QuietHoursInput value={sched.quiet_hours} onChange={(q) => setSched({ ...sched, quiet_hours: q })} />
      {state && (
        <p className="small muted">
          {state.running
            ? `רץ עכשיו: ${state.step === "scan" ? "סורק" : state.step === "analyze" ? "מנתח" : "שולח התראות"}`
            : `אחרון: ${dateTime(state.last_end) || "עוד לא רץ"}${state.last_result ? ` · ${state.last_result}` : ""}`}
          {state.next_run && !state.running && sched.enabled && ` · הבא: ${dateTime(state.next_run)}`}
        </p>
      )}
      <div className="row">
        <button className="btn primary" onClick={save}>
          שמור
        </button>
        <button className="btn" onClick={test}>
          התראת בדיקה
        </button>
        <button
          className="btn"
          disabled={state?.running}
          onClick={async () => {
            try {
              await api.runScheduler();
              app.toast({ text: "סריקה התחילה" });
              window.setTimeout(refresh, 3000);
            } catch (e) {
              app.fail(e);
            }
          }}
        >
          סרוק עכשיו
        </button>
      </div>
    </>
  );
}
