import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError, setCollection, type PhotoSummary } from "../src/api";
import { UNDO_CONFIRM_ABOVE, useUndo } from "../src/hooks";
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

/** Where the Undo pill goes: never over a control. On a photo, along the bottom edge of the photo
 *  (above the panel on a phone, centred on the photo beside it on an iPad), clear of the arrows and
 *  Accept at the top; elsewhere, in the top bar over the screen's title, clear of the ⋯ button; with
 *  no top bar (a person's page), at the very top. */
function pillPlace(): React.CSSProperties {
  const stage = document.querySelector(".t-viewer .t-stage")?.getBoundingClientRect();
  if (stage) {
    return {
      // Where the photo runs to the bottom of the screen (an iPad), clear of the home bar too.
      top: "auto", left: stage.left + stage.width / 2,
      bottom: `calc(${innerHeight - stage.bottom + 10}px + ${stage.bottom >= innerHeight - 1 ? "var(--safe-b)" : "0px"})`,
      maxWidth: stage.width - 24, transform: "translateX(-50%)",
    };
  }
  const top = document.querySelector(".t-top");
  if (top) {
    const r = top.getBoundingClientRect();
    const cs = getComputedStyle(top);
    const menu = top.querySelector(".t-top__menu")?.getBoundingClientRect();
    const from = r.left + 8, to = (menu ? menu.left : r.right) - 8;
    const mid = (r.top + parseFloat(cs.paddingTop) + r.bottom - parseFloat(cs.paddingBottom)) / 2;
    return { top: mid, left: (from + to) / 2, maxWidth: to - from, transform: "translate(-50%, -50%)" };
  }
  return { top: "calc(var(--safe-t) + 8px)", left: "50%", maxWidth: "calc(100% - 24px)", transform: "translateX(-50%)" };
}

/** After each change you make, a small "… · Undo" pill at the top for a few seconds, out of the way
 *  of the buttons you'd tap next; it goes as soon as you tap anything else. Undo is per device (the
 *  server keeps each browser's own history), so this never undoes someone else's edit. The ⋯ menu
 *  keeps Undo after the pill has gone. */
export function UndoToast() {
  const edits = useQuery({ queryKey: ["edits"], queryFn: api.edits });
  const undo = useUndo();
  const newest = edits.data?.find((e) => !e.undone);
  const seen = useRef<string | null | undefined>(undefined);
  const [shown, setShown] = useState<{ id: string; text: string; edits: number } | null>(null);
  const [note, setNote] = useState("");
  const [place, setPlace] = useState<React.CSSProperties>({});
  const pill = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!edits.data) return;
    const id = newest?.id ?? null;
    if (seen.current === undefined) {
      seen.current = id; // what was there when the app opened isn't news
      return;
    }
    if (id && id !== seen.current) setShown({ id, text: newest!.description, edits: newest!.edits ?? 0 });
    seen.current = id;
  }, [edits.data, newest]);

  useEffect(() => {
    if (!shown && !note) return;
    const t = setTimeout(() => (setShown(null), setNote("")), shown ? 4000 : 2500);
    return () => clearTimeout(t);
  }, [shown, note]);

  const visible = !!(shown || note);
  useLayoutEffect(() => {
    if (!visible) return;
    const at = () => setPlace(pillPlace());
    at();
    window.addEventListener("resize", at);
    return () => window.removeEventListener("resize", at);
  }, [visible, shown?.id]);
  // The next tap anywhere else (or a swipe) puts it away: it never waits in front of what you do next.
  useEffect(() => {
    if (!visible) return;
    const away = (e: PointerEvent) => {
      if (!pill.current?.contains(e.target as Node)) (setShown(null), setNote(""));
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [visible]);

  if (!visible) return null;
  return (
    <div className="t-toast" role="status" ref={pill} style={place}>
      <span className="t-toast__text">{note || shown?.text}</span>
      {shown && !note && (
        <button
          className="t-btn t-btn--plain t-toast__undo"
          disabled={undo.isPending}
          onClick={async () => {
            if (shown.edits > UNDO_CONFIRM_ABOVE && !window.confirm(`Undo “${shown.text}”? It changes ${shown.edits} things.`)) return;
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

/** Undo the last change made on this device, from the ⋯ menu: still there after the pill has gone. */
function MenuUndo({ onDone }: { onDone: () => void }) {
  const edits = useQuery({ queryKey: ["edits"], queryFn: api.edits });
  const undo = useUndo();
  const last = edits.data?.find((e) => !e.undone);
  const [note, setNote] = useState("");
  return (
    <>
      <button
        className="t-row"
        disabled={!last || undo.isPending}
        onClick={async () => {
          if (!last) return;
          if ((last.edits ?? 0) > UNDO_CONFIRM_ABOVE && !window.confirm(`Undo “${last.description}”? It changes ${last.edits} things.`)) return;
          try {
            const r = await undo.mutateAsync({ batchId: last.id });
            if (r.undone) return onDone();
            setNote(r.blocked ? "Can't undo: it was changed again since." : "Nothing to undo.");
          } catch (e) {
            setNote(`Couldn't undo: ${e instanceof ApiError ? e.message : String(e)}`);
          }
        }}
      >
        <span className="t-menu-undo">
          <span>Undo</span>
          <span className="t-muted t-small">{last ? last.description : "Nothing to undo"}</span>
        </span>
        <span aria-hidden>↶</span>
      </button>
      {note && <p className="t-muted t-small t-menu-undo__note">{note}</p>}
    </>
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
          <MenuUndo onDone={onClose} />
        </div>
      </section>
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
