import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, imgUrl, type ListParams } from "./api";
import { DateEventEditor } from "./Editor";
import { FaceLayer } from "./FaceLayer";
import { usePhotos, useUpdatePhotos } from "./hooks";
import { gridHref, navigate, photoHref } from "./route";

export function PhotoView({ id, list }: { id: number; list: ListParams }) {
  const detail = useQuery({ queryKey: ["photo", id], queryFn: () => api.photo(id) });
  const photos = usePhotos(list);
  const update = useUpdatePhotos();
  const qc = useQueryClient();
  const items = photos.data?.items ?? [];
  const idx = items.findIndex((p) => p.id === id);
  const prev = idx > 0 ? items[idx - 1] : null;
  const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null;
  const p = detail.data;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      if (e.key === "ArrowLeft" && prev) navigate(photoHref(prev.id, list));
      else if (e.key === "ArrowRight" && next) navigate(photoHref(next.id, list));
      else if (e.key === "Escape") navigate(gridHref(list));
      else if ((e.key === "r" || e.key === "R") && !e.metaKey && !e.ctrlKey)
        update.mutate({ ids: [id], rotate_by: e.shiftKey ? 270 : 90 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next, list, id, update]);

  // Preload neighbours so arrowing through an album feels instant.
  useEffect(() => {
    for (const n of [prev, next]) if (n) new Image().src = imgUrl(n.sha256, "preview", n.rotation);
  }, [prev, next]);

  if (detail.isError) return <div className="error pad">{String(detail.error)}</div>;
  if (!p) return <div className="pad muted">Loading…</div>;

  return (
    <div className="photo-view">
      <div className="photo-view__bar">
        <a className="btn btn--ghost" href={gridHref(list)}>
          ← Back to grid
        </a>
        <span className="muted">
          {p.album_name} · #{p.album_index + 1}
          {idx >= 0 && ` · ${idx + 1} of ${items.length}`}
        </span>
        <span className="spacer" />
        <button className="btn btn--ghost" disabled={!prev} onClick={() => prev && navigate(photoHref(prev.id, list))}>
          ‹ Prev
        </button>
        <button className="btn btn--ghost" disabled={!next} onClick={() => next && navigate(photoHref(next.id, list))}>
          Next ›
        </button>
      </div>
      <div className={`photo-view__images ${p.back_sha256 ? "has-back" : ""}`}>
        <figure>
          <FaceLayer photoId={p.id} sha={p.sha256} rotation={p.rotation} alt={p.rel_path} />
          <figcaption>Front · {p.rel_path}</figcaption>
        </figure>
        {p.back_sha256 && (
          <figure>
            <img src={imgUrl(p.back_sha256, "preview", 0)} alt={p.back_rel_path ?? "back"} />
            <figcaption>Back · {p.back_rel_path}</figcaption>
          </figure>
        )}
      </div>
      <div className="photo-view__panel">
        <div className="facts">
          <div>
            <span className="facts__k">Date</span>
            <span className={`facts__v status-${p.status}`}>{p.date_label || "Undated"}</span>
            {p.date_source && <span className="muted"> ({p.date_source})</span>}
          </div>
          {p.inferred_label && (
            <div>
              <span className="facts__k">{p.status === "inferred" ? "Why" : "Estimate"}</span>
              <span className="facts__v">
                {p.status !== "inferred" && `≈${p.inferred_label} · `}
                <span className="muted">
                  {p.inferred_source}
                  {p.inferred_confidence != null && ` · ${Math.round(p.inferred_confidence * 100)}%`}
                </span>
              </span>
              {p.status === "inferred" && (
                <button
                  className="btn btn--ghost"
                  onClick={() => update.mutate({ ids: [p.id], date: p.inferred_label, date_source: "accepted" })}
                  title="Accept this estimate as the photo's date"
                >
                  Accept ≈{p.inferred_label}
                </button>
              )}
            </div>
          )}
          {p.back_transcription && (
            <div>
              <span className="facts__k">Back reads</span>
              <span className="transcription">“{p.back_transcription}”</span>
            </div>
          )}
          {p.back_date_label && (
            <div>
              <span className="facts__k">Back says</span>
              <span className="facts__v">{p.back_date_label}</span>
              <span className="muted"> ({p.back_date_source})</span>
              {p.date_label !== p.back_date_label && (
                <button
                  className="btn btn--ghost"
                  onClick={() => update.mutate({ ids: [p.id], date: p.back_date_label, date_source: "back" })}
                >
                  Use as date
                </button>
              )}
            </div>
          )}
          <div>
            <span className="facts__k">Event</span>
            <span className="facts__v">{p.event_label || "—"}</span>
          </div>
          {p.scan_datetime && (
            <div>
              <span className="facts__k">File date</span>
              <span className="facts__v muted">{p.scan_datetime} (usually the scan date)</span>
            </div>
          )}
          {p.dup_members.length > 1 && (
            <div>
              <span className="facts__k">Duplicates</span>
              <span className="facts__v">
                {p.dup_members.map((m) => (
                  <a key={m} href={photoHref(m, list)} className={m === p.id ? "is-current" : ""}>
                    #{m}
                  </a>
                ))}{" "}
                {p.is_dup_keeper ? (
                  <span className="badge badge--ok">kept for export</span>
                ) : (
                  <button
                    className="btn btn--ghost"
                    onClick={async () => {
                      await api.keeper(p.id);
                      qc.invalidateQueries();
                    }}
                  >
                    Keep this one
                  </button>
                )}
              </span>
            </div>
          )}
          {p.fixity_ok === false && (
            <div className="error">Original file changed or missing since import — re-run Import.</div>
          )}
        </div>
        <DateEventEditor ids={[p.id]} currentDate={p.date_label} currentEvent={p.event_label} />
        <CaptionEditor id={p.id} caption={p.caption ?? ""} />
      </div>
    </div>
  );
}

function CaptionEditor({ id, caption }: { id: number; caption: string }) {
  const update = useUpdatePhotos();
  return (
    <label className="field caption">
      <span className="field__label">Caption / notes (exported as description)</span>
      <textarea
        key={`${id}:${caption}`}
        defaultValue={caption}
        rows={2}
        onBlur={(e) => {
          if (e.target.value !== caption) update.mutate({ ids: [id], caption: e.target.value });
        }}
      />
    </label>
  );
}
