import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../App";
import { TYPE_LABEL } from "../format";

type Values = Record<string, unknown>;

interface Dest {
  name: string;
  address?: string;
  max_minutes?: number | string | null;
  arrive_by?: string | null;
  depart_at?: string | null;
  days?: string;
}

const DAYS: [string, string][] = [["weekdays", "ימי חול (א׳-ה׳)"], ["daily", "כל יום"], ["weekend", "סוף שבוע"]];

const LISTS: [string, string, string][] = [
  ["listing.search_cities", "ערים לחיפוש", "תל אביב, רמת גן"],
  ["areas.allowed", "רק באזורים האלה (ריק = כל העיר)", ""],
  ["areas.preferred", "אזורים מועדפים", "פלורנטין"],
  ["areas.less_preferred", "אזורים פחות מועדפים", ""],
  ["areas.excluded", "אזורים פסולים", "בת ים"],
  ["features.required", "חובה", "סלון, ממ״ד"],
  ["features.nice_to_have", "נחמד שיהיה", "מרפסת, מעלית"],
  ["features.dealbreakers", "פוסל", "קומת קרקע"],
];

const CHOICES: [string, string, [string, string][]][] = [
  ["profile.gender", "מגדר", [["male", "גבר"], ["female", "אישה"]]],
  ["budget.absolute_max_applies_to", "המקסימום חל על", [["rent", "שכירות בלבד"], ["rent_plus_known_costs", "שכירות + הוצאות ידועות"]]],
  ["broker.policy", "תיווך", [["allow", "מותר"], ["avoid", "עדיף בלי"], ["reject", "פוסל"]]],
  ["roommates.gender_preference_mismatch", "העדפת מגדר אחר אצל שותפים", [["needs_review", "לבדיקה"], ["reject", "פוסל"], ["ignore", "להתעלם"]]],
];

const NUMBERS: [string, string][] = [
  ["profile.age", "גיל"],
  ["budget.preferred_max_rent", "תקציב מועדף (₪)"],
  ["budget.absolute_max_rent", "תקציב מקסימלי (₪)"],
  ["listing.max_post_age_days", "מודעות עד גיל (ימים)"],
  ["roommates.preferred_age_min", "גיל שותפים מ-"],
  ["roommates.preferred_age_max", "עד"],
  ["roommates.max_roommates", "מקסימום שותפים"],
];

/** My search requirements from config.yaml (profile, budget, areas, ...). Saved back to the file. */
export default function SearchSettings() {
  const app = useApp();
  const [values, setValues] = useState<Values | null>(null);
  const [lists, setLists] = useState<Record<string, string>>({});
  const [types, setTypes] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    api
      .profile()
      .then((r) => {
        setValues(r.values);
        setTypes(r.kinds["listing.allowed_types"] as string[]);
        setLists(Object.fromEntries(LISTS.map(([k]) => [k, ((r.values[k] as string[]) ?? []).join(", ")])));
      })
      .catch(app.fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!values) return null;
  const set = (k: string, v: unknown) => {
    setValues({ ...values, [k]: v });
    setDirty(true);
  };

  async function save() {
    if (!values) return;
    const out: Values = { ...values };
    out["commute.destinations"] = ((values["commute.destinations"] as Dest[] | null) ?? []).filter((d) => d.name.trim());
    for (const [k] of LISTS) out[k] = (lists[k] ?? "").split(/[,\n]/).map((x) => x.trim()).filter(Boolean);
    try {
      const r = await api.saveProfile(out);
      setValues(r.values);
      setDirty(false);
      app.toast({ text: "ההגדרות נשמרו ב-config.yaml (גיבוי נשמר)" });
    } catch (e) {
      app.fail(e);
    }
  }

  const allowed = (values["listing.allowed_types"] as string[]) ?? [];
  const dests = ((values["commute.destinations"] as Dest[] | null) ?? []).map((d) => ({ ...d }));
  const setDest = (i: number, patch: Partial<Dest>) => set("commute.destinations", dests.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  return (
    <div className="form">
      <p className="muted small">אלה הדרישות שה-AI בודק. השינויים נשמרים ב-config.yaml במחשב (עם גיבוי) וחלים על ניתוחים מעכשיו.</p>
      <div className="grid2">
        {NUMBERS.map(([k, label]) => (
          <label key={k} className="field">
            <span className="label">{label}</span>
            <input inputMode="numeric" value={(values[k] as number | null) ?? ""} onChange={(e) => set(k, e.target.value === "" ? null : e.target.value)} />
          </label>
        ))}
      </div>
      {CHOICES.map(([k, label, opts]) => (
        <label key={k} className="field">
          <span className="label">{label}</span>
          <select value={values[k] as string} onChange={(e) => set(k, e.target.value)}>
            {opts.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </label>
      ))}
      <span className="label">סוגי מודעות</span>
      <div className="pills">
        {types.map((t) => (
          <button key={t} className={"pill" + (allowed.includes(t) ? " on" : "")} onClick={() => set("listing.allowed_types", allowed.includes(t) ? allowed.filter((x) => x !== t) : [...allowed, t])}>
            {TYPE_LABEL[t] ?? t}
          </button>
        ))}
      </div>
      <span className="label">יעדי נסיעה (לכל אחד זמן נסיעה בתחבורה ציבורית מכל דירה מאושרת)</span>
      {dests.map((d, i) => (
        <div key={i} className="card-box" style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--border)", borderRadius: 8 }}>
          <input dir="auto" placeholder="שם היעד, למשל: העבודה או הים" value={d.name} onChange={(e) => setDest(i, { name: e.target.value })} />
          <input dir="auto" placeholder="כתובת (לא חובה)" value={d.address ?? ""} onChange={(e) => setDest(i, { address: e.target.value })} />
          <div className="grid2">
            <label className="field">
              <span className="label">מקסימום דקות</span>
              <input inputMode="numeric" value={d.max_minutes ?? ""} onChange={(e) => setDest(i, { max_minutes: e.target.value === "" ? null : e.target.value })} />
            </label>
            <label className="field">
              <span className="label">לצאת בשעה</span>
              <input type="time" value={d.depart_at ?? ""} onChange={(e) => setDest(i, { depart_at: e.target.value || null, arrive_by: null })} />
            </label>
            <label className="field">
              <span className="label">או: להגיע עד</span>
              <input type="time" value={d.depart_at ? "" : d.arrive_by ?? ""} onChange={(e) => setDest(i, { arrive_by: e.target.value || null, depart_at: null })} />
            </label>
          </div>
          <select value={d.days ?? "weekdays"} onChange={(e) => setDest(i, { days: e.target.value })}>
            {DAYS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <button className="btn" onClick={() => set("commute.destinations", dests.filter((_, j) => j !== i))}>
            הסר יעד
          </button>
        </div>
      ))}
      <button className="btn" onClick={() => set("commute.destinations", [...dests, { name: "", days: "weekdays" }])}>
        + הוסף יעד
      </button>
      {LISTS.map(([k, label, ph]) => (
        <label key={k} className="field">
          <span className="label">{label}</span>
          <input
            dir="auto"
            placeholder={ph}
            value={lists[k] ?? ""}
            onChange={(e) => {
              setLists({ ...lists, [k]: e.target.value });
              setDirty(true);
            }}
          />
        </label>
      ))}
      <div className="grid2">
        <label className="field">
          <span className="label">כניסה החל מ-</span>
          <input type="date" value={(values["move_in.earliest"] as string) ?? ""} onChange={(e) => set("move_in.earliest", e.target.value || null)} />
        </label>
        <label className="field">
          <span className="label">כניסה עד</span>
          <input type="date" value={(values["move_in.latest"] as string) ?? ""} onChange={(e) => set("move_in.latest", e.target.value || null)} />
        </label>
      </div>
      <label className="field">
        <span className="label">עוד דברים שחשובים לי (טקסט חופשי)</span>
        <textarea rows={4} dir="auto" value={(values["free_text"] as string) ?? ""} onChange={(e) => set("free_text", e.target.value)} />
      </label>
      <button className="btn primary" disabled={!dirty} onClick={save}>
        שמור דרישות
      </button>
    </div>
  );
}
