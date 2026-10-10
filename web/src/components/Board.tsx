import { useCallback, useEffect, useState } from "react";
import { api, Card } from "../api";
import { DEFAULT_FILTERS, useApp } from "../App";
import { age, area, money } from "../format";
import StatusPicker, { useStatusActions } from "./StatusPicker";

/** Kanban: one column per status of mine (posts without a status are not shown here). */
export default function Board() {
  const app = useApp();
  const { change } = useStatusActions();
  const [cards, setCards] = useState<Card[]>([]);
  const [moving, setMoving] = useState<Card | null>(null);
  const [hideClosed, setHideClosed] = useState(true);

  const load = useCallback(async () => {
    try {
      const keys = app.meta.statuses.map((s) => s.key);
      const r = await api.posts({ ...DEFAULT_FILTERS, decision: [], status: keys, include_old: true }, 0, 500);
      setCards(r.items);
    } catch (e) {
      app.fail(e);
    }
  }, [app]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(
    () =>
      app.onStatusChange((postId, status) =>
        setCards((prev) =>
          status ? prev.map((c) => (c.post_id === postId ? { ...c, status } : c)) : prev.filter((c) => c.post_id !== postId)
        )
      ),
    [app]
  );

  const closed = new Set(["not_relevant", "taken"]);
  const columns = app.meta.statuses.filter((s) => !(hideClosed && closed.has(s.key)));

  return (
    <div className="board-wrap">
      <label className="check board-toggle">
        <input type="checkbox" checked={hideClosed} onChange={(e) => setHideClosed(e.target.checked)} />
        הסתר ״לא רלוונטי״ ו״נתפס״
      </label>
      <div className="board">
        {columns.map((s) => {
          const col = cards.filter((c) => c.status?.status === s.key);
          return (
            <section key={s.key} className="column">
              <h3 style={{ borderColor: s.color }}>
                {s.label} <span className="muted">{col.length}</span>
              </h3>
              {col.map((c) => (
                <div key={c.post_id} className="mini-card" onClick={() => app.openPost(c.post_id)}>
                  <div className="mini-title">{[money(c.facts?.rent_ils), area(c.facts)].filter(Boolean).join(" · ") || c.group_name}</div>
                  {c.status?.note && <div className="small">📝 {c.status.note}</div>}
                  <div className="row small muted">
                    <span>{age(c.posted_at ?? c.first_seen_at)}</span>
                    <button
                      className="link"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMoving(c);
                      }}
                    >
                      העבר ←
                    </button>
                  </div>
                </div>
              ))}
            </section>
          );
        })}
      </div>
      {moving && (
        <StatusPicker
          current={moving.status}
          onClose={() => setMoving(null)}
          onPick={(st, note) => {
            change(moving.post_id, st, note);
            setMoving(null);
          }}
        />
      )}
    </div>
  );
}
