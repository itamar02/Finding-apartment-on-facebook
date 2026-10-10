import { useEffect, useState } from "react";
import { api, ChatResult, Proposal, VisitRow } from "../api";
import { useApp } from "../App";
import { dateTime, money } from "../format";

/** Visits: WhatsApp chat -> meeting proposal -> confirmed calendar event, and the comparison of visited apartments. */
export default function Visits({ shared, onSharedUsed }: { shared: { text: string; title?: string } | null; onSharedUsed: () => void }) {
  const app = useApp();
  const [rows, setRows] = useState<VisitRow[]>([]);
  const [chat, setChat] = useState(shared?.text ?? "");
  const [title, setTitle] = useState(shared?.title);
  const [source, setSource] = useState(shared ? "share" : "paste");
  const [result, setResult] = useState<ChatResult | null>(null);
  const [meetings, setMeetings] = useState<Proposal[]>([]);
  const [calendar, setCalendar] = useState<{ client_file: boolean; signed_in: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  async function load() {
    try {
      setRows(await api.visits());
      const m = await api.meetings();
      setMeetings(m.items);
      setCalendar(m.calendar);
    } catch (e) {
      app.fail(e);
    }
  }

  useEffect(() => {
    load();
    if (shared) onSharedUsed();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function check() {
    setBusy(true);
    try {
      setResult(await api.addChat(chat, source, title));
      load();
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function link(postId: string) {
    if (!result) return;
    try {
      await api.linkChat(result.chat_id, postId);
      setResult({ ...result, post_id: postId, proposal: { ...result.proposal, post_id: postId } });
      load();
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <div className="settings">
      <h2>קביעת צפייה מוואטסאפ</h2>
      <p className="muted small">
        שתפו שיחה מוואטסאפ לאפליקציה (באנדרואיד: ייצוא צ׳אט ← שיתוף), או הדביקו אותה כאן / טענו את קובץ ה-txt. המערכת מציעה פגישה רק אם
        הסכמתם במפורש על שעה, ומוסיפה ליומן רק אחרי שתאשרו.
      </p>
      <textarea rows={6} dir="auto" placeholder="הדבק כאן את השיחה" value={chat} onChange={(e) => setChat(e.target.value)} />
      <div className="row">
        <label className="btn">
          טען קובץ שיחה
          <input
            type="file"
            accept=".txt,text/plain"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) {
                setChat(await f.text());
                setTitle(f.name);
                setSource("upload");
              }
            }}
          />
        </label>
        <button className="btn primary" disabled={busy || !chat.trim()} onClick={check}>
          {busy ? "בודק…" : "בדוק שיחה"}
        </button>
      </div>

      {result && (
        <div className="preview-box">
          {result.post_id ? (
            <p className="small">
              מקושר למודעה:{" "}
              <button className="link" onClick={() => app.openPost(result.post_id!)}>
                {result.post_id}
              </button>
            </p>
          ) : result.candidates.length ? (
            <>
              <p className="small">נמצאו כמה מודעות עם אותו טלפון. בחרו:</p>
              {result.candidates.map((c) => (
                <button key={c.post_id} className="btn" onClick={() => link(c.post_id)}>
                  {c.author ?? c.post_id} · {c.snippet.slice(0, 40)}
                </button>
              ))}
            </>
          ) : (
            <LinkByHand onPick={link} />
          )}
          <ProposalCard p={result.proposal} onDone={load} calendarReady={!!calendar?.signed_in} />
        </div>
      )}

      {meetings.filter((m) => m.status === "proposed" && m.id !== result?.proposal.id).length > 0 && (
        <>
          <h2>הצעות שממתינות לאישור</h2>
          {meetings
            .filter((m) => m.status === "proposed" && m.id !== result?.proposal.id)
            .map((m) => (
              <ProposalCard key={m.id} p={m} onDone={load} calendarReady={!!calendar?.signed_in} />
            ))}
        </>
      )}

      {calendar && !calendar.signed_in && (
        <p className="small warning">
          יומן גוגל לא מחובר. במחשב: שמרו את קובץ ה-OAuth בתיקייה secrets והריצו <code dir="ltr">python app_server.py --google-login</code>.
        </p>
      )}

      <h2>השוואת דירות שביקרתי</h2>
      {rows.length === 0 && <p className="muted small">עוד אין ביקורים. דירה בסטטוס ״נקבעה פגישה״ ומעלה מופיעה כאן.</p>}
      <div className="compare">
        {rows.map((r) => (
          <button key={r.post_id} className="compare-row" onClick={() => app.openPost(r.post_id)}>
            <div className="row">
              <b>{money(r.rent_ils) ?? "—"}</b>
              <span className="grow" dir="auto">
                {r.area ?? ""}
                {r.rooms ? ` · ${r.rooms} חד׳` : ""}
              </span>
              <span className="stars small">{r.rating ? "★".repeat(r.rating) : "ללא דירוג"}</span>
            </div>
            <div className="small muted">
              {app.statusOf(r.status)?.label ?? ""}
              {r.ok.length > 0 && ` · ✓ ${r.ok.length}`}
              {r.photos > 0 && ` · 📷 ${r.photos}`}
            </div>
            {r.issues.length > 0 && (
              <div className="small issue-text">⚠ {r.issues.map((i) => i.label + (i.note ? ` (${i.note})` : "")).join(" · ")}</div>
            )}
            {r.notes && (
              <div className="small" dir="auto">
                {r.notes}
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

function LinkByHand({ onPick }: { onPick: (id: string) => void }) {
  const [id, setId] = useState("");
  return (
    <div className="row nowrap">
      <input dir="ltr" placeholder="מזהה או קישור למודעה" value={id} onChange={(e) => setId(e.target.value)} />
      <button className="btn" disabled={!id.trim()} onClick={() => onPick((id.match(/(\d{6,})\/?(?:\?.*)?$/) || [, id.trim()])[1]!)}>
        קשר
      </button>
    </div>
  );
}

function ProposalCard({ p, onDone, calendarReady }: { p: Proposal; onDone: () => void; calendarReady: boolean }) {
  const app = useApp();
  const [date, setDate] = useState(p.date ?? "");
  const [time, setTime] = useState(p.time ?? "");
  const [address, setAddress] = useState(p.address ?? "");
  const [minutes, setMinutes] = useState(45);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);

  if (!p.found) return <p className="small muted">לא נמצאה בשיחה הסכמה מפורשת על מועד. {p.explanation ?? ""}</p>;
  if (done !== null)
    return (
      <p className="small">
        נוסף ליומן.{" "}
        {done && (
          <a href={done} target="_blank" rel="noopener noreferrer">
            פתח ביומן
          </a>
        )}
      </p>
    );

  async function confirmIt() {
    setBusy(true);
    try {
      const r = await api.confirmMeeting(p.id, { date, time, address: address || null, minutes, notes: null });
      setDone(r.event_link ?? "");
      app.statusChanged(r.post_id, {
        status: "meeting_scheduled",
        note: `${date} ${time}${address ? `, ${address}` : ""}`,
        source: "human",
        updated_at: new Date().toISOString(),
      });
      app.toast({ text: "הפגישה נוספה ליומן והסטטוס עודכן" });
      onDone();
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="proposal">
      <b>הצעה לפגישה</b>
      <div className="row">
        <input className="time" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        <input className="time" type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        <label className="check small">
          <input className="short" inputMode="numeric" value={minutes} onChange={(e) => setMinutes(Number(e.target.value) || 45)} />
          דק׳
        </label>
      </div>
      <input dir="auto" placeholder="כתובת" value={address} onChange={(e) => setAddress(e.target.value)} />
      <div className="small muted">לפי השורות:</div>
      {p.evidence.map((q) => (
        <blockquote key={q} dir="auto">
          {q}
        </blockquote>
      ))}
      {p.created_at && <div className="small muted">{dateTime(p.created_at)}</div>}
      <div className="row">
        <button className="btn primary" disabled={busy || !calendarReady || !p.post_id || !date || !time} onClick={confirmIt}>
          אשר והוסף ליומן
        </button>
        <button
          className="btn"
          onClick={async () => {
            await api.discardMeeting(p.id).catch(app.fail);
            onDone();
          }}
        >
          בטל
        </button>
      </div>
      {!p.post_id && <p className="small muted">קשרו את השיחה למודעה כדי לאשר.</p>}
    </div>
  );
}
