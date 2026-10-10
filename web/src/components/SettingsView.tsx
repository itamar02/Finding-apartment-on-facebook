import { useEffect, useState } from "react";
import { api, StatusDef } from "../api";
import { useApp } from "../App";
import GptTool from "./GptTool";
import NotifySettings from "./NotifySettings";
import AiSettings from "./AiSettings";
import SearchSettings from "./SearchSettings";
import LearningPanel from "./LearningPanel";

/** One collapsible block of the settings screen. */
function Section({ title, children, open }: { title: string; children: React.ReactNode; open?: boolean }) {
  return (
    <details className="section" open={open}>
      <summary>{title}</summary>
      <div className="settings">{children}</div>
    </details>
  );
}

const PLACEHOLDERS = ["{name}", "{area}", "{street}", "{group}", "{price}"];
const SAMPLE: Record<string, string> = { name: "דנה", area: "פלורנטין", street: "", group: "דירות בתל אביב", price: "5,000" };

// Mirrors outreach.render_template on the server, for the live preview only.
function preview(t: string): string {
  return t
    .replace(/\[([^[\]]*)\]/g, (_, part: string) =>
      (part.match(/\{(\w+)\}/g) ?? []).some((p) => !SAMPLE[p.slice(1, -1)]) ? "" : part
    )
    .replace(/\{(\w+)\}/g, (_, k: string) => SAMPLE[k] ?? "")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

export default function SettingsView({ onLogout }: { onLogout: () => void }) {
  const app = useApp();
  const [template, setTemplate] = useState(app.meta.settings.whatsapp_template);
  const [recent, setRecent] = useState(String(app.meta.settings.recent_days ?? ""));
  const [myName, setMyName] = useState(app.meta.settings.whatsapp_my_name ?? "");

  async function save() {
    try {
      await api.saveSettings({
        whatsapp_template: template,
        recent_days: recent ? Number(recent) : null,
        whatsapp_my_name: myName.trim() || null,
      });
      await app.reloadMeta();
      app.toast({ text: "נשמר" });
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <div className="settings">
      <Section title="הדרישות שלי (מה ה-AI בודק)">
        <SearchSettings />
      </Section>

      <Section title="הצעות לשיפור החיפוש">
        <LearningPanel />
      </Section>

      <Section title="וואטסאפ ומודעות ישנות">
        <h2>הודעת פתיחה בוואטסאפ</h2>
        <p className="muted small">
          משתנים: {PLACEHOLDERS.join(" ")}. חלק בסוגריים מרובעים [ ] יושמט אם חסר בו מידע.
        </p>
        <textarea rows={4} value={template} onChange={(e) => setTemplate(e.target.value)} dir="auto" />
        <p className="preview small">{preview(template)}</p>

        <label className="label">השם שלי בוואטסאפ (כדי לזהות בשיחות מי אמר מה)</label>
        <input dir="auto" value={myName} onChange={(e) => setMyName(e.target.value)} />

        <h2>מודעות ישנות</h2>
        <label className="check">
          להסתיר מודעות מעל
          <input className="short" inputMode="numeric" value={recent} placeholder={String(app.meta.recent_days)} onChange={(e) => setRecent(e.target.value.replace(/\D/g, ""))} />
          ימים (גם בייצוא)
        </label>
        <button className="btn primary" onClick={save}>
          שמור
        </button>
      </Section>

      <Section title="סטטוסים ורשימת בדיקה לביקור">
        <StatusEditor />
        <VisitItemsEditor />
      </Section>

      <Section title="התראות, תזכורות וסריקה אוטומטית">
        <NotifySettings />
      </Section>

      <Section title="ספקי AI">
        <AiSettings />
      </Section>

      <Section title="מיון עם ChatGPT">
        <GptTool />
      </Section>

      <h2>חשבון</h2>
      <button
        className="btn"
        onClick={async () => {
          await api.logout().catch(() => {});
          onLogout();
        }}
      >
        התנתק בטלפון הזה
      </button>
    </div>
  );
}

function VisitItemsEditor() {
  const app = useApp();
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    api.visitItems().then((items) => setText(items.map((i) => i.label).join("\n"))).catch(app.fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (text === null) return null;
  return (
    <>
      <h2>רשימת בדיקה לביקור</h2>
      <p className="muted small">שורה לכל פריט, לפי הסדר.</p>
      <textarea rows={7} dir="auto" value={text} onChange={(e) => setText(e.target.value)} />
      <button
        className="btn"
        onClick={async () => {
          try {
            await api.saveVisitItems(text.split("\n"));
            app.toast({ text: "נשמר" });
          } catch (e) {
            app.fail(e);
          }
        }}
      >
        שמור רשימה
      </button>
    </>
  );
}

function StatusEditor() {
  const app = useApp();
  const list = app.meta.statuses;
  const [adding, setAdding] = useState({ label: "", color: "#0ea5e9" });

  async function run(p: Promise<unknown>) {
    try {
      await p;
      await app.reloadMeta();
    } catch (e) {
      app.fail(e);
    }
  }

  function move(i: number, dir: -1 | 1) {
    const keys = list.map((s) => s.key);
    const j = i + dir;
    if (j < 0 || j >= keys.length) return;
    [keys[i], keys[j]] = [keys[j], keys[i]];
    run(api.reorderStatuses(keys));
  }

  return (
    <>
      <h2>סטטוסים</h2>
      {list.map((s: StatusDef, i) => (
        <div key={s.key} className="status-row">
          <input type="color" value={s.color} onChange={(e) => run(api.editStatus(s.key, s.label, e.target.value, !!s.needs_note))} />
          <input defaultValue={s.label} onBlur={(e) => e.target.value.trim() && e.target.value !== s.label && run(api.editStatus(s.key, e.target.value, s.color, !!s.needs_note))} />
          <label className="check small" title="מבקש הערה כשבוחרים">
            <input type="checkbox" checked={!!s.needs_note} onChange={(e) => run(api.editStatus(s.key, s.label, s.color, e.target.checked))} />
            הערה
          </label>
          <button className="icon" onClick={() => move(i, -1)} aria-label="למעלה">
            ▲
          </button>
          <button className="icon" onClick={() => move(i, 1)} aria-label="למטה">
            ▼
          </button>
          {!s.built_in && (
            <button className="icon" onClick={() => confirm(`למחוק את "${s.label}"?`) && run(api.deleteStatus(s.key))} aria-label="מחק">
              ✕
            </button>
          )}
        </div>
      ))}
      <div className="status-row">
        <input type="color" value={adding.color} onChange={(e) => setAdding({ ...adding, color: e.target.value })} />
        <input placeholder="סטטוס חדש" value={adding.label} onChange={(e) => setAdding({ ...adding, label: e.target.value })} />
        <button
          className="btn"
          disabled={!adding.label.trim()}
          onClick={() => {
            run(api.addStatus(adding.label.trim(), adding.color, false));
            setAdding({ label: "", color: "#0ea5e9" });
          }}
        >
          הוסף
        </button>
      </div>
    </>
  );
}
