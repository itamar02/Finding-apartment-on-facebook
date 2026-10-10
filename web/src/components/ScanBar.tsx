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
      setState((s) => (s ? { ...s, stopping: true } : s));
      app.toast({ text: "עוצר: מסיים את הפעולה הנוכחית ושומר" });
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(refresh, 1500);
    } catch (e) {
      app.fail(e);
    }
  }

  if (!state) return null;
  const limit = state.settings.analyze_limit;

  /** The button that started the running job turns into Stop; the others wait. */
  function jobButton(job: "download" | "analyze" | "all", label: string, disabled = false) {
    const mine = state!.running && state!.job === job;
    if (mine) {
      return (
        <button className="btn danger" disabled={state!.stopping} onClick={stop}>
          {state!.stopping ? "⏳ עוצר…" : "⏹ עצור"}
        </button>
      );
    }
    return (
      <button className="btn" disabled={state!.running || disabled} onClick={() => start(job)}>
        {label}
      </button>
    );
  }

  return (
    <div className="scanbar">
      {jobButton("download", "⬇ הורד מודעות")}
      {jobButton("analyze", "🧠 נתח מודעות", state.unchecked === 0)}
      {jobButton("all", "🔄 הכל")}
      <span className="small muted grow-line">
        {state.stopping
          ? "מסיים את הפעולה הנוכחית ושומר, בלי לפגוע במסד הנתונים…"
          : state.running
            ? `${STEP[state.step ?? "scan"] ?? "רץ"}… אפשר להמשיך להשתמש באפליקציה`
            : `${state.unchecked.toLocaleString("he-IL")} מודעות לא נבדקו · ניתוח עד ${limit || "ללא הגבלה"} בכל פעם`}
        {!state.running && state.last_end && ` · אחרון ${age(state.last_end)}${state.last_result ? `: ${state.last_result}` : ""}`}
      </span>
    </div>
  );
}
