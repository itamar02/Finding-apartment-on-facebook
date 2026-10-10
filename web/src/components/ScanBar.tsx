import { useCallback, useEffect, useRef, useState } from "react";
import { api, SchedulerState } from "../api";
import { useApp } from "../App";
import { age } from "../format";

const STEP: Record<string, string> = { scan: "סורק את הקבוצות", analyze: "מנתח מודעות חדשות", notify: "שולח התראות" };

/** "Scan now" at the top of the feed: scan, analyze (up to the limit in Settings), notify. */
export default function ScanBar({ onFinished }: { onFinished: () => void }) {
  const app = useApp();
  const [state, setState] = useState<SchedulerState | null>(null);
  const timer = useRef<number>();
  const wasRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const s = await api.scheduler();
      setState(s);
      if (wasRunning.current && !s.running) onFinished();
      wasRunning.current = s.running;
      window.clearTimeout(timer.current);
      if (s.running) timer.current = window.setTimeout(refresh, 4000);
    } catch (e) {
      app.fail(e);
    }
  }, [app, onFinished]);

  useEffect(() => {
    refresh();
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function start() {
    try {
      await api.runScheduler();
      wasRunning.current = true;
      setState((s) => (s ? { ...s, running: true, step: "scan" } : s));
      timer.current = window.setTimeout(refresh, 2500);
    } catch (e) {
      app.fail(e);
    }
  }

  if (!state) return null;
  const limit = state.settings.analyze_limit;
  return (
    <div className="scanbar">
      <button className="btn" disabled={state.running} onClick={start}>
        {state.running ? `⏳ ${STEP[state.step ?? "scan"] ?? "רץ"}…` : "🔄 סרוק עכשיו"}
      </button>
      <span className="small muted">
        {state.running
          ? "אפשר להמשיך להשתמש באפליקציה"
          : state.last_end
            ? `סריקה אחרונה ${age(state.last_end)}${state.last_result ? ` · ${state.last_result}` : ""}`
            : "עוד לא נסרק מהאפליקציה"}
        {!state.running && state.settings.analyze && ` · ניתוח AI עד ${limit || "ללא הגבלה"} מודעות`}
      </span>
    </div>
  );
}
