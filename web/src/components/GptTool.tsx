import { useState } from "react";
import { api, GptPreview } from "../api";
import { useApp } from "../App";

/** Export listings for an AI chat (ChatGPT, Claude, Gemini...) and import its statuses back, with a preview. */
export default function GptTool() {
  const app = useApp();
  const [includeOld, setIncludeOld] = useState(false);
  const [exported, setExported] = useState<{ text: string; count: number } | null>(null);
  const [reply, setReply] = useState("");
  const [preview, setPreview] = useState<GptPreview | null>(null);
  const [override, setOverride] = useState(false);
  const [busy, setBusy] = useState(false);

  async function doExport() {
    setBusy(true);
    try {
      const r = await api.gptExport(includeOld);
      setExported({ text: r.text, count: r.count });
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!exported) return;
    try {
      await navigator.clipboard.writeText(exported.text);
      app.toast({ text: "הועתק. הדבק בצ׳אט של ה-AI." });
    } catch {
      app.toast({ text: "ההעתקה נכשלה; השתמש בהורדה." });
    }
  }

  function download() {
    if (!exported) return;
    const url = URL.createObjectURL(new Blob([exported.text], { type: "text/plain;charset=utf-8" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "apartments-for-ai.txt" });
    a.click();
    URL.revokeObjectURL(url);
  }

  async function check() {
    setBusy(true);
    try {
      setPreview(await api.gptPreview(reply));
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    try {
      const r = await api.gptApply(reply, override);
      app.toast({ text: `עודכנו ${r.applied} מודעות${r.skipped ? `, ${r.skipped} דולגו (סטטוס שקבעת בעצמך)` : ""}` });
      setPreview(null);
      setReply("");
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  const label = (key: string | undefined) => (key ? app.statusOf(key)?.label ?? key : "ללא");

  return (
    <>
      <h2>מיון עם צ׳אט AI</h2>
      <p className="muted small">
        1. ייצא את המודעות שעוד אין להן סטטוס שקבעת. 2. הדבק בצ׳אט. 3. הדבק כאן את התשובה, בדוק ואשר.
        שים לב: המודעות כוללות מספרי טלפון של אנשים אחרים.
      </p>
      <label className="check small">
        <input type="checkbox" checked={includeOld} onChange={(e) => setIncludeOld(e.target.checked)} />
        כולל מודעות ישנות
      </label>
      <div className="row">
        <button className="btn" disabled={busy} onClick={doExport}>
          ייצוא
        </button>
        {exported && (
          <>
            <span className="muted small">{exported.count} מודעות</span>
            <button className="btn" onClick={copy}>
              העתק
            </button>
            <button className="btn" onClick={download}>
              הורד קובץ
            </button>
          </>
        )}
      </div>

      <textarea rows={5} dir="ltr" placeholder='[{"post_id": "...", "status": "...", "note": "..."}]' value={reply} onChange={(e) => setReply(e.target.value)} />
      <div className="row">
        <label className="btn">
          טען קובץ
          <input
            type="file"
            accept=".json,.txt,text/plain,application/json"
            hidden
            onChange={async (e) => {
              const f = e.target.files?.[0];
              if (f) setReply(await f.text());
            }}
          />
        </label>
        <button className="btn primary" disabled={busy || !reply.trim()} onClick={check}>
          בדוק
        </button>
      </div>

      {preview && (
        <div className="preview-box">
          {preview.errors.length > 0 && (
            <ul className="error small">
              {preview.errors.map((e) => (
                <li key={e} dir="auto">
                  {e}
                </li>
              ))}
            </ul>
          )}
          <p className="small">
            {preview.changes.length} שינויים · {preview.conflicts.length} מתנגשים עם סטטוס שקבעת · {preview.unchanged} ללא שינוי
          </p>
          {[...preview.changes, ...(override ? preview.conflicts : [])].map((c) => (
            <div key={c.post_id} className="diff-row">
              <button className="link" onClick={() => app.openPost(c.post_id)}>
                {c.post_id}
              </button>
              <span>
                {label(c.current?.status)} ← <b>{c.label}</b>
              </span>
              {c.note && (
                <div className="small" dir="auto">
                  {c.note}
                </div>
              )}
            </div>
          ))}
          {preview.conflicts.length > 0 && (
            <label className="check small">
              <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
              לדרוס גם {preview.conflicts.length} סטטוסים שקבעתי בעצמי
            </label>
          )}
          <button
            className="btn primary"
            disabled={busy || preview.changes.length + (override ? preview.conflicts.length : 0) === 0}
            onClick={apply}
          >
            אשר ועדכן
          </button>
        </div>
      )}
    </>
  );
}
