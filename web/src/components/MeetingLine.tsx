import { useEffect, useState } from "react";
import { api, Card } from "../api";
import { useApp } from "../App";

function describe(at: string): { when: string; relative: string; past: boolean } {
  const d = new Date(at);
  const when = `${d.toLocaleDateString("he-IL", { weekday: "long", day: "numeric", month: "numeric" })} · ${d.toLocaleTimeString("he-IL", { hour: "2-digit", minute: "2-digit" })}`;
  const startOfDay = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((startOfDay(d) - startOfDay(new Date())) / 86400000);
  const relative = days === 0 ? "היום" : days === 1 ? "מחר" : days > 1 ? `בעוד ${days} ימים` : days === -1 ? "אתמול" : `לפני ${-days} ימים`;
  return { when, relative, past: days < 0 };
}

/** On a listing at "meeting scheduled": when the viewing is, without opening the details. Editable. */
export default function MeetingLine({ card, startEditing = false, onDone }: { card: Card; startEditing?: boolean; onDone?: () => void }) {
  const app = useApp();
  const [meeting, setMeeting] = useState(card.meeting);
  const [editing, setEditing] = useState(startEditing);
  const [date, setDate] = useState("");
  const [time, setTime] = useState("18:00");
  const [address, setAddress] = useState("");

  useEffect(() => setMeeting(card.meeting), [card.meeting]);
  useEffect(() => {
    if (startEditing) openEditor();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startEditing]);

  if (card.status?.status !== "meeting_scheduled" && !startEditing) return null;

  function openEditor() {
    if (meeting?.at) {
      setDate(meeting.at.slice(0, 10));
      setTime(meeting.at.slice(11, 16));
    }
    setAddress(meeting?.address ?? card.location.text ?? "");
    setEditing(true);
  }

  async function save(at: string | null) {
    try {
      const r = await api.setMeeting(card.post_id, at, at ? address || null : null);
      setMeeting(r.meeting);
      if (r.status) app.statusChanged(card.post_id, r.status);
      setEditing(false);
      onDone?.();
    } catch (e) {
      app.fail(e);
    }
  }

  if (editing)
    return (
      <div className="meeting editing">
        <b>📅 מתי הפגישה?</b>
        <div className="row">
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
        <input dir="auto" placeholder="כתובת (לא חובה)" value={address} onChange={(e) => setAddress(e.target.value)} />
        <div className="row">
          <button className="btn primary" disabled={!date || !time} onClick={() => save(`${date}T${time}`)}>
            שמור
          </button>
          <button
            className="btn"
            onClick={() => {
              setEditing(false);
              onDone?.();
            }}
          >
            ביטול
          </button>
          {meeting && (
            <button className="btn" onClick={() => save(null)}>
              הסר מועד
            </button>
          )}
        </div>
      </div>
    );

  if (!meeting)
    return (
      <div className="meeting">
        📅 נקבעה פגישה{" "}
        <button className="link" onClick={openEditor}>
          מתי? הוסף מועד
        </button>
      </div>
    );

  const m = describe(meeting.at);
  return (
    <div className={"meeting" + (m.past ? " past" : "")}>
      <span className="grow">
        📅 <b>פגישה:</b> {m.when} <span className="muted small">({m.relative})</span>
        {meeting.address && (
          <span className="small muted" dir="auto">
            {" "}
            · {meeting.address}
          </span>
        )}
      </span>
      <button className="icon small-icon" aria-label="שינוי מועד" onClick={openEditor}>
        ✏️
      </button>
    </div>
  );
}
