import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, setCollection, type PhotoSummary } from "../src/api";
import { useUndo } from "../src/hooks";

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
