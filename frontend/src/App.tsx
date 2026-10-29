import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { Grid } from "./Grid";
import { Jobs } from "./Jobs";
import { LooseBacks } from "./LooseBacks";
import { PeoplePage, PersonPage } from "./People";
import { PhotoView } from "./PhotoView";
import { useStats, useUndo } from "./hooks";
import { useRoute } from "./route";

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
    const r = await undo.mutateAsync({ skip: blocked !== null && blocked === lastUndoable?.id });
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
      if ((e.metaKey || e.ctrlKey) && e.key === "z" && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault();
        doUndo();
      }
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
          className="btn btn--ghost"
          onClick={doUndo}
          disabled={!lastUndoable || undo.isPending}
          title={lastUndoable ? `Undo: ${lastUndoable.description} (⌘Z)` : "Nothing to undo"}
        >
          ↶ Undo{lastUndoable ? `: ${lastUndoable.description}` : ""}
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
