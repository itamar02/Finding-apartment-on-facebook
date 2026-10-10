import { useEffect, useState } from "react";
import { api, Visit } from "../api";
import { useApp } from "../App";

const STATES: { key: "ok" | "issue" | "na"; label: string }[] = [
  { key: "ok", label: "תקין" },
  { key: "issue", label: "בעיה" },
  { key: "na", label: "לא רלוונטי" },
];

/** Visit checklist, notes, my photos and a 1-5 rating for one listing (inside the post details). */
export default function VisitPanel({ postId }: { postId: string }) {
  const app = useApp();
  const [v, setV] = useState<Visit | null>(null);
  const [dirty, setDirty] = useState(false);
  const [uploading, setUploading] = useState(0);

  useEffect(() => {
    api.visit(postId).then(setV).catch(app.fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postId]);

  if (!v) return null;

  const change = (next: Visit) => {
    setV(next);
    setDirty(true);
  };

  async function save() {
    if (!v) return;
    try {
      setV(
        await api.saveVisit(postId, {
          rating: v.rating,
          notes: v.notes,
          checks: v.checklist.map((c) => ({ item_id: c.item_id, state: c.state, note: c.note })),
        })
      );
      setDirty(false);
      app.toast({ text: "הביקור נשמר" });
    } catch (e) {
      app.fail(e);
    }
  }

  async function upload(files: FileList | null) {
    if (!files) return;
    setUploading(files.length);
    try {
      for (const f of Array.from(files)) await api.uploadVisitPhoto(postId, f);
      setV({ ...(await api.visit(postId)), rating: v!.rating, notes: v!.notes, checklist: v!.checklist });
    } catch (e) {
      app.fail(e);
    } finally {
      setUploading(0);
    }
  }

  async function removePhoto(id: number) {
    if (!confirm("למחוק את התמונה?")) return;
    try {
      await api.deleteVisitPhoto(id);
      setV({ ...v!, photos: v!.photos.filter((p) => p.id !== id) });
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <section className="visit">
      <h3>ביקור</h3>
      <div className="stars" role="radiogroup" aria-label="דירוג">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} className={(v.rating ?? 0) >= n ? "on" : ""} onClick={() => change({ ...v, rating: v.rating === n ? null : n })} aria-label={`${n}`}>
            ★
          </button>
        ))}
      </div>
      {v.checklist.map((c, i) => (
        <div key={c.item_id} className="check-item">
          <span className="grow">{c.label}</span>
          <div className="seg">
            {STATES.map((s) => (
              <button
                key={s.key}
                className={c.state === s.key ? `on ${s.key}` : ""}
                onClick={() => {
                  const checklist = [...v.checklist];
                  checklist[i] = { ...c, state: c.state === s.key ? null : s.key };
                  change({ ...v, checklist });
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
          {c.state === "issue" && (
            <input
              placeholder="מה הבעיה?"
              value={c.note ?? ""}
              onChange={(e) => {
                const checklist = [...v.checklist];
                checklist[i] = { ...c, note: e.target.value || null };
                change({ ...v, checklist });
              }}
            />
          )}
        </div>
      ))}
      <textarea rows={3} placeholder="הערות מהביקור" value={v.notes ?? ""} onChange={(e) => change({ ...v, notes: e.target.value || null })} />
      <button className="btn primary" disabled={!dirty} onClick={save}>
        שמור ביקור
      </button>

      <div className="gallery">
        {v.photos.map((p) => (
          <img key={p.id} src={p.src} alt="" onClick={() => removePhoto(p.id)} title="הקש למחיקה" />
        ))}
      </div>
      <label className="btn">
        {uploading ? `מעלה ${uploading}…` : "📷 הוסף תמונות"}
        <input type="file" accept="image/*" multiple hidden onChange={(e) => upload(e.target.files)} />
      </label>
      <p className="muted small">התמונות נשמרות רק במחשב.</p>
    </section>
  );
}
