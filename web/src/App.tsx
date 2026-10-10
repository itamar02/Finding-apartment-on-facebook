import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, Filters, Meta, MyStatus, NotPaired, StatusDef } from "./api";
import Pair from "./components/Pair";
import Feed from "./components/Feed";
import Board from "./components/Board";
import Detail from "./components/Detail";
import SettingsView from "./components/SettingsView";
import Groups from "./components/Groups";
import Visits from "./components/Visits";

type View = "feed" | "board" | "visits" | "groups" | "settings";

/** A chat shared to the app (Android share target): the service worker keeps it in a cache. */
async function takeShared(): Promise<{ text: string; title?: string } | null> {
  try {
    const cache = await caches.open("share-target");
    const resp = await cache.match("/shared");
    if (!resp) return null;
    await cache.delete("/shared");
    return await resp.json();
  } catch {
    return null;
  }
}

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
  const [shared, setShared] = useState<{ text: string; title?: string } | null>(null);
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

  // Notifications open the app at /#post=<id>.
  useEffect(() => {
    const fromHash = () => {
      if (location.hash === "#share") {
        history.replaceState(null, "", location.pathname);
        takeShared().then((s) => {
          if (s) {
            setShared(s);
            setView("visits");
          }
        });
        return;
      }
      const m = location.hash.match(/post=(\w+)/);
      if (m) {
        setOpenId(m[1]);
        history.replaceState(null, "", location.pathname);
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);

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
            <button className={view === "visits" ? "on" : ""} onClick={() => setView("visits")}>
              ביקורים
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
          {view === "visits" && <Visits shared={shared} onSharedUsed={() => setShared(null)} />}
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
