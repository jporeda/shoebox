import { useEffect, useRef, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { albumLabel, api, imgUrl, type ListParams, type NearbyDate } from "./api";
import { DupCompare } from "./DupCompare";
import { DateEventEditor, PlaceEditor } from "./Editor";
import { AcceptGuesses, FaceLayer } from "./FaceLayer";
import { useEventInstances, usePhotos, useSaveOnLeave, useUpdatePhotos } from "./hooks";
import { isTypingTarget } from "./keys";
import { gridHref, navigate, navigateReplace, photoHref, rememberPhoto } from "./route";

export function PhotoView({ id, list }: { id: number; list: ListParams }) {
  const detail = useQuery({ queryKey: ["photo", id], queryFn: () => api.photo(id) });
  const photos = usePhotos(list);
  const update = useUpdatePhotos();
  const items = photos.data?.items ?? [];
  const idx = items.findIndex((p) => p.id === id);
  const prev = idx > 0 ? items[idx - 1] : null;
  const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null;
  const p = detail.data;
  const instances = useEventInstances();
  const instance = instances.data?.find((i) => i.id === p?.event_instance_id);

  // Stepping between photos replaces the history entry, so Back returns to the grid (which then
  // scrolls to the last photo viewed) instead of walking back through every photo.
  const step = (to: { id: number } | null) => to && navigateReplace(photoHref(to.id, list));
  useEffect(() => rememberPhoto(list, id), [list, id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      if (e.key === "ArrowLeft" && prev) step(prev);
      else if (e.key === "ArrowRight" && next) step(next);
      else if (e.key === "Escape") navigate(backHref(list));
      // Holding R would otherwise fire a rotate per auto-repeat.
      else if ((e.key === "r" || e.key === "R") && !e.metaKey && !e.ctrlKey && !e.repeat)
        update.mutate({ ids: [id], rotate_by: e.shiftKey ? 270 : 90 });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prev, next, list, id, update]);

  // Swipe left / right on the photo for next / previous (finger only; not while drawing a face box
  // or while the page is pinch-zoomed, when a sideways drag should pan instead).
  const zoomed = usePinchZoomed();
  const swipe = useRef<{ x: number; y: number; t: number } | null>(null);
  const onSwipeDown = (e: React.PointerEvent) => {
    if (e.pointerType === "mouse") return;
    // Only the photo itself starts a swipe: not the face tools, slider, face list or face sheet,
    // which sit inside this element (the sheet only looks separate on a phone).
    const t = e.target as Element;
    const onPhoto = t.closest(".photo-frame") || t.matches(".photo-view__images > figure > img");
    if (!e.isPrimary || zoomed || !onPhoto || t.closest(".photo-frame.is-drawing")) {
      swipe.current = null; // a second finger (pinch) cancels the swipe
      return;
    }
    swipe.current = { x: e.clientX, y: e.clientY, t: e.timeStamp };
  };
  const onSwipeUp = (e: React.PointerEvent) => {
    const s = swipe.current;
    swipe.current = null;
    if (!s || !e.isPrimary) return;
    const dx = e.clientX - s.x;
    const dy = e.clientY - s.y;
    if (Math.abs(dx) < 60 || Math.abs(dx) < 1.5 * Math.abs(dy) || e.timeStamp - s.t > 800) return;
    step(dx < 0 ? next : prev);
  };

  // Preload neighbours so arrowing through an album feels instant.
  useEffect(() => {
    for (const n of [prev, next]) if (n) new Image().src = imgUrl(n.sha256, "preview", n.rotation);
  }, [prev, next]);

  if (detail.isError) return <div className="error pad">{String(detail.error)}</div>;
  if (!p) return <div className="pad muted">Loading…</div>;

  return (
    <div className={`photo-view ${zoomed ? "is-zoomed" : ""}`}>
      <div className="photo-view__bar">
        <a className="btn btn--ghost photo-view__back" href={backHref(list)}>
          ← Back<span className="photo-view__back-more"> to {list.ids ? "Check" : "grid"}</span>
        </a>
        <span className="muted photo-view__where">
          {albumLabel({ name: p.album_name, rel_path: p.rel_path.split("/").slice(0, -1).join("/") })} · #{p.album_index + 1}
          {idx >= 0 && ` · ${idx + 1} of ${items.length}`}
        </span>
        <span className="spacer" />
        <AcceptGuesses photoId={p.id} rotation={p.rotation} />
        <button className="btn btn--ghost photo-view__step" disabled={!prev} onClick={() => step(prev)}>
          ‹ Prev
        </button>
        <button className="btn btn--ghost photo-view__step" disabled={!next} onClick={() => step(next)}>
          Next ›
        </button>
      </div>
      <SourceFile rel={p.rel_path} folder={p.source_folder ?? null} albumId={p.album_id}>
        <span className="photo-view__rotate" role="group" aria-label="Rotate photo">
          <button className="btn btn--ghost" disabled={update.isPending} onClick={() => update.mutate({ ids: [id], rotate_by: 270 })} title="Rotate left (Shift+R)">
            ⟲ Left
          </button>
          <button className="btn btn--ghost" disabled={update.isPending} onClick={() => update.mutate({ ids: [id], rotate_by: 90 })} title="Rotate right (R)">
            ⟳ Right
          </button>
        </span>
      </SourceFile>
      <div
        className={`photo-view__images ${p.back_sha256 ? "has-back" : ""}`}
        onPointerDown={onSwipeDown}
        onPointerUp={onSwipeUp}
        onPointerCancel={() => (swipe.current = null)}
      >
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
            {p.date_source && DATE_SOURCE[p.date_source] && <span className="muted"> ({DATE_SOURCE[p.date_source]})</span>}
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
          {p.status === "inferred" && p.nearby_dates.length > 0 && (
            <NearbyDateChoices
              nearby={p.nearby_dates}
              busy={update.isPending}
              onPick={(date) => update.mutate({ ids: [p.id], date, date_source: "accepted" })}
            />
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
          {p.dup_members.length > 1 && <DuplicatesRow id={p.id} members={p.dup_members} list={list} />}
          {p.fixity_ok === false && (
            <div className="error">Original file changed or missing since import — re-run Import.</div>
          )}
        </div>
        <DateEventEditor
          ids={[p.id]}
          currentDate={p.date_label}
          currentEvent={p.event_label}
          datePicker
          currentExactDay={/^\d{4}-\d{2}-\d{2}$/.test(p.trusted_label) ? p.trusted_label : null}
          initialDate={p.trusted_label}
          initialKindId={instance?.kind_id ?? null}
          initialEventLabel={instance?.label ?? null}
          showRotate={false}
        />
        <PlaceEditor ids={[p.id]} current={{ location: p.location, country: p.country, state: p.state, city: p.city }} />
        <CaptionEditor id={p.id} caption={p.caption ?? ""} />
      </div>
    </div>
  );
}

/** Where a photo's date came from, in words. */
const DATE_SOURCE: Record<string, string> = {
  manual: "set by you",
  accepted: "accepted estimate",
  back: "from the back",
  event: "from the event",
}

/** The photo's duplicate group: links to each, Compare side by side, and which to keep for export. */
function DuplicatesRow({ id, members, list }: { id: number; members: number[]; list: ListParams }) {
  const qc = useQueryClient();
  const [comparing, setComparing] = useState(false);
  // The same queries the compare view uses, so it opens with them already loaded.
  const details = useQueries({ queries: members.map((m) => ({ queryKey: ["photo", m], queryFn: () => api.photo(m) })) });
  const loaded = details.every((d) => d.data);
  const kept = details.filter((d) => d.data?.is_dup_keeper).length;
  const thisKept = details[members.indexOf(id)]?.data?.is_dup_keeper ?? false;
  const act = async (call: Promise<unknown>) => {
    await call;
    qc.invalidateQueries();
  };
  return (
    <div>
      <span className="facts__k">Duplicates</span>
      <span className="facts__v dup-row">
        {members.map((m, i) => {
          const d = details[i]?.data;
          return m === id ? (
            <span key={m} className="is-current">This photo</span>
          ) : (
            <a key={m} href={photoHref(m, list)} title={d?.rel_path}>
              {d ? d.rel_path.split("/").slice(-2).join(" / ") : "…"}
            </a>
          );
        })}
        <button className="btn btn--ghost" onClick={() => setComparing(true)}>
          Compare side by side
        </button>
        {thisKept && <span className="badge badge--ok">{kept === members.length ? "all exported" : "exported"}</span>}
        {!(thisKept && kept === 1) && (
          <button className="btn btn--ghost" onClick={() => act(api.keeper(id))} title="Which copy Export writes">
            {thisKept ? "Export only this one" : "Export this one"}
          </button>
        )}
        {loaded && kept < members.length && (
          <button className="btn btn--ghost" onClick={() => act(api.keepAll(id))}>
            {members.length === 2 ? "Export both" : `Export all ${members.length}`}
          </button>
        )}
        <button
          className="btn btn--ghost"
          title="These aren't the same picture: split them up (Import keeps them apart; Undo puts them back)"
          onClick={() => act(api.notDuplicates(id))}
        >
          Not duplicates
        </button>
      </span>
      {comparing && <DupCompare ids={members} list={list} onClose={() => setComparing(false)} />}
    </div>
  );
}

function CaptionEditor({ id, caption }: { id: number; caption: string }) {
  const update = useUpdatePhotos();
  // Saved when you click or tab away; say so, since there's no Save button. Leaving the page (or going
  // to the next photo) with it still typed saves it too.
  const [state, setState] = useState<"" | "editing" | "saving" | "saved" | "error">("");
  useEffect(() => setState(""), [id]);
  const field = useSaveOnLeave((value, leaving) => {
    if (value === caption) return leaving || setState((s) => (s === "editing" ? "" : s));
    if (leaving) return update.mutate({ ids: [id], caption: value });
    setState("saving");
    update.mutate({ ids: [id], caption: value }, { onSuccess: () => setState("saved"), onError: () => setState("error") });
  }, id);
  return (
    <label className="field caption">
      <span className="field__label">
        Caption / notes (exported as description){" "}
        <em className="muted small" aria-live="polite">
          {state === "editing" ? "saved when you click away" : state === "saving" ? "saving…" : state === "saved" ? "✓ saved" : ""}
        </em>
        {state === "error" && <em className="error small"> not saved: {update.error instanceof Error ? update.error.message : "try again"}</em>}
      </span>
      <textarea
        key={`${id}:${caption}`}
        defaultValue={caption}
        rows={2}
        onChange={(e) => (field.onChange(e), setState("editing"))}
        onBlur={field.onBlur}
      />
    </label>
  );
}

/** True while the page is pinch-zoomed in (visual viewport scaled up). */
function usePinchZoomed(): boolean {
  const [zoomed, setZoomed] = useState(false);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const on = () => setZoomed(vv.scale > 1.01);
    on();
    vv.addEventListener("resize", on);
    return () => vv.removeEventListener("resize", on);
  }, []);
  return zoomed;
}

/** The original's file name, its folder on the Mac (with Copy, for Finder's Go to Folder, ⇧⌘G)
 *  and a link to that folder's album here. Browsers can't open Finder folders from a web page. */
function SourceFile({ rel, folder, albumId, children }: { rel: string; folder: string | null; albumId: number; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const name = rel.split("/").pop() ?? rel;
  const copy = async () => {
    if (!folder) return;
    try {
      await navigator.clipboard.writeText(folder); // only on https or localhost
    } catch {
      const t = document.createElement("textarea"); // plain http on the home network
      t.value = folder;
      document.body.appendChild(t);
      t.select();
      document.execCommand("copy");
      t.remove();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="photo-view__file">
      <span className="photo-view__filename" title={rel}>{name}</span>
      {folder && (
        <>
          <span className="muted small photo-view__folder" title={folder}>{folder}</span>
          <button className="btn btn--ghost" onClick={copy} title="Copy the folder path; in Finder press ⇧⌘G and paste">
            {copied ? "Copied" : "Copy folder path"}
          </button>
        </>
      )}
      <a className="btn btn--ghost" href={gridHref({ albums: String(albumId) })}>Open album</a>
      {children}
    </div>
  );
}

/** Dates set on the nearest photos before and after this one in its album: take one exactly, or a
 *  similar date (about that year, ±2 years). Shown while this photo's date is still an estimate. */
function NearbyDateChoices({ nearby, busy, onPick }: { nearby: NearbyDate[]; busy: boolean; onPick: (date: string) => void }) {
  const closest = [...nearby].sort((a, b) => Math.abs(a.offset) - Math.abs(b.offset))[0];
  const exact = nearby.filter((n, i) => nearby.findIndex((m) => m.label === n.label) === i); // one button per distinct date
  const where = (n: NearbyDate) => `${Math.abs(n.offset)} photo${Math.abs(n.offset) === 1 ? "" : "s"} ${n.offset < 0 ? "before" : "after"}`;
  return (
    <div className="nearby-dates">
      <span className="facts__k">Nearby</span>
      {exact.map((n) => (
        <button key={n.photo_id} className="btn btn--ghost" disabled={busy} onClick={() => onPick(n.label)} title={`The date set on the photo ${where(n)}`}>
          Same date: {n.label} <span className="muted small">({where(n)})</span>
        </button>
      ))}
      <button className="btn btn--ghost" disabled={busy} onClick={() => onPick(`~${closest.year}`)} title={`About ${closest.year}, give or take 2 years`}>
        Similar date: ~{closest.year}
      </button>
    </div>
  );
}

/** Where Back goes: a list of picked photos (opened from a People › Check group) returns there. */
function backHref(list: ListParams): string {
  return list.ids ? "#/people?tab=check" : gridHref(list);
}
