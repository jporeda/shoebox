import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, setCollection, type PhotoSummary } from "../src/api";
import { useUndo } from "../src/hooks";
import { useNewGrid } from "../src/GridNew";

/** A panel sliding up from the bottom over a dimmed screen; tap outside or "Done" to close. */
export function Sheet({ open, onClose, title, children, full }: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  /** Nearly full height (filters), instead of as tall as its content. */
  full?: boolean;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="t-sheet-wrap" onClick={onClose}>
      <div className={`t-sheet ${full ? "t-sheet--full" : ""}`} role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="t-sheet__head">
          <span className="t-sheet__title">{title}</span>
          <button className="t-btn t-btn--plain" onClick={onClose}>
            Done
          </button>
        </div>
        <div className="t-sheet__body">{children}</div>
      </div>
    </div>
  );
}

/** Undated / estimated / confirmed, as a small coloured dot. */
export function StatusDot({ status }: { status: PhotoSummary["status"] }) {
  const label = status === "reviewed" ? "Dated" : status === "inferred" ? "Estimated date" : "No date yet";
  return <span className={`t-dot t-dot--${status}`} role="img" aria-label={label} title={label} />;
}

/** After each change you make, a few seconds of "… · Undo" at the bottom. Undo is per device
 *  (the server keeps each browser's own history), so this never undoes someone else's edit. */
export function UndoToast() {
  const edits = useQuery({ queryKey: ["edits"], queryFn: api.edits });
  const undo = useUndo();
  const newest = edits.data?.find((e) => !e.undone);
  const seen = useRef<string | null | undefined>(undefined);
  const [shown, setShown] = useState<{ id: string; text: string } | null>(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!edits.data) return;
    const id = newest?.id ?? null;
    if (seen.current === undefined) {
      seen.current = id; // what was there when the app opened isn't news
      return;
    }
    if (id && id !== seen.current) setShown({ id, text: newest!.description });
    seen.current = id;
  }, [edits.data, newest]);

  useEffect(() => {
    if (!shown && !note) return;
    const t = setTimeout(() => (setShown(null), setNote("")), shown ? 6000 : 3000);
    return () => clearTimeout(t);
  }, [shown, note]);

  if (!shown && !note) return null;
  return (
    <div className="t-toast" role="status">
      <span className="t-toast__text">{note || shown?.text}</span>
      {shown && !note && (
        <button
          className="t-btn t-btn--plain t-toast__undo"
          disabled={undo.isPending}
          onClick={async () => {
            try {
              const r = await undo.mutateAsync({ batchId: shown.id });
              seen.current = undefined; // the list changes under us; start counting again
              setShown(null);
              setNote(r.undone ? "Undone" : r.blocked ? "Can't undo: changed again since" : "Nothing to undo");
            } catch (e) {
              setNote(`Couldn't undo: ${e instanceof ApiError ? e.message : String(e)}`);
            }
          }}
        >
          Undo
        </button>
      )}
    </div>
  );
}

/** The ⋯ menu: which collection, reload. Nothing here points at the desktop site: people using this
 *  app never need it. */
export function Menu({ open, onClose }: { open: boolean; onClose: () => void }) {
  const collections = useQuery({ queryKey: ["collections"], queryFn: api.collections });
  const look = useNewGrid(true);
  return (
    <Sheet open={open} onClose={onClose} title="Shoebox">
      {(collections.data?.length ?? 0) > 1 && (
        <section className="t-section">
          <h3 className="t-h3">Collection</h3>
          <div className="t-list">
            {collections.data!.map((c) => (
              <button
                key={c.id}
                className={`t-row ${c.current ? "is-on" : ""}`}
                onClick={() => {
                  setCollection(c.id);
                  location.hash = "#/";
                  location.reload();
                }}
              >
                <span>{c.name}</span>
                {c.current && <span aria-hidden>✓</span>}
              </button>
            ))}
          </div>
        </section>
      )}
      <section className="t-section">
        <div className="t-list">
          <label className="t-row">
            <span>New look</span>
            <input type="checkbox" className="t-switch" checked={look.on} onChange={look.toggle} />
          </label>
          <button className="t-row" onClick={() => location.reload()}>
            <span>Reload</span>
          </button>
        </div>
      </section>
    </Sheet>
  );
}

/** "1 photo", "3 photos". */
export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** A finished list: a big check, a warm title and what happens next. */
export function Done({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="t-done" role="status">
      <span className="t-done__mark" aria-hidden>✓</span>
      <h2 className="t-done__title">{title}</h2>
      {children && <p className="t-done__text">{children}</p>}
      {action}
    </div>
  );
}

const WELCOMED = "shoebox.welcomed";

/** Shown once on each phone or tablet: what this is, and the one obvious place to start. */
export function Welcome({ onStart }: { onStart: () => void }) {
  const [open, setOpen] = useState(() => {
    try {
      return !localStorage.getItem(WELCOMED);
    } catch {
      return false;
    }
  });
  const close = () => {
    try {
      localStorage.setItem(WELCOMED, "1");
    } catch {
      // private window: it shows again next time, which is harmless
    }
    setOpen(false);
  };
  return (
    <Sheet open={open} onClose={close} title="Welcome">
      <div className="t-welcome">
        <h2 className="t-welcome__title">The family photos</h2>
        <p>
          Help put dates and names on our old photos. Pick a year, say who someone is, or tap a face that’s wrong. Every
          change can be undone.
        </p>
        <button className="t-btn t-btn--primary t-btn--big" onClick={() => (close(), onStart())}>
          Start with dates
        </button>
        <button className="t-btn t-btn--plain t-btn--block" onClick={close}>
          Look around first
        </button>
      </div>
    </Sheet>
  );
}

/** The photos can't be reached: the Mac that holds them is asleep or this device is on other Wi-Fi.
 *  `stale`: something is on screen already, so a slim banner instead of the whole screen. */
export function CantReach({ stale, onRetry, busy, waiting = 0 }: { stale: boolean; onRetry: () => void; busy: boolean; waiting?: number }) {
  if (stale)
    return (
      <div className="t-offline-banner" role="alert">
        <span>
          Can’t reach the photos right now.
          {waiting > 0 && ` ${waiting === 1 ? "1 change is" : `${waiting} changes are`} kept and will save then.`}
        </span>
        <button className="t-btn t-btn--plain" disabled={busy} onClick={onRetry}>
          Try again
        </button>
      </div>
    );
  return (
    <div className="t-offline" role="alert">
      <h2 className="t-done__title">Can’t reach the family photos</h2>
      <p className="t-done__text">
        The computer that keeps them may be asleep, or this phone may be on a different Wi-Fi. Check you’re on the home
        Wi-Fi, then try again.
      </p>
      <button className="t-btn t-btn--primary t-btn--big" disabled={busy} onClick={onRetry}>
        {busy ? "Trying…" : "Try again"}
      </button>
    </div>
  );
}

/** While a screen loads: soft shapes where its content will be, instead of the word "Loading…". */
export function Placeholder({ kind }: { kind: "grid" | "cards" | "queue" | "lines" }) {
  const n = (count: number) => Array.from({ length: count }, (_, i) => i);
  return (
    <div className={`t-ph t-ph--${kind}`} role="status" aria-label="Loading">
      {kind === "grid" && n(12).map((i) => <span key={i} className="t-ph__block" />)}
      {kind === "cards" &&
        n(2).map((i) => (
          <div key={i} className="t-ph__card">
            <span className="t-ph__line t-ph__line--title" />
            <div className="t-ph__faces">{n(4).map((j) => <span key={j} className="t-ph__block" />)}</div>
            <span className="t-ph__line t-ph__line--button" />
          </div>
        ))}
      {kind === "queue" && (
        <div className="t-ph__card">
          <span className="t-ph__block t-ph__block--photo" />
          <span className="t-ph__line t-ph__line--button" />
          <span className="t-ph__line" />
        </div>
      )}
      {kind === "lines" && n(3).map((i) => <span key={i} className="t-ph__line" />)}
    </div>
  );
}
