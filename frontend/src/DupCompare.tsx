import { useEffect } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { api, imgUrl, type ListParams } from "./api";
import { navigate, photoHref } from "./route";

/** Duplicate group side by side: each photo with its file facts, and which to keep for export. */
export function DupCompare({ ids, list, onClose }: { ids: number[]; list: ListParams; onClose: () => void }) {
  const qc = useQueryClient();
  const details = useQueries({ queries: ids.map((id) => ({ queryKey: ["photo", id], queryFn: () => api.photo(id) })) });
  const photos = details.map((d) => d.data).filter((p) => p !== undefined);
  const keepers = photos.filter((p) => p.is_dup_keeper).length;
  const allKept = photos.length === ids.length && keepers === ids.length;

  // While open, keys belong to the overlay: Escape closes it (not the photo view behind it, which
  // would go back to the grid), and R / arrows don't rotate or step the photo underneath.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.key === "Tab") return;
      e.stopImmediatePropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const act = async (call: Promise<unknown>) => {
    await call;
    qc.invalidateQueries();
  };

  return (
    <div className="compare" role="dialog" aria-modal="true" aria-label="Compare duplicates">
      <div className="compare__bar">
        <strong>Possible duplicates · {ids.length} photos</strong>
        <span className="muted">
          {allKept ? "All are kept for export." : keepers === 1 ? "One is kept for export." : "Export keeps the first one unless you pick."}
        </span>
        <span className="compare__actions">
          {!allKept && (
            <button className="btn" onClick={() => act(api.keepAll(ids[0]))}>
              {ids.length === 2 ? "Keep both" : `Keep all ${ids.length}`}
            </button>
          )}
          <button
            className="btn btn--ghost"
            title="These aren't the same picture: split them up (Import keeps them apart; Undo puts them back)"
            onClick={() => act(api.notDuplicates(ids[0]))}
          >
            Not duplicates
          </button>
          <button className="btn btn--ghost" onClick={onClose} autoFocus>
            Close
          </button>
        </span>
      </div>
      <div className="compare__grid" style={{ "--n": ids.length } as React.CSSProperties}>
        {ids.map((id, i) => {
          const p = details[i].data;
          if (!p) return <figure key={id} className="compare__item muted">Loading…</figure>;
          const slash = p.rel_path.lastIndexOf("/");
          return (
            <figure key={id} className={`compare__item ${p.is_dup_keeper ? "is-kept" : ""}`}>
              <img src={imgUrl(p.sha256, "preview", p.rotation)} alt={p.rel_path} />
              <figcaption>
                <div className="compare__name" title={p.rel_path}>
                  {p.rel_path.slice(slash + 1)}
                </div>
                <div className="muted compare__folder">{slash > 0 ? p.rel_path.slice(0, slash) : "(top folder)"}</div>
                <dl className="compare__facts">
                  <dt>Size</dt>
                  <dd>
                    {p.width && p.height ? `${p.width} × ${p.height}` : "—"} · {fileSize(p.file_size)}
                  </dd>
                  <dt>Date</dt>
                  <dd>{p.date_label || "Undated"}</dd>
                  <dt>Back</dt>
                  <dd>{p.back_rel_path ? "scanned" : "—"}</dd>
                </dl>
                <div className="compare__buttons">
                  {p.is_dup_keeper ? (
                    <span className="badge badge--ok">kept for export</span>
                  ) : null}
                  {!(p.is_dup_keeper && keepers === 1) && (
                    <button className="btn btn--ghost" onClick={() => act(api.keeper(p.id))}>
                      {keepers > 0 ? "Keep only this one" : "Keep this one"}
                    </button>
                  )}
                  {ids.length > 2 && (
                    <button className="btn btn--ghost" title="Take just this photo out of the group" onClick={() => act(api.notDuplicates(p.id, true))}>
                      Not a duplicate
                    </button>
                  )}
                  <button
                    className="btn btn--ghost"
                    onClick={() => {
                      onClose();
                      navigate(photoHref(p.id, list));
                    }}
                  >
                    Open
                  </button>
                </div>
              </figcaption>
            </figure>
          );
        })}
      </div>
    </div>
  );
}

function fileSize(bytes: number): string {
  if (!bytes) return "—";
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
