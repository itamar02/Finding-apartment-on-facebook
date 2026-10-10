import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, Filters, Meta, MyStatus, NotPaired, StatusDef } from "./api";
import Pair from "./components/Pair";
import Feed from "./components/Feed";
import Board from "./components/Board";
import Detail from "./components/Detail";
import SettingsView from "./components/SettingsView";
import Groups from "./components/Groups";

type View = "feed" | "board" | "groups" | "settings";

interface Toast {
  text: string;
  undo?: () => void;
}

interface Ctx {
  meta: Meta;
  reloadMeta: () => Promise<void>;
  statusOf: (key: string | null | undefined) => StatusDef | undefined;
  toast: (t: Toast) => void;
  openPost: (id: string) => void;
  /** Called after any status change so every list can update its card. */
  statusChanged: (postId: string, status: MyStatus | null) => void;
  onStatusChange: (fn: (postId: string, status: MyStatus | null) => void) => () => void;
  fail: (e: unknown) => void;
}

const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => useContext(AppCtx)!;

export const DEFAULT_FILTERS: Filters = {
  decision: ["PASS", "NEEDS_REVIEW"],
  status: [],
  group: [],
  include_old: false,
  sort: "newest",
};

function loadFilters(): Filters {
  try {
    const saved = localStorage.getItem("filters");
    return saved ? { ...DEFAULT_FILTERS, ...JSON.parse(saved) } : DEFAULT_FILTERS;
  } catch {
    return DEFAULT_FILTERS;
  }
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [offline, setOffline] = useState(false);
  const [view, setView] = useState<View>("feed");
  const [filters, setFilters] = useState<Filters>(loadFilters);
  const [openId, setOpenId] = useState<string | null>(null);
  const [toastState, setToast] = useState<Toast | null>(null);
  const toastTimer = useRef<number>();
  const listeners = useRef(new Set<(postId: string, status: MyStatus | null) => void>());

  const reloadMeta = useCallback(async () => {
    try {
      setMeta(await api.meta());
      setPaired(true);
      setOffline(false);
    } catch (e) {
      if (e instanceof NotPaired) setPaired(false);
      else setOffline(true);
    }
  }, []);

  useEffect(() => {
    reloadMeta();
  }, [reloadMeta]);

  useEffect(() => {
    try {
      localStorage.setItem("filters", JSON.stringify(filters));
    } catch {
      /* private mode */
    }
  }, [filters]);

  const toast = useCallback((t: Toast) => {
    setToast(t);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), t.undo ? 6000 : 3000);
  }, []);

  const ctx = useMemo<Ctx | null>(
    () =>
      meta && {
        meta,
        reloadMeta,
        statusOf: (key) => meta.statuses.find((s) => s.key === key),
        toast,
        openPost: setOpenId,
        statusChanged: (postId, status) => listeners.current.forEach((fn) => fn(postId, status)),
        onStatusChange: (fn) => {
          listeners.current.add(fn);
          return () => listeners.current.delete(fn);
        },
        fail: (e) => {
          if (e instanceof NotPaired) setPaired(false);
          else toast({ text: `שגיאה: ${e instanceof Error ? e.message : String(e)}` });
        },
      },
    [meta, reloadMeta, toast]
  );

  if (paired === false) return <Pair onPaired={reloadMeta} />;
  if (offline)
    return (
      <div className="center-screen">
        <h2>המחשב לא זמין</h2>
        <p className="muted">האפליקציה עובדת רק כשהמחשב דולק והשרת רץ.</p>
        <button className="btn primary" onClick={reloadMeta}>
          נסה שוב
        </button>
      </div>
    );
  if (!ctx) return <div className="center-screen muted">טוען…</div>;

  return (
    <AppCtx.Provider value={ctx}>
      <div className="app">
        <header className="topbar">
          <h1>דירות</h1>
          <nav className="tabs">
            <button className={view === "feed" ? "on" : ""} onClick={() => setView("feed")}>
              פיד
            </button>
            <button className={view === "board" ? "on" : ""} onClick={() => setView("board")}>
              לוח
            </button>
            <button className={view === "groups" ? "on" : ""} onClick={() => setView("groups")}>
              קבוצות
            </button>
            <button className={view === "settings" ? "on" : ""} onClick={() => setView("settings")} aria-label="הגדרות">
              ⚙
            </button>
          </nav>
        </header>
        <main>
          {view === "feed" && <Feed filters={filters} setFilters={setFilters} />}
          {view === "board" && <Board />}
          {view === "groups" && <Groups />}
          {view === "settings" && <SettingsView onLogout={() => setPaired(false)} />}
        </main>
        {openId && <Detail postId={openId} onClose={() => setOpenId(null)} />}
        {toastState && (
          <div className="toast" role="status">
            <span>{toastState.text}</span>
            {toastState.undo && (
              <button
                onClick={() => {
                  toastState.undo!();
                  setToast(null);
                }}
              >
                בטל
              </button>
            )}
          </div>
        )}
      </div>
    </AppCtx.Provider>
  );
}
