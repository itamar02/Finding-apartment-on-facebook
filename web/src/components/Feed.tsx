import { useCallback, useEffect, useState } from "react";
import { api, Card, Decision, Filters } from "../api";
import { DEFAULT_FILTERS, useApp } from "../App";
import { DECISION_LABEL, SOURCE_LABEL } from "../format";
import PostCard from "./PostCard";
import FollowUps from "./FollowUps";

const PAGE = 30;

export default function Feed({ filters, setFilters }: { filters: Filters; setFilters: (f: Filters) => void }) {
  const app = useApp();
  const [items, setItems] = useState<Card[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [showFilters, setShowFilters] = useState(false);

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      try {
        const r = await api.posts(filters, offset, PAGE);
        setTotal(r.total);
        setItems((prev) => (offset === 0 ? r.items : [...prev, ...r.items]));
      } catch (e) {
        app.fail(e);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filters]
  );

  useEffect(() => {
    load(0);
  }, [load]);

  useEffect(
    () =>
      app.onStatusChange((postId, status) =>
        setItems((prev) => prev.map((c) => (c.post_id === postId ? { ...c, status } : c)))
      ),
    [app]
  );

  const active = countActive(filters);

  return (
    <div className="feed">
      <div className="feed-bar">
        <button className="btn" onClick={() => setShowFilters(true)}>
          סינון{active ? ` (${active})` : ""}
        </button>
        <select value={filters.sort} onChange={(e) => setFilters({ ...filters, sort: e.target.value as Filters["sort"] })}>
          <option value="newest">החדשים קודם</option>
          <option value="price_asc">מחיר: מהזול</option>
          <option value="price_desc">מחיר: מהיקר</option>
        </select>
        <span className="muted small grow-end">{total} מודעות</span>
      </div>

      <FollowUps />
      {items.map((c) => (
        <PostCard key={c.post_id} card={c} />
      ))}
      {!loading && items.length === 0 && <p className="center muted">אין מודעות שמתאימות לסינון.</p>}
      {items.length < total && (
        <button className="btn wide" disabled={loading} onClick={() => load(items.length)}>
          {loading ? "טוען…" : "עוד מודעות"}
        </button>
      )}
      {loading && items.length === 0 && <p className="center muted">טוען…</p>}

      {showFilters && <FilterSheet filters={filters} onApply={setFilters} onClose={() => setShowFilters(false)} />}
    </div>
  );
}

function countActive(f: Filters): number {
  let n = 0;
  if (f.decision.join() !== DEFAULT_FILTERS.decision.join()) n++;
  if (f.status.length) n++;
  if (f.group.length) n++;
  if (f.price_min != null || f.price_max != null) n++;
  if (f.area) n++;
  if (f.since || f.include_old) n++;
  if (f.has_phone != null) n++;
  if (f.source) n++;
  return n;
}

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
}

function FilterSheet({ filters, onApply, onClose }: { filters: Filters; onApply: (f: Filters) => void; onClose: () => void }) {
  const { meta } = useApp();
  const [f, setF] = useState<Filters>(filters);
  const num = (s: string) => (s.trim() === "" ? undefined : Number(s.replace(/\D/g, "")));

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet tall" onClick={(e) => e.stopPropagation()}>
        <h3>סינון</h3>

        <label className="label">החלטת AI</label>
        <div className="pills">
          {meta.decisions.map((d: Decision) => (
            <button key={d} className={"pill" + (f.decision.includes(d) ? " on" : "")} onClick={() => setF({ ...f, decision: toggle(f.decision, d) })}>
              {DECISION_LABEL[d]}
            </button>
          ))}
        </div>

        <label className="label">הסטטוס שלי</label>
        <div className="pills">
          <button className={"pill" + (f.status.includes("none") ? " on" : "")} onClick={() => setF({ ...f, status: toggle(f.status, "none") })}>
            ללא סטטוס
          </button>
          {meta.statuses.map((s) => (
            <button key={s.key} className={"pill" + (f.status.includes(s.key) ? " on" : "")} onClick={() => setF({ ...f, status: toggle(f.status, s.key) })}>
              {s.label}
            </button>
          ))}
        </div>

        <label className="label">מי קבע את הסטטוס</label>
        <div className="pills">
          {["", "human", "gpt", "system"].map((s) => (
            <button key={s} className={"pill" + ((f.source ?? "") === s ? " on" : "")} onClick={() => setF({ ...f, source: s || undefined })}>
              {s ? SOURCE_LABEL[s] : "הכל"}
            </button>
          ))}
        </div>

        <label className="label">קבוצה</label>
        <div className="pills">
          {meta.groups.map((g) => (
            <button key={g.id} className={"pill" + (f.group.includes(g.id) ? " on" : "")} onClick={() => setF({ ...f, group: toggle(f.group, g.id) })}>
              {g.name}
            </button>
          ))}
        </div>

        <label className="label">מחיר (₪)</label>
        <div className="row">
          <input inputMode="numeric" placeholder="מ-" value={f.price_min ?? ""} onChange={(e) => setF({ ...f, price_min: num(e.target.value) })} />
          <input inputMode="numeric" placeholder="עד" value={f.price_max ?? ""} onChange={(e) => setF({ ...f, price_max: num(e.target.value) })} />
        </div>

        <label className="label">אזור</label>
        <input placeholder="עיר, שכונה או רחוב" value={f.area ?? ""} onChange={(e) => setF({ ...f, area: e.target.value || undefined })} />

        <label className="label">תאריך פרסום</label>
        <div className="row">
          <input type="date" value={f.since?.slice(0, 10) ?? ""} onChange={(e) => setF({ ...f, since: e.target.value || undefined })} />
          <label className="check">
            <input type="checkbox" checked={f.include_old} onChange={(e) => setF({ ...f, include_old: e.target.checked })} />
            כולל ישנות (מעל {meta.recent_days} ימים)
          </label>
        </div>

        <label className="label">טלפון</label>
        <div className="pills">
          {[undefined, true, false].map((v) => (
            <button key={String(v)} className={"pill" + (f.has_phone === v ? " on" : "")} onClick={() => setF({ ...f, has_phone: v })}>
              {v === undefined ? "הכל" : v ? "יש טלפון" : "אין טלפון"}
            </button>
          ))}
        </div>

        <div className="row sticky-actions">
          <button
            className="btn primary"
            onClick={() => {
              onApply(f);
              onClose();
            }}
          >
            הצג
          </button>
          <button className="btn" onClick={() => setF(DEFAULT_FILTERS)}>
            איפוס
          </button>
        </div>
      </div>
    </div>
  );
}
