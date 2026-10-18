import { useEffect, useRef, useState } from "react";
import { api, Group } from "../api";
import { useApp } from "../App";

const SOURCE: Record<Group["source"], string> = {
  config: "מההגדרות",
  my_groups: "הקבוצות שלי",
  search: "חיפוש",
  manual: "נוסף ידנית",
};

function members(n: number | null): string | null {
  if (n == null) return null;
  return n >= 1000 ? `${Math.round(n / 1000)}K חברים` : `${n} חברים`;
}

/** Choose which Facebook groups the scanner reads, and find new ones. */
export default function Groups() {
  const app = useApp();
  const [groups, setGroups] = useState<Group[]>([]);
  const [queries, setQueries] = useState("");
  const [filter, setFilter] = useState<"selected" | "all" | "apartments">("apartments");
  const [search, setSearch] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [status, setStatus] = useState<{ running: boolean; log: string[] } | null>(null);
  const [score, setScore] = useState(false);
  const poll = useRef<number>();

  async function load() {
    try {
      const r = await api.groups();
      setGroups(r.groups);
      setQueries(r.queries.join("\n"));
    } catch (e) {
      app.fail(e);
    }
  }

  async function checkDiscovery() {
    try {
      const s = await api.discovery();
      setStatus(s);
      if (s.running) poll.current = window.setTimeout(checkDiscovery, 4000);
      else load();
    } catch (e) {
      app.fail(e);
    }
  }

  useEffect(() => {
    load();
    checkDiscovery();
    return () => window.clearTimeout(poll.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggle(g: Group) {
    const selected = !g.selected;
    if (selected && g.is_member === 0 && !confirm("הקבוצה סומנה 'לא חבר/ה' כשנמצאה. כבר הצטרפת? הסריקה עובדת רק אם חשבון הפייסבוק של הבוט חבר בקבוצה.")) return;
    setGroups((prev) => prev.map((x) => (x.id === g.id ? { ...x, selected: selected ? 1 : 0, is_member: selected ? 1 : x.is_member } : x)));
    try {
      await api.selectGroup(g.id, selected);
    } catch (e) {
      app.fail(e);
      load();
    }
  }

  async function discover() {
    try {
      await api.saveSettings({ group_search_queries: queries.split("\n").map((q) => q.trim()).filter(Boolean) });
      await api.startDiscovery(score);
      app.toast({ text: "החיפוש התחיל. הוא רץ במחשב ולוקח כמה דקות." });
      checkDiscovery();
    } catch (e) {
      app.fail(e);
    }
  }

  async function add() {
    try {
      await api.addGroup(newUrl);
      setNewUrl("");
      load();
    } catch (e) {
      app.fail(e);
    }
  }

  const looksRelevant = (g: Group) =>
    g.selected || g.source === "search" || (g.relevance_score ?? 0) >= 5 || /דיר|שותפ|השכר|סאבלט|rent|apartment|roommate|sublet/i.test(g.name ?? "");
  const shown = groups.filter(
    (g) =>
      (filter === "all" || (filter === "selected" ? g.selected : looksRelevant(g))) &&
      (!search || (g.name ?? g.id).toLowerCase().includes(search.toLowerCase()))
  );
  const selectedCount = groups.filter((g) => g.selected).length;

  return (
    <div className="settings">
      <h2>קבוצות לסריקה</h2>
      <p className="muted small">
        {selectedCount} קבוצות מסומנות לסריקה. קבוצות שלא חבר/ה בהן לא נסרקות; ההצטרפות היא ידנית בפייסבוק.
      </p>
      <div className="pills">
        {(["apartments", "selected", "all"] as const).map((f) => (
          <button key={f} className={"pill" + (filter === f ? " on" : "")} onClick={() => setFilter(f)}>
            {f === "apartments" ? "דירות" : f === "selected" ? "מסומנות" : "הכל"}
          </button>
        ))}
      </div>
      <input placeholder="חיפוש לפי שם" value={search} onChange={(e) => setSearch(e.target.value)} />

      {shown.map((g) => (
        <label key={g.id} className="group-row">
          <input type="checkbox" checked={!!g.selected} onChange={() => toggle(g)} />
          <div className="grow">
            <div className="group-name" dir="auto">
              {g.name ?? g.id}
            </div>
            <div className="muted small">
              {[
                members(g.member_count),
                g.privacy === "private" ? "פרטית" : g.privacy === "public" ? "ציבורית" : null,
                g.is_member === 1 ? "חבר/ה" : g.is_member === 0 ? "לא חבר/ה" : null,
                SOURCE[g.source],
              ]
                .filter(Boolean)
                .join(" · ")}
              {g.relevance_score != null && <span> · התאמה {g.relevance_score}/10</span>}
            </div>
            {g.relevance_reason && <div className="small">{g.relevance_reason}</div>}
            {g.description && (
              <div className="small muted" dir="auto">
                {g.description}
              </div>
            )}
          </div>
          {g.is_member === 0 && (
            <a className="btn" href={g.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
              להצטרפות
            </a>
          )}
        </label>
      ))}

      <h2>הוספת קבוצה</h2>
      <div className="row nowrap">
        <input dir="ltr" placeholder="https://www.facebook.com/groups/..." value={newUrl} onChange={(e) => setNewUrl(e.target.value)} />
        <button className="btn" disabled={!newUrl.trim()} onClick={add}>
          הוסף
        </button>
      </div>

      <h2>מציאת קבוצות</h2>
      <p className="muted small">
        הבוט פותח במחשב את רשימת הקבוצות שלך ומחפש קבוצות לפי החיפושים כאן (שורה לכל חיפוש). הוא רק קורא, לא מצטרף לשום דבר,
        ולא רץ בזמן סריקה.
      </p>
      <textarea rows={4} value={queries} onChange={(e) => setQueries(e.target.value)} dir="auto" />
      <label className="check small">
        <input type="checkbox" checked={score} onChange={(e) => setScore(e.target.checked)} />
        לדרג התאמה עם AI (מודל זול, כמה סנטים)
      </label>
      <button className="btn primary" disabled={status?.running} onClick={discover}>
        {status?.running ? "מחפש…" : "חפש קבוצות"}
      </button>
      {status && status.log.length > 0 && (
        <pre className="log" dir="ltr">
          {status.log.join("\n")}
        </pre>
      )}
    </div>
  );
}
