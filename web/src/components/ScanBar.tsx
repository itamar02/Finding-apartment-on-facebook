import { useCallback, useEffect, useRef, useState } from "react";
import { api, SchedulerState } from "../api";
import { useApp } from "../App";
import { age } from "../format";

const STEP: Record<string, string> = { scan: "מוריד מודעות מפייסבוק", analyze: "מנתח מודעות עם AI", notify: "שולח התראות" };

/** The jobs: download posts (Facebook only, free), analyze them with the AI (costs), or both; and Stop. */
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

  async function start(job: "download" | "analyze" | "all") {
    if (job !== "download") {
      const n = Math.min(state?.unchecked ?? 0, state?.settings.analyze_limit || Infinity);
      if (!confirm(`לנתח עד ${n.toLocaleString("he-IL")} מודעות עם AI? זה עולה כסף (בערך ${(n * 0.002).toFixed(2)}$).`)) return;
    }
    try {
      await api.runScheduler(job);
      wasRunning.current = true;
      setState((s) => (s ? { ...s, running: true, job, step: job === "analyze" ? "analyze" : "scan" } : s));
      timer.current = window.setTimeout(refresh, 2500);
    } catch (e) {
      app.fail(e);
    }
  }

  async function stop() {
    try {
      await api.stopScheduler();
      app.toast({ text: "עוצר…" });
      timer.current = window.setTimeout(refresh, 1500);
    } catch (e) {
      app.fail(e);
    }
  }

  if (!state) return null;
  const limit = state.settings.analyze_limit;
  return (
    <div className="scanbar">
      {state.running ? (
        <>
          <span>⏳ {STEP[state.step ?? "scan"] ?? "רץ"}…</span>
          <button className="btn danger" onClick={stop}>
            ⏹ עצור
          </button>
        </>
      ) : (
        <>
          <button className="btn" onClick={() => start("download")}>
            ⬇ הורד מודעות
          </button>
          <button className="btn" onClick={() => start("analyze")} disabled={state.unchecked === 0}>
            🧠 נתח מודעות
          </button>
          <button className="btn" onClick={() => start("all")}>
            🔄 הכל
          </button>
        </>
      )}
      <span className="small muted grow-line">
        {state.running
          ? "אפשר להמשיך להשתמש באפליקציה"
          : `${state.unchecked.toLocaleString("he-IL")} מודעות לא נבדקו · ניתוח עד ${limit || "ללא הגבלה"} בכל פעם`}
        {!state.running && state.last_end && ` · אחרון ${age(state.last_end)}${state.last_result ? `: ${state.last_result}` : ""}`}
      </span>
    </div>
  );
}
