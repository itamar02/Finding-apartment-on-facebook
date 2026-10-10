import { useEffect, useState } from "react";
import { AiState, api, ProviderConf } from "../api";
import { useApp } from "../App";

const STAGE_LABEL: Record<string, string> = {
  gate: "בדיקה ראשונה (האם זו מודעת השכרה)",
  triage: "מעבר מהיר",
  full: "ניתוח מלא",
  group_score: "דירוג קבוצות",
};

/** Choose an AI provider per stage; add OpenAI-compatible providers (Groq, OpenRouter, Gemini...). */
export default function AiSettings() {
  const app = useApp();
  const [state, setState] = useState<AiState | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});

  useEffect(() => {
    api.ai().then(setState).catch(app.fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!state) return null;

  const update = (i: number, patch: Partial<ProviderConf>) =>
    setState({ ...state, ai_providers: state.ai_providers.map((p, j) => (j === i ? { ...p, ...patch } : p)) });

  async function save() {
    if (!state) return;
    try {
      for (const [id, key] of Object.entries(keys)) if (key.trim()) await api.saveAiKey(id, key);
      setKeys({});
      setState(await api.saveAi(state));
      app.toast({ text: "נשמר" });
    } catch (e) {
      app.fail(e);
    }
  }

  const status = (id: string) => state.status.find((s) => s.id === id);
  const unused = state.presets.filter((p) => !state.ai_providers.some((x) => x.id === p.id));

  return (
    <>
      <h2>ספקי AI</h2>
      <p className="small warning">
        שימו לב: שכבות חינמיות של ספקים רבים משתמשות בנתונים שנשלחים אליהן לאימון מודלים, והמודעות כוללות מספרי טלפון ושמות של אנשים
        אחרים. ברירת המחדל (Anthropic, {state.anthropic_model}) משמשת גם כגיבוי כשספק אחר נכשל או מגיע למגבלה.
      </p>

      {state.stages.map((st) => (
        <label key={st} className="check">
          <span className="grow">{STAGE_LABEL[st] ?? st}</span>
          <select value={state.ai_stages[st] ?? "anthropic"} onChange={(e) => setState({ ...state, ai_stages: { ...state.ai_stages, [st]: e.target.value } })}>
            <option value="anthropic">Anthropic</option>
            {state.ai_providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label || p.id}
              </option>
            ))}
          </select>
        </label>
      ))}

      {state.ai_providers.map((p, i) => (
        <div key={i} className="provider">
          <div className="row nowrap">
            <input dir="ltr" placeholder="id" value={p.id} onChange={(e) => update(i, { id: e.target.value.replace(/[^\w-]/g, "") })} />
            <button
              className="icon"
              aria-label="הסר"
              onClick={() =>
                setState({
                  ...state,
                  ai_providers: state.ai_providers.filter((_, j) => j !== i),
                  ai_stages: Object.fromEntries(Object.entries(state.ai_stages).map(([k, v]) => [k, v === p.id ? "anthropic" : v])),
                })
              }
            >
              ✕
            </button>
          </div>
          <input dir="ltr" placeholder="https://.../v1" value={p.base_url} onChange={(e) => update(i, { base_url: e.target.value })} />
          <input dir="ltr" placeholder="model" value={p.model} onChange={(e) => update(i, { model: e.target.value })} />
          <div className="row">
            <select value={p.json_mode ?? "json_object"} onChange={(e) => update(i, { json_mode: e.target.value as ProviderConf["json_mode"] })}>
              <option value="json_object">JSON רגיל</option>
              <option value="json_schema">JSON schema</option>
            </select>
            <label className="check small">
              לדקה
              <input className="short" inputMode="numeric" value={p.per_minute ?? ""} onChange={(e) => update(i, { per_minute: Number(e.target.value) || null })} />
            </label>
            <label className="check small">
              ליום
              <input className="short" inputMode="numeric" value={p.per_day ?? ""} onChange={(e) => update(i, { per_day: Number(e.target.value) || null })} />
            </label>
          </div>
          <input
            dir="ltr"
            type="password"
            autoComplete="off"
            placeholder={status(p.id)?.has_key ? "מפתח שמור (הקלד כדי להחליף)" : "API key"}
            value={keys[p.id] ?? ""}
            onChange={(e) => setKeys({ ...keys, [p.id]: e.target.value })}
          />
          {status(p.id)?.remaining_today != null && <span className="small muted">נותרו היום: {status(p.id)!.remaining_today}</span>}
        </div>
      ))}

      <div className="row">
        {unused.map((p) => (
          <button key={p.id} className="btn" onClick={() => setState({ ...state, ai_providers: [...state.ai_providers, { ...p }] })}>
            + {p.label}
          </button>
        ))}
        <button
          className="btn"
          onClick={() => setState({ ...state, ai_providers: [...state.ai_providers, { id: `custom${state.ai_providers.length + 1}`, base_url: "", model: "", json_mode: "json_object" }] })}
        >
          + אחר
        </button>
      </div>
      <p className="small muted">המגבלות, המודלים והשכבות החינמיות משתנים; בדקו בדף של כל ספק. המפתחות נשמרים רק במחשב, לא במסד הנתונים.</p>
      <button className="btn primary" onClick={save}>
        שמור ספקים
      </button>
    </>
  );
}
