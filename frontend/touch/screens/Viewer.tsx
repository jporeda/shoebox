import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { albumLabel, api, imgUrl, type Face, type ListParams, type PhotoDetail, type PhotoSummary } from "../../src/api";
import { useMedia, usePhotos, usePlaces, useSaveOnLeave, useUpdatePhotos } from "../../src/hooks";
import { DatePanel, DATE_SOURCE, EventPanel } from "../dating";
import { FacesPanel, guessOf, useRefreshFaces } from "../faces";
import { LAST_PHOTO_EVENT, stepPhoto } from "../route";
import { plural } from "../ui";

type Tab = "date" | "people" | "info";

export function Viewer({ id, list, faces: focus, backLabel, onClose }: {
  id: number;
  /** The photos to swipe through. */
  list: ListParams;
  /** Opened from faces (Check, To name): open on People with the face on this photo highlighted. */
  faces?: number[];
  /** Where Back goes, by name: "Check", "Mom", "Date", "Photos". */
  backLabel: string;
  onClose: () => void;
}) {
  const detail = useQuery({ queryKey: ["photo", id], queryFn: () => api.photo(id) });
  const faces = useQuery({ queryKey: ["photoFaces", id, detail.data?.rotation], queryFn: () => api.photoFaces(id), enabled: !!detail.data });
  const photos = usePhotos(list);
  const items = photos.data?.items ?? [];
  const idx = items.findIndex((p) => p.id === id);
  const prev = idx > 0 ? items[idx - 1] : null;
  const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : null;
  const wide = useMedia("(min-width: 768px)");
  // Phones start with the sheet folded down to a summary; iPads keep the side panel open.
  const [tab, setTab] = useState<Tab | null>(focus?.length ? "people" : null);
  const shown: Tab | null = tab ?? (wide ? "date" : null);
  const [activeFace, setActiveFace] = useState<number | null>(null);
  // Drawing a box around a face the detector missed: the photo stops swiping while you draw.
  const [drawing, setDrawing] = useState(false);
  const [drawn, setDrawn] = useState<number | null>(null); // the face just drawn: its name box comes up
  const [drawError, setDrawError] = useState("");
  const refreshFaces = useRefreshFaces();
  useEffect(() => (setDrawing(false), setDrawn(null), setDrawError("")), [id]);
  const addFace = async (box: { x: number; y: number; w: number; h: number }) => {
    setDrawError("");
    try {
      const f = await api.drawFace(id, box);
      setDrawing(false);
      setDrawn(f.id);
      setActiveFace(f.id);
      setTab("people");
      refreshFaces();
    } catch (e) {
      setDrawError(e instanceof Error ? e.message : String(e));
    }
  };
  // A tap on the photo folds the panel away (never the Back button) for a clear view.
  const [chrome, setChrome] = useState(true);
  // On each photo, point out the face it was opened for (if any).
  useEffect(() => {
    const want = new Set(focus ?? []);
    // Otherwise keep the one picked (or just drawn) while it's still on this photo.
    setActiveFace((cur) => faces.data?.find((f) => want.has(f.id))?.id ?? (faces.data?.some((f) => f.id === cur) ? cur : null));
  }, [id, faces.data, focus]);

  const step = (to: { id: number } | null) => to && stepPhoto(to.id);
  const close = () => {
    // The screen underneath scrolls to the photo you ended on.
    window.dispatchEvent(new CustomEvent(LAST_PHOTO_EVENT, { detail: id }));
    onClose();
  };
  useEffect(() => {
    for (const n of [prev, next]) if (n) new Image().src = imgUrl(n.sha256, "preview", n.rotation);
  }, [prev, next]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "ArrowLeft") step(prev);
      else if (e.key === "ArrowRight") step(next);
      else if (e.key === "Escape") drawing ? setDrawing(false) : close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const p = detail.data;
  // The list already has what the photo itself needs: show it at once, the details follow.
  const pic: PhotoSummary | undefined = p ?? items[idx];
  const counter = idx >= 0 ? `${idx + 1} of ${items.length}` : "";
  return (
    <div className={`t-viewer ${shown ? "has-panel" : ""} ${chrome ? "" : "no-chrome"}`}>
      <div className="t-viewer__bar">
        <button className="t-btn t-btn--glass t-viewer__back" onClick={close} aria-label={`Back to ${backLabel}`}>
          ‹ {backLabel}
        </button>
        <span className="t-viewer__counter">{counter}</span>
        <span className="t-viewer__nav">
          <button className="t-btn t-btn--glass" disabled={!prev} onClick={() => step(prev)} aria-label="Previous photo">
            ‹
          </button>
          <button className="t-btn t-btn--glass" disabled={!next} onClick={() => step(next)} aria-label="Next photo">
            ›
          </button>
        </span>
      </div>
      {pic ? (
        <Stage
          photo={pic}
          boxes={shown === "people" ? (faces.data ?? []).filter((f) => !f.ignored) : []}
          activeFace={activeFace}
          onFace={(fid) => (setActiveFace(fid), setTab("people"))}
          onPrev={() => step(prev)}
          onNext={() => step(next)}
          onTap={() => setChrome((c) => !c)}
          onDown={close}
          drawing={drawing}
          drawError={drawError}
          onDrawn={addFace}
          onCancelDraw={() => (setDrawing(false), setDrawError(""))}
        />
      ) : (
        <div className="t-stage">
          <p className="t-muted">{detail.isError ? "Couldn't load this photo." : "Loading…"}</p>
        </div>
      )}
      {p && (
        <div className="t-panel">
          <Summary photo={p} faces={faces.data?.filter((f) => !f.ignored).length ?? 0} named={faces.data ?? []} />
          <div className="t-panel__tabs" role="tablist">
            {(["date", "people", "info"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={shown === t}
                className={shown === t ? "is-on" : ""}
                onClick={() => setTab(shown === t && !wide ? null : t)}
              >
                {t === "date" ? "Date" : t === "people" ? `People${faces.data ? ` (${faces.data.filter((f) => !f.ignored).length})` : ""}` : "Info"}
              </button>
            ))}
          </div>
          {shown && (
            <div className="t-panel__body">
              {shown === "date" && (
                <>
                  <DatePanel ids={[p.id]} photo={p} showNow={false} previous={prev?.status === "reviewed" ? prev.date_label : null} />
                  <h3 className="t-h3">Event</h3>
                  <EventPanel ids={[p.id]} photo={p} />
                </>
              )}
              {shown === "people" && (
                <FacesPanel faces={faces.data ?? []} active={activeFace} onActive={setActiveFace} drawn={drawn}
                  drawing={drawing} onDraw={() => (setDrawing(true), setChrome(true))} />
              )}
              {shown === "info" && <InfoPanel photo={p} />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


/** One line under the photo: its date, event and who's in it. */
function Summary({ photo: p, faces, named }: { photo: PhotoDetail; faces: number; named: Face[] }) {
  const names = named.filter((f) => !f.ignored && f.person_name && f.source !== "auto").map((f) => (f.alt_name ? `${f.person_name} or ${f.alt_name}` : f.person_name));
  const guesses = named.filter((f) => !f.ignored && guessOf(f)).length;
  return (
    <div className="t-summary">
      <span className={`t-summary__date t-status-${p.status}`}>{p.date_label || "No date"}</span>
      {p.date_source && <span className="t-muted"> {DATE_SOURCE[p.date_source]}</span>}
      {p.event_label && <span> · {p.event_label}</span>}
      <div className="t-summary__people t-muted">
        {names.length ? names.join(", ") : faces ? plural(faces, "face") : "No faces"}
        {guesses > 0 && ` · ${guesses === 1 ? "1 guess" : `${guesses} guesses`} to check`}
      </div>
    </div>
  );
}

/** The photo, fitted to the space, with face boxes; swipe for next/previous, double-tap to zoom. */
function Stage({ photo: p, boxes, activeFace, onFace, onPrev, onNext, onTap, onDown, drawing, drawError, onDrawn, onCancelDraw }: {
  photo: PhotoSummary;
  boxes: { id: number; x: number; y: number; w: number; h: number; person_name?: string | null }[];
  activeFace: number | null;
  onFace: (id: number) => void;
  onPrev: () => void;
  onNext: () => void;
  onTap: () => void;
  /** Swipe down: close, like the Photos app. */
  onDown: () => void;
  /** Drawing a missed face: one finger or a pen drags a box (a second finger is ignored). */
  drawing: boolean;
  drawError: string;
  onDrawn: (box: { x: number; y: number; w: number; h: number }) => void;
  onCancelDraw: () => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState<{ s: number; x: number; y: number } | null>(null);
  const pointer = useRef<{ x: number; y: number; t: number; ox: number; oy: number } | null>(null);
  const lastTap = useRef(0);
  const frame = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ start: [number, number]; box: { x: number; y: number; w: number; h: number }; pointerId: number } | null>(null);
  useEffect(() => setDrag(null), [p.id, drawing]);
  // Where the finger is on the photo (0..1), measured on the shown frame, so it's right when zoomed in too.
  const at = (e: React.PointerEvent): [number, number] => {
    const r = frame.current!.getBoundingClientRect();
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };
  const turned = p.rotation === 90 || p.rotation === 270;
  const aspect = p.width && p.height ? (turned ? p.height / p.width : p.width / p.height) : 4 / 3;
  useEffect(() => setZoom(null), [p.id]);

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const fit = () => {
      const W = el.clientWidth, H = el.clientHeight;
      setSize(W / H > aspect ? { w: H * aspect, h: H } : { w: W, h: W / aspect });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);

  return (
    <div
      ref={stage}
      className={`t-stage ${drawing ? "is-drawing" : ""}`}
      onPointerDown={(e) => {
        if (drawing) {
          if (!e.isPrimary || !frame.current || (e.target as Element).closest(".t-drawbar")) return;
          e.preventDefault();
          try {
            e.currentTarget.setPointerCapture(e.pointerId); // keep drawing if the finger leaves the photo
          } catch {
            // not a live pointer: draw without capture
          }
          const s = at(e);
          setDrag({ start: s, box: { x: s[0], y: s[1], w: 0, h: 0 }, pointerId: e.pointerId });
          return;
        }
        if ((e.target as Element).closest(".t-box")) return;
        pointer.current = { x: e.clientX, y: e.clientY, t: e.timeStamp, ox: zoom?.x ?? 0, oy: zoom?.y ?? 0 };
      }}
      onPointerMove={(e) => {
        if (drawing) {
          if (!drag || e.pointerId !== drag.pointerId) return;
          const [x, y] = at(e), [sx, sy] = drag.start;
          setDrag({ ...drag, box: { x: Math.min(x, sx), y: Math.min(y, sy), w: Math.abs(x - sx), h: Math.abs(y - sy) } });
          return;
        }
        const s = pointer.current;
        if (s && zoom) setZoom({ ...zoom, x: s.ox + e.clientX - s.x, y: s.oy + e.clientY - s.y }); // pan
      }}
      onPointerUp={(e) => {
        if (drawing) {
          if (!drag || e.pointerId !== drag.pointerId) return;
          setDrag(null);
          if (drag.box.w >= 0.02 && drag.box.h >= 0.02) onDrawn(drag.box); // a tap isn't a box
          return;
        }
        const s = pointer.current;
        pointer.current = null;
        if (!s) return;
        const dx = e.clientX - s.x, dy = e.clientY - s.y;
        if (!zoom && Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.5 && e.timeStamp - s.t < 800) {
          if (dx < 0) onNext();
          else onPrev();
          return;
        }
        if (!zoom && dy > 90 && dy > Math.abs(dx) * 1.5 && e.timeStamp - s.t < 800) {
          onDown();
          return;
        }
        if (Math.hypot(dx, dy) > 10) return;
        if (e.timeStamp - lastTap.current < 300) {
          // Double tap: zoom in where tapped, or back out.
          lastTap.current = 0;
          const r = stage.current!.getBoundingClientRect();
          setZoom(zoom ? null : { s: 2.5, x: (r.left + r.width / 2 - e.clientX) * 1.5, y: (r.top + r.height / 2 - e.clientY) * 1.5 });
          return;
        }
        lastTap.current = e.timeStamp;
        window.setTimeout(() => lastTap.current && e.timeStamp === lastTap.current && onTap(), 320);
      }}
      onPointerCancel={() => ((pointer.current = null), setDrag(null))}
    >
      {size && (
        <div
          ref={frame}
          className="t-frame"
          style={{
            width: size.w,
            height: size.h,
            transform: zoom ? `translate(${zoom.x}px, ${zoom.y}px) scale(${zoom.s})` : undefined,
          }}
        >
          <img src={imgUrl(p.sha256, "preview", p.rotation)} alt={p.rel_path} draggable={false} />
          {boxes.map((b) => (
            <button
              key={b.id}
              className={`t-box ${b.id === activeFace ? "is-active" : ""}`}
              style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }}
              onClick={() => onFace(b.id)}
              aria-label={b.person_name ?? "Face"}
            />
          ))}
          {drag && (
            <div className="t-box is-new" style={{ left: `${drag.box.x * 100}%`, top: `${drag.box.y * 100}%`, width: `${drag.box.w * 100}%`, height: `${drag.box.h * 100}%` }} />
          )}
        </div>
      )}
      {drawing && (
        <div className="t-drawbar" role="status">
          <span>{drawError ? `Not saved: ${drawError}` : "Drag a box around the face"}</span>
          <button className="t-btn t-btn--glass" onClick={onCancelDraw}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

/** Caption, place, the back of the photo, and where the file is. */
function InfoPanel({ photo: p }: { photo: PhotoDetail }) {
  const update = useUpdatePhotos();
  const places = usePlaces();
  const [caption, setCaption] = useState<"" | "saving" | "saved" | "error">("");
  const [place, setPlace] = useState({ country: p.country ?? "", state: p.state ?? "", city: p.city ?? "" });
  const [placeNote, setPlaceNote] = useState("");
  useEffect(() => {
    setPlace({ country: p.country ?? "", state: p.state ?? "", city: p.city ?? "" });
    setCaption("");
    setPlaceNote("");
  }, [p.id, p.country, p.state, p.city]);
  const placeChanged = place.country !== (p.country ?? "") || place.state !== (p.state ?? "") || place.city !== (p.city ?? "");
  // Swiping to the next photo, closing it or another panel tab never blurs a field: what's typed and
  // not saved yet is saved then (the Undo toast shows it), not dropped.
  const captionField = useSaveOnLeave((value, leaving) => {
    if (value === (p.caption ?? "")) return;
    if (leaving) return update.mutate({ ids: [p.id], caption: value });
    setCaption("saving");
    update.mutate({ ids: [p.id], caption: value }, { onSuccess: () => setCaption("saved"), onError: () => setCaption("error") });
  }, p.id);
  const placeEdit = useRef<{ id: number; place: typeof place } | null>(null);
  useEffect(
    () => () => {
      const e = placeEdit.current;
      placeEdit.current = null;
      if (e) update.mutate({ ids: [e.id], country: e.place.country.trim(), state: e.place.state.trim(), city: e.place.city.trim() });
    },
    [p.id],
  );
  const editPlace = (next: typeof place) => {
    setPlace(next);
    setPlaceNote("");
    const same = next.country === (p.country ?? "") && next.state === (p.state ?? "") && next.city === (p.city ?? "");
    placeEdit.current = same ? null : { id: p.id, place: next };
  };
  const options = (k: "country" | "state" | "city") => [...new Set((places.data ?? []).map((x) => x[k]).filter(Boolean) as string[])];
  return (
    <div className="t-info">
      <label className="t-field">
        <span className="t-field__label">
          Caption <span className="t-muted">{caption === "saving" ? "saving…" : caption === "saved" ? "✓ saved" : caption === "error" ? "not saved" : "saved when you're done typing"}</span>
        </span>
        <textarea
          key={`${p.id}:${p.caption ?? ""}`}
          defaultValue={p.caption ?? ""}
          rows={3}
          onChange={captionField.onChange}
          onBlur={captionField.onBlur}
        />
      </label>
      <div className="t-place">
        {(["country", "state", "city"] as const).map((k) => (
          <label key={k} className="t-field">
            <span className="t-field__label">{k === "country" ? "Country" : k === "state" ? "State" : "City"}</span>
            <input list={`t-place-${k}`} value={place[k]} onChange={(e) => editPlace({ ...place, [k]: e.target.value })} autoComplete="off" />
            <datalist id={`t-place-${k}`}>
              {options(k).map((o) => <option key={o} value={o} />)}
            </datalist>
          </label>
        ))}
        <button
          className="t-btn t-btn--primary"
          disabled={!placeChanged || update.isPending}
          onClick={() => {
            placeEdit.current = null;
            update.mutate(
              { ids: [p.id], country: place.country.trim(), state: place.state.trim(), city: place.city.trim() },
              { onSuccess: () => setPlaceNote("✓ saved"), onError: (e) => setPlaceNote(String(e)) },
            );
          }}
        >
          Save place
        </button>
        {placeNote && <span className="t-muted">{placeNote}</span>}
      </div>
      {p.back_sha256 && (
        <figure className="t-back">
          <a href={imgUrl(p.back_sha256, "preview", 0)} target="_blank" rel="noreferrer">
            <img src={imgUrl(p.back_sha256, "preview", 0)} alt="The back of the photo" loading="lazy" />
          </a>
          <figcaption className="t-muted">
            The back{p.back_transcription ? `: “${p.back_transcription}”` : ""}
            {p.back_date_label ? ` · says ${p.back_date_label}` : ""}
          </figcaption>
        </figure>
      )}
      <dl className="t-facts">
        <dt>Album</dt>
        <dd>{albumLabel({ name: p.album_name, rel_path: p.rel_path.split("/").slice(0, -1).join("/") })}</dd>
        <dt>File</dt>
        <dd>{p.rel_path.split("/").pop()}</dd>
        {p.scan_datetime && (
          <>
            <dt>Scanned</dt>
            <dd>{p.scan_datetime}</dd>
          </>
        )}
      </dl>
    </div>
  );
}
