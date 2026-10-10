import { useState } from "react";
import { api } from "../api";

export default function Pair({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.pair(code.replace(/\D/g, ""));
      onPaired();
    } catch {
      setError("הקוד שגוי או שפג תוקפו. צור קוד חדש במחשב.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="center-screen" onSubmit={submit}>
      <h2>חיבור לאפליקציה</h2>
      <p className="muted">
        במחשב הרץ: <code dir="ltr">python app_server.py --pair</code>
        <br />
        והקלד כאן את הקוד בן 8 הספרות.
      </p>
      <input
        className="code-input"
        inputMode="numeric"
        autoComplete="one-time-code"
        dir="ltr"
        maxLength={9}
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder="12345678"
      />
      {error && <p className="error">{error}</p>}
      <button className="btn primary" disabled={busy || code.replace(/\D/g, "").length !== 8}>
        התחבר
      </button>
    </form>
  );
}
