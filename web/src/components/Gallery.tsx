import { useState } from "react";
import type { Photos } from "../api";

/** A swipeable strip of photos; tap one to see it full screen. */
export default function Gallery({ photos }: { photos: Photos }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <div className="gallery">
        {photos.items.map((p, i) => (
          <img key={p.idx} src={p.src} loading="lazy" alt="" onClick={() => setOpen(i)} referrerPolicy="no-referrer" />
        ))}
        {photos.more_on_facebook > 0 && <span className="more-photos">+{photos.more_on_facebook} בפייסבוק</span>}
      </div>
      {open !== null && (
        <div className="viewer" onClick={() => setOpen(null)}>
          <div className="viewer-strip" onClick={(e) => e.stopPropagation()}>
            {photos.items.map((p) => (
              <img key={p.idx} src={p.src} alt="" referrerPolicy="no-referrer" />
            ))}
          </div>
          <button className="viewer-close" onClick={() => setOpen(null)} aria-label="סגור">
            ✕
          </button>
        </div>
      )}
    </>
  );
}
