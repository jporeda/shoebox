import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import { Grid } from "./Grid";
import { Jobs } from "./Jobs";
import { LooseBacks } from "./LooseBacks";
import { PeoplePage, PersonPage } from "./People";
import { PhotoView } from "./PhotoView";
import { useStats, useUndo } from "./hooks";
import { isTypingTarget } from "./keys";
import { useRoute } from "./route";

/** Undoing more changes than this asks for confirmation first. */
const UNDO_CONFIRM_ABOVE = 20;

export function App() {
  const route = useRoute();
  const stats = useStats();
  const undo = useUndo();
  const [toast, setToast] = useState("");
  const edits = useQuery({ queryKey: ["edits"], queryFn: api.edits });
  const lastUndoable = edits.data?.find((e) => !e.undone);

  // A batch whose changes were all changed again can't be undone; a second Undo skips it.
  const [blocked, setBlocked] = useState<string | null>(null);

  const doUndo = async () => {
    // Skip names the blocked batch, so the server skips exactly that one (not whatever is newest now).
    const skip = blocked !== null && blocked === lastUndoable?.id;
    // A big undo (one that reverts more than about 20 changes) asks first; covers the button and ⌘Z.
    const n = lastUndoable?.edits ?? 0;
    if (!skip && lastUndoable && n > UNDO_CONFIRM_ABOVE && !window.confirm(`Undo “${lastUndoable.description}”? It changes ${n} items.`))
      return;
    let r;
    try {
      // Undo exactly the batch shown (and confirmed); the server checks it's this device's.
      r = await undo.mutateAsync(skip ? { batchId: blocked, skip: true } : { batchId: lastUndoable?.id });
    } catch (e) {
      setToast(`Couldn't undo: ${e instanceof ApiError ? e.message : String(e)}`);
      return;
    }
    setBlocked(r.blocked ? (r.batch_id ?? null) : null);
    setToast(
      r.blocked
        ? `Can't undo “${r.description}”: it was changed again since. Undo again to skip it.`
        : r.skipped
          ? `Skipped “${r.description}”`
          : r.undone
            ? `Undid “${r.description}”${r.conflicts.length ? ` (${r.conflicts.length} later change(s) kept)` : ""}`
            : "Nothing to undo",
    );
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key !== "z" || isTypingTarget(e)) return;
      e.preventDefault();
      // Holding ⌘Z, or pressing it again before the last undo answers, must not undo twice
      // (the Undo button is disabled while pending for the same reason).
      if (e.repeat || undo.isPending) return;
      doUndo();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const s = stats.data;
  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          Shoebox
        </a>
        <nav>
          <a className={route.view === "grid" || route.view === "photo" ? "is-active" : ""} href="#/">
            Photos
          </a>
          <a className={route.view === "people" || route.view === "person" ? "is-active" : ""} href="#/people">
            People
          </a>
          <a className={route.view === "backs" ? "is-active" : ""} href="#/backs?undated=1">
            Backs
          </a>
          <a className={route.view === "jobs" ? "is-active" : ""} href="#/jobs">
            Jobs
          </a>
        </nav>
        {s && (
          <span className="stats muted">
            {s.photos} photos · <b className="status-reviewed">{s.reviewed}</b> confirmed ·{" "}
            <b className="status-inferred">{s.inferred}</b> estimated · <b className="status-untagged">{s.untagged}</b> undated
          </span>
        )}
        <span className="spacer" />
        <button
          className="btn btn--ghost topbar__undo"
          onClick={doUndo}
          disabled={!lastUndoable || undo.isPending}
          title={lastUndoable ? `Undo: ${lastUndoable.description} (⌘Z)` : "Nothing to undo"}
          aria-label={lastUndoable ? `Undo: ${lastUndoable.description}` : "Undo"}
        >
          ↶<span className="topbar__undo-word"> Undo</span>
          {lastUndoable && <span className="topbar__undo-label">: {lastUndoable.description}</span>}
        </button>
      </header>
      {route.view === "grid" && <Grid list={route.list} />}
      {route.view === "photo" && <PhotoView id={route.id} list={route.list} />}
      {route.view === "jobs" && <Jobs />}
      {route.view === "people" && <PeoplePage tab={route.tab} />}
      {route.view === "person" && <PersonPage id={route.id} />}
      {route.view === "backs" && <LooseBacks kind={route.kind} albumId={route.album_id} undated={route.undated} />}
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
