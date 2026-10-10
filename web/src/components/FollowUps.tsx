import { useEffect, useState } from "react";
import { api, FollowUp } from "../api";
import { useApp } from "../App";
import { area, money } from "../format";

/** "Needs follow-up": posts that sat too long in Contacted / Awaiting reply / Needs details. */
export default function FollowUps() {
  const app = useApp();
  const [items, setItems] = useState<FollowUp[]>([]);
  const [open, setOpen] = useState(false);

  const load = () => api.followups().then(setItems).catch(app.fail);

  useEffect(() => {
    load();
    return app.onStatusChange(() => load());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!items.length) return null;

  async function act(p: Promise<unknown>) {
    try {
      await p;
      load();
    } catch (e) {
      app.fail(e);
    }
  }

  return (
    <section className="followups">
      <button className="followups-head" onClick={() => setOpen(!open)}>
        ⏰ צריך מעקב ({items.length}) <span className="muted">{open ? "▲" : "▼"}</span>
      </button>
      {open &&
        items.map((f) => (
          <div key={f.post_id} className="followup">
            <button className="link mini-title" onClick={() => app.openPost(f.post_id)}>
              {[money(f.card?.facts?.rent_ils), area(f.card?.facts ?? null)].filter(Boolean).join(" · ") || f.card?.group_name || f.post_id}
            </button>
            <div className="small muted">
              {app.statusOf(f.status)?.label ?? f.status} · {Math.round(f.overdue_hours)} שע׳ מעבר לזמן
              {f.note ? ` · ${f.note}` : ""}
            </div>
            <div className="row">
              {f.card?.action.kind === "whatsapp" && (
                <a className="btn whatsapp" href={`https://wa.me/${f.card.action.number}`} target="_blank" rel="noopener noreferrer">
                  WhatsApp
                </a>
              )}
              <button className="btn" onClick={() => act(api.snooze(f.post_id, 24))}>
                עוד יום
              </button>
              <button className="btn" onClick={() => act(api.dismissFollowup(f.post_id))}>
                בטל תזכורת
              </button>
            </div>
          </div>
        ))}
    </section>
  );
}
