import { useState } from "react";
import { api, MyStatus, StatusDef } from "../api";
import { useApp } from "../App";

/** Change a post's status with an Undo toast; shared by cards, the board and the detail view. */
export function useStatusActions() {
  const app = useApp();

  async function change(postId: string, status: string | null, note?: string | null) {
    try {
      const r = await api.setStatus(postId, status, note);
      app.statusChanged(postId, r.status);
      const label = status ? app.statusOf(status)?.label ?? status : "ללא סטטוס";
      app.toast({ text: `הסטטוס שונה ל: ${label}`, undo: () => undo(postId) });
    } catch (e) {
      app.fail(e);
    }
  }

  async function undo(postId: string) {
    try {
      const r = await api.undoStatus(postId);
      app.statusChanged(postId, r.status);
    } catch (e) {
      app.fail(e);
    }
  }

  /** The contact button: open WhatsApp / Facebook and mark the post as contacted (undoable). */
  async function contact(postId: string, url: string, current: MyStatus | null) {
    window.open(url, "_blank", "noopener");
    if (current?.status === "contacted") return;
    try {
      const r = await api.contacted(postId);
      app.statusChanged(postId, r.status);
      app.toast({ text: "סומן כ״פניתי״", undo: () => undo(postId) });
    } catch (e) {
      app.fail(e);
    }
  }

  return { change, contact };
}

export function StatusChip({ status, onClick }: { status: MyStatus | null; onClick?: () => void }) {
  const { statusOf } = useApp();
  const def = statusOf(status?.status);
  return (
    <button
      className="status-chip"
      style={def ? { background: def.color } : undefined}
      onClick={onClick}
      title={status?.note ?? undefined}
    >
      {def ? def.label : "+ סטטוס"}
    </button>
  );
}

export default function StatusPicker({
  current,
  aiLabel,
  onPick,
  onClose,
}: {
  current: MyStatus | null;
  aiLabel?: string;
  onPick: (status: string | null, note: string | null) => void;
  onClose: () => void;
}) {
  const { meta } = useApp();
  const [noteFor, setNoteFor] = useState<StatusDef | null>(null);
  const [note, setNote] = useState(current?.note ?? "");

  function pick(s: StatusDef) {
    if (s.needs_note) {
      setNoteFor(s);
      return;
    }
    onPick(s.key, current?.status === s.key ? current.note : null);
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        {noteFor ? (
          <>
            <h3>{noteFor.label}: מה צריך לברר?</h3>
            <textarea autoFocus rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
            <div className="row">
              <button className="btn primary" onClick={() => onPick(noteFor.key, note.trim() || null)}>
                שמור
              </button>
              <button className="btn" onClick={() => setNoteFor(null)}>
                חזרה
              </button>
            </div>
          </>
        ) : (
          <>
            <h3>ההחלטה שלי</h3>
            {aiLabel && (
              <p className="muted small">
                ה-AI סימן: <b>{aiLabel}</b>. אחרי שבדקת בעצמך, בחר מה לעשות איתה (ההחלטה של ה-AI נשארת כפי שהיא).
              </p>
            )}
            <div className="row">
              <button className="btn vote-wide on-good-btn" onClick={() => onPick("interested", current?.status === "interested" ? current.note : null)}>
                👍 מתאימה לי
              </button>
              <button className="btn vote-wide on-bad-btn" onClick={() => onPick("not_relevant", null)}>
                🗑️ לא מתאימה
              </button>
            </div>
            <span className="label">או סטטוס אחר:</span>
            <div className="status-grid">
              {meta.statuses.map((s) => (
                <button
                  key={s.key}
                  className={"status-option" + (current?.status === s.key ? " on" : "")}
                  style={{ borderColor: s.color }}
                  onClick={() => pick(s)}
                >
                  <span className="dot" style={{ background: s.color }} />
                  {s.label}
                </button>
              ))}
            </div>
            {current && (
              <button className="btn subtle" onClick={() => onPick(null, null)}>
                הסר סטטוס
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
