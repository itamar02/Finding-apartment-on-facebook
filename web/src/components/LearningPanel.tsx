import { useEffect, useState } from "react";
import { api, ConfigProposal } from "../api";
import { useApp } from "../App";

const FIELD: Record<string, string> = {
  "features.dealbreakers": "פוסל",
  "features.required": "חובה",
  "features.nice_to_have": "נחמד שיהיה",
  "areas.excluded": "אזורים פסולים",
  "areas.preferred": "אזורים מועדפים",
  "areas.less_preferred": "אזורים פחות מועדפים",
  "areas.allowed": "רק באזורים",
  "budget.preferred_max_rent": "תקציב מועדף",
  "budget.absolute_max_rent": "תקציב מקסימלי",
  "broker.policy": "תיווך",
  "listing.search_cities": "ערים",
};

/** Suggested config changes based on my statuses and notes; nothing changes until I accept. */
export default function LearningPanel() {
  const app = useApp();
  const [items, setItems] = useState<ConfigProposal[]>([]);
  const [mode, setMode] = useState<string>("on_demand");
  const [lastRun, setLastRun] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .learning()
      .then((r) => {
        setItems(r.proposals);
        setMode(r.settings.mode);
        setLastRun(r.settings.last_run);
      })
      .catch(app.fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function run() {
    setBusy(true);
    try {
      const r = await api.runLearning();
      setItems(r.proposals);
      app.toast({ text: r.added ? `${r.added} הצעות חדשות` : "אין הצעות חדשות כרגע" });
    } catch (e) {
      app.fail(e);
    } finally {
      setBusy(false);
    }
  }

  async function decide(id: number, accept: boolean) {
    try {
      setItems((await api.decideLearning(id, accept)).proposals);
      if (accept) app.toast({ text: "עודכן ב-config.yaml" });
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <div className="form">
      <p className="muted small">המערכת לומדת מהסטטוסים וההערות שלך (בעיקר ״לא רלוונטי״ עם הערה) ומציעה שינויים בדרישות. רק מה שתאשר נשמר.</p>
      <div className="row">
        <select
          value={mode}
          onChange={async (e) => {
            setMode(e.target.value);
            await api.saveSettings({ learning: { mode: e.target.value, last_run: lastRun } }).catch(app.fail);
          }}
        >
          <option value="off">כבוי</option>
          <option value="weekly">פעם בשבוע</option>
          <option value="on_demand">רק כשאבקש</option>
        </select>
        <button className="btn" disabled={busy || mode === "off"} onClick={run}>
          {busy ? "בודק…" : "הצע עכשיו"}
        </button>
      </div>
      {items.length === 0 && <p className="muted small">אין הצעות פתוחות.</p>}
      {items.map((p) => (
        <div key={p.id} className="proposal">
          <b dir="auto">{p.reason}</b>
          <span className="small">
            {p.op === "add" ? "להוסיף ל" : p.op === "remove" ? "להסיר מ" : "לשנות את "}
            {FIELD[p.path] ?? p.path}: <b dir="auto">{String(p.value)}</b>
          </span>
          <span className="small muted">
            לפי {p.evidence.length} מודעות:{" "}
            {p.evidence.slice(0, 5).map((id) => (
              <button key={id} className="link small" onClick={() => app.openPost(id)}>
                {id.slice(-5)}{" "}
              </button>
            ))}
          </span>
          <div className="row">
            <button className="btn primary" onClick={() => decide(p.id, true)}>
              אשר
            </button>
            <button className="btn" onClick={() => decide(p.id, false)}>
              לא
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
