import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { albumLabel, api, ApiError, faceName, imgUrl, type Face, type ListParams, type PhotoDetail, type PhotoSummary } from "../../src/api";
import { useLocations, useMedia, usePhotos, usePlaces, useSaveOnLeave, useUpdatePhotos } from "../../src/hooks";
import { DatePanel, DATE_SOURCE, EventPanel } from "../dating";
import { FacesPanel, faceStatus, guessOf, useFaceAction, useFaceZoomSetting, useRefreshFaces } from "../faces";
import { stepPhoto } from "../route";
import { saveOrKeep } from "../pending";
import { ACCEPT_ALL, CONFIRMED_NAMES, ESTIMATED_NAMES, FIRST_NAMES, OPEN_ESTIMATE, OPEN_EXACT, useSwitch } from "../settings";
import { plural } from "../ui";

type Tab = "date" | "people" | "info" | "advanced";
const TAB_LABEL: Record<Tab, string> = { date: "Date", people: "People", info: "Info", advanced: "Advanced" };

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
  // A subset (a Check group's photos, a person's, a filtered grid) goes round: Previous on the first
  // photo is the last, Next on the last is the first. All photos stop at either end.
  const subset = Object.entries(list).some(([k, v]) => v !== undefined && v !== "" && k !== "order");
  const wrap = subset && items.length > 1;
  const prev = idx > 0 ? items[idx - 1] : wrap && idx === 0 ? items[items.length - 1] : null;
  const next = idx >= 0 && idx < items.length - 1 ? items[idx + 1] : wrap && idx === items.length - 1 ? items[0] : null;
  const wide = useMedia("(min-width: 768px)");
  // Phones start with the sheet folded down to a summary; iPads keep the side panel open.
  const [tab, setTab] = useState<Tab | null>(focus?.length ? "people" : null);
  const shown: Tab | null = tab ?? (wide ? "date" : null);
  const [activeFace, setActiveFace] = useState<number | null>(null);
  // Drawing a box around a face the detector missed: the photo stops swiping while you draw.
  const [drawing, setDrawing] = useState(false);
  const [drawn, setDrawn] = useState<number | null>(null); // the face just drawn: its name box comes up
  const [drawError, setDrawError] = useState("");
  // Boxes drawn since drawing started: drawing goes on (for the next missed face) until a tap off the
  // photo, or on one of these, whose name box then comes up.
  const [newBoxes, setNewBoxes] = useState<number[]>([]);
  const refreshFaces = useRefreshFaces();
  useEffect(() => (setDrawing(false), setDrawn(null), setDrawError(""), setNewBoxes([])), [id]);
  const startDrawing = () => (setDrawing(true), setChrome(true), setNewBoxes([]), setDrawn(null), setDrawError(""));
  /** Stop drawing; `name`: a box just drawn that was tapped, whose name box comes up. */
  const finishDrawing = (name: number | null) => {
    setDrawing(false);
    setDrawError("");
    if (name !== null) (setDrawn(name), setActiveFace(name), setTab("people"));
  };
  // A tap anywhere off the photo (the panel, the bar) also ends drawing; the photo's own area is
  // handled by Stage (a tap beside the photo ends it too, a tap on a new box names it).
  useEffect(() => {
    if (!drawing) return;
    const off = (e: PointerEvent) => {
      if (!(e.target as Element).closest?.(".t-stage")) finishDrawing(null);
    };
    document.addEventListener("pointerdown", off, true);
    return () => document.removeEventListener("pointerdown", off, true);
  }, [drawing]); // eslint-disable-line react-hooks/exhaustive-deps
  const addFace = async (box: { x: number; y: number; w: number; h: number }) => {
    setDrawError("");
    try {
      const f = await api.drawFace(id, box);
      setNewBoxes((b) => [...b, f.id]); // still drawing: the next missed face can be boxed right away
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
  // The screen underneath then scrolls to the photo you ended on: App tells it, however the photo
  // closes (this Back, swipe down, or the phone's own Back).
  const close = () => onClose();
  useEffect(() => {
    for (const n of [prev, next]) if (n) new Image().src = imgUrl(n.sha256, "preview", n.rotation);
  }, [prev, next]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.key === "ArrowLeft") step(prev);
      else if (e.key === "ArrowRight") step(next);
      else if (e.key === "Escape") drawing ? finishDrawing(null) : close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const [acceptAllOn] = useSwitch(ACCEPT_ALL);
  const [confirmedNames] = useSwitch(CONFIRMED_NAMES);
  const [estimatedNames] = useSwitch(ESTIMATED_NAMES);
  const [firstNames] = useSwitch(FIRST_NAMES);
  const accepting = useFaceAction();
  // The face list's order, set when the photo's faces load and kept while it's open (confirming a
  // name doesn't move its card from under your finger): faces drawn since come first, then the
  // app's guesses, then unknown faces, then confirmed ones.
  const order = useRef<{ photo: number; ids: number[] } | null>(null);
  if (faces.data) {
    if (order.current?.photo !== id) {
      const rank = { guess: 0, unknown: 1, named: 2 };
      const sorted = [...faces.data].sort((a, b) => Number(a.ignored) - Number(b.ignored) || rank[faceStatus(a)] - rank[faceStatus(b)]);
      order.current = { photo: id, ids: sorted.map((f) => f.id) };
    }
    const added = faces.data.filter((f) => !order.current!.ids.includes(f.id)).map((f) => f.id);
    if (added.length) order.current.ids = [...added.reverse(), ...order.current.ids]; // the newest on top
  }
  const listed = [...(faces.data ?? [])].sort((a, b) => order.current!.ids.indexOf(a.id) - order.current!.ids.indexOf(b.id));
  const live = (faces.data ?? []).filter((f) => !f.ignored);
  const guesses = live.filter((f) => guessOf(f));
  // Each box's colour (green: a person confirmed the name, yellow: the app has a guess, red: no idea)
  // and, if the Advanced tab says so, its label: the confirmed name, or the guess with a "?".
  const boxOf = (f: Face) => {
    const g = guessOf(f);
    const short = (name: string | null) => (name && firstNames ? firstOnly(name) : name);
    const status = faceStatus(f);
    if (g) return { status, label: estimatedNames ? `${short(g.name)}?` : null };
    if (status === "named") return { status, label: confirmedNames ? short(faceName(f)) : null };
    return { status, label: null };
  };

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
          {/* Right under the arrows (Advanced tab switch): on the photo, never in the panel's way. */}
          {acceptAllOn && guesses.length > 0 && !drawing && (
            <div className="t-acceptall">
              <button className="t-btn t-btn--glass" disabled={accepting.busy}
                aria-label={guesses.length === 1 ? "Accept the guess" : `Accept all ${guesses.length} guesses`}
                onClick={() => accepting.run(() => api.acceptFaces(guesses.map((f) => f.id)))}>
                ✓ Accept
              </button>
              {accepting.error && <span className="t-acceptall__error">Not saved: {accepting.error}</span>}
            </div>
          )}
        </span>
      </div>
      {pic ? (
        <Stage
          photo={pic}
          boxes={shown === "people" ? live.map((f) => ({ ...f, ...boxOf(f) })) : []}
          activeFace={activeFace}
          onFace={(fid) => (setActiveFace(fid), setTab("people"))}
          onPrev={() => step(prev)}
          onNext={() => step(next)}
          onTap={() => setChrome((c) => !c)}
          onDown={close}
          drawing={drawing}
          drawError={drawError}
          onDrawn={addFace}
          newBoxes={(faces.data ?? []).filter((f) => newBoxes.includes(f.id))}
          onFinishDraw={finishDrawing}
        />
      ) : (
        <div className="t-stage">
          <p className="t-muted">{detail.isError ? "Couldn't load this photo." : "Loading…"}</p>
        </div>
      )}
      {p && (
        <div className="t-panel">
          <Summary photo={p} faces={faces.data?.filter((f) => !f.ignored).length ?? 0} named={faces.data ?? []} onDate={() => setTab("date")} />
          <div className="t-panel__tabs" role="tablist">
            {(["date", "people", "info", "advanced"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={shown === t}
                className={shown === t ? "is-on" : ""}
                onClick={() => setTab(shown === t && !wide ? null : t)}
              >
                {t === "people" && faces.data ? `People (${faces.data.filter((f) => !f.ignored).length})` : TAB_LABEL[t]}
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
                <FacesPanel faces={listed} active={activeFace} onActive={setActiveFace} drawn={drawn}
                  drawing={drawing} onDraw={startDrawing}
                  photo={{ src: imgUrl(p.sha256, "preview", p.rotation), aspect: aspectOf(p) }} />
              )}
              {shown === "info" && <InfoPanel photo={p} />}
              {shown === "advanced" && <AdvancedPanel photo={p} />}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


/** "Ana Smith" → "Ana"; "Ana Smith or Ben Smith" (twins) → "Ana or Ben". */
function firstOnly(name: string): string {
  return name.split(" or ").map((n) => n.trim().split(/\s+/)[0]).join(" or ");
}

/** Width / height of the photo as shown (turned a quarter, a portrait scan is landscape). */
function aspectOf(p: PhotoSummary): number {
  const turned = p.rotation === 90 || p.rotation === 270;
  return p.width && p.height ? (turned ? p.height / p.width : p.width / p.height) : 4 / 3;
}

/** Turning the photo, and settings for the rest of the panel. */
function AdvancedPanel({ photo: p }: { photo: PhotoDetail }) {
  const update = useUpdatePhotos();
  const [zoom, setZoom] = useFaceZoomSetting();
  const [acceptAll, setAcceptAll] = useSwitch(ACCEPT_ALL);
  const [confirmed, setConfirmed] = useSwitch(CONFIRMED_NAMES);
  const [estimated, setEstimated] = useSwitch(ESTIMATED_NAMES);
  const [first, setFirst] = useSwitch(FIRST_NAMES);
  const [openEstimate, setOpenEstimate] = useSwitch(OPEN_ESTIMATE);
  const [openExact, setOpenExact] = useSwitch(OPEN_EXACT);
  const rotate = (by: number) => update.mutate({ ids: [p.id], rotate_by: by });
  return (
    <div className="t-advanced">
      <h3 className="t-h3">Rotation</h3>
      <div className="t-face__actions">
        <button className="t-btn" disabled={update.isPending} onClick={() => rotate(270)}>
          ⟲ Rotate left
        </button>
        <button className="t-btn" disabled={update.isPending} onClick={() => rotate(90)}>
          ⟳ Rotate right
        </button>
      </div>
      {update.isError && <p className="t-error">Not turned: {update.error instanceof Error ? update.error.message : String(update.error)}</p>}
      <h3 className="t-h3">Faces</h3>
      <div className="t-list">
        <label className="t-row">
          <span>Enable zoom</span>
          <input type="checkbox" className="t-switch" checked={zoom} onChange={(e) => setZoom(e.target.checked)} />
        </label>
      </div>
      <p className="t-muted t-small">Adds “Zoom in” to the name box on the People tab, for small or blurry faces.</p>
      <div className="t-list">
        <label className="t-row">
          <span>Enable Accept all Guesses</span>
          <input type="checkbox" className="t-switch" checked={acceptAll} onChange={(e) => setAcceptAll(e.target.checked)} />
        </label>
        <label className="t-row">
          <span>Show confirmed names</span>
          <input type="checkbox" className="t-switch" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
        </label>
        <label className="t-row">
          <span>Show estimated names</span>
          <input type="checkbox" className="t-switch" checked={estimated} onChange={(e) => setEstimated(e.target.checked)} />
        </label>
        <label className={`t-row ${confirmed || estimated ? "" : "is-off"}`}>
          <span>First names only</span>
          <input type="checkbox" className="t-switch" checked={first} disabled={!confirmed && !estimated} onChange={(e) => setFirst(e.target.checked)} />
        </label>
      </div>
      <p className="t-muted t-small">Accept all puts a button on the photo that says yes to every name guess on it at once (Undo takes them back). On the People tab, face boxes are green when the name is confirmed, yellow when there's a guess, and red when nobody knows yet. The names switches put confirmed names and guesses (ending in “?”) beside the boxes; first names only keeps them short in crowded photos.</p>
      <h3 className="t-h3">Date</h3>
      <div className="t-list">
        <label className="t-row">
          <span>Open the estimate by default</span>
          <input type="checkbox" className="t-switch" checked={openEstimate} onChange={(e) => setOpenEstimate(e.target.checked)} />
        </label>
        <label className="t-row">
          <span>Open the exact by default</span>
          <input type="checkbox" className="t-switch" checked={openExact} onChange={(e) => setOpenExact(e.target.checked)} />
        </label>
      </div>
    </div>
  );
}

/** One line under the photo: its date, event and who's in it. */
function Summary({ photo: p, faces, named, onDate }: { photo: PhotoDetail; faces: number; named: Face[]; onDate: () => void }) {
  const names = named.filter((f) => !f.ignored && f.person_name && f.source !== "auto").map((f) => (f.alt_name ? `${f.person_name} or ${f.alt_name}` : f.person_name));
  const guesses = named.filter((f) => !f.ignored && guessOf(f)).length;
  return (
    <div className="t-summary">
      {/* The date is a button: it opens the Date tab, where it can be changed. */}
      <button className="t-summary__datebtn" onClick={onDate}
        aria-label={`${p.date_label || "No date"}${p.date_source ? `, ${DATE_SOURCE[p.date_source]}` : ""}. Change the date`}>
        <span className={`t-summary__date t-status-${p.status}`}>{p.date_label || "No date"}</span>
        {p.date_source && <span className="t-muted"> {DATE_SOURCE[p.date_source]}</span>}
        <span className="t-summary__change">Change</span>
      </button>
      {p.event_label && <span> · {p.event_label}</span>}
      <div className="t-summary__people t-muted">
        {names.length ? names.join(", ") : faces ? plural(faces, "face") : "No faces"}
        {guesses > 0 && ` · ${guesses === 1 ? "1 guess" : `${guesses} guesses`} to check`}
      </div>
    </div>
  );
}

/** The photo, fitted to the space, with face boxes; swipe for next/previous, double-tap to zoom. */
function Stage({ photo: p, boxes, activeFace, onFace, onPrev, onNext, onTap, onDown, drawing, drawError, onDrawn, newBoxes, onFinishDraw }: {
  photo: PhotoSummary;
  /** `status`: the box's colour; `label`: shown beside it (the Advanced tab's names switches). */
  boxes: { id: number; x: number; y: number; w: number; h: number; person_name?: string | null; label?: string | null; status: "named" | "guess" | "unknown" }[];
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
  /** Boxes drawn since drawing started: a tap on one ends drawing and names it. */
  newBoxes: { id: number; x: number; y: number; w: number; h: number }[];
  /** End drawing: a tap beside the photo (null), or on one of `newBoxes` (its id), or Done (null). */
  onFinishDraw: (name: number | null) => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState<{ s: number; x: number; y: number } | null>(null);
  const pointer = useRef<{ x: number; y: number; t: number; ox: number; oy: number } | null>(null);
  const lastTap = useRef(0);
  const frame = useRef<HTMLDivElement>(null);
  // `raw`: where the finger went down on the photo, unclamped (outside 0..1: beside the photo).
  const [drag, setDrag] = useState<{ start: [number, number]; raw: [number, number]; box: { x: number; y: number; w: number; h: number }; pointerId: number } | null>(null);
  useEffect(() => setDrag(null), [p.id, drawing]);
  // Where the finger is on the photo (0..1), measured on the shown frame, so it's right when zoomed in too.
  const rawAt = (e: React.PointerEvent): [number, number] => {
    const r = frame.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };
  const at = (e: React.PointerEvent): [number, number] => {
    const clamp = (v: number) => Math.min(1, Math.max(0, v));
    const [x, y] = rawAt(e);
    return [clamp(x), clamp(y)];
  };
  const aspect = aspectOf(p);
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
          setDrag({ start: s, raw: rawAt(e), box: { x: s[0], y: s[1], w: 0, h: 0 }, pointerId: e.pointerId });
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
          if (drag.box.w >= 0.02 && drag.box.h >= 0.02) return onDrawn(drag.box);
          // A tap, not a box: beside the photo, drawing is done; on a box just drawn, name that face.
          const [x, y] = drag.raw;
          if (x < 0 || x > 1 || y < 0 || y > 1) return onFinishDraw(null);
          const hit = newBoxes.find((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h);
          if (hit) onFinishDraw(hit.id);
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
              className={`t-box t-box--${b.status} ${b.id === activeFace ? "is-active" : ""}`}
              style={{ left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` }}
              onClick={() => onFace(b.id)}
              aria-label={b.status === "named" ? (b.person_name ?? "Face") : b.status === "guess" ? `${b.label ?? "Face"}, a guess` : "Unknown face"}
            >
              {b.label && <span className={`t-box__name t-box__name--${b.status}`} style={{ "--zoom": zoom?.s ?? 1 } as React.CSSProperties}>{b.label}</span>}
            </button>
          ))}
          {drag && (
            <div className="t-box is-new" style={{ left: `${drag.box.x * 100}%`, top: `${drag.box.y * 100}%`, width: `${drag.box.w * 100}%`, height: `${drag.box.h * 100}%` }} />
          )}
        </div>
      )}
      {drawing && (
        <div className="t-drawbar" role="status">
          <span>
            {drawError ? `Not saved: ${drawError}`
              : newBoxes.length ? "Box the next face, or tap a new box to name it"
              : "Drag a box around the face"}
          </span>
          <button className="t-btn t-btn--glass" onClick={() => onFinishDraw(null)}>
            Done
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
  const locations = useLocations();
  const [location, setLocation] = useState<"" | "saving" | "saved" | "waiting" | "error">("");
  const [caption, setCaption] = useState<"" | "saving" | "saved" | "waiting" | "error">("");
  // Saved now, or kept on this device and saved once the photos can be reached (never just dropped).
  const keep = (key: string, body: Parameters<typeof update.mutateAsync>[0]) => saveOrKeep(key, body, (b) => update.mutateAsync(b));
  const [place, setPlace] = useState({ country: p.country ?? "", state: p.state ?? "", city: p.city ?? "" });
  const [placeNote, setPlaceNote] = useState("");
  useEffect(() => {
    setPlace({ country: p.country ?? "", state: p.state ?? "", city: p.city ?? "" });
    setCaption("");
    setLocation("");
    setPlaceNote("");
  }, [p.id, p.country, p.state, p.city]);
  const placeChanged = place.country !== (p.country ?? "") || place.state !== (p.state ?? "") || place.city !== (p.city ?? "");
  // Swiping to the next photo, closing it or another panel tab never blurs a field: what's typed and
  // not saved yet is saved then (the Undo toast shows it), not dropped.
  const captionField = useSaveOnLeave((value, leaving) => {
    if (value === (p.caption ?? "")) return;
    const saving = keep(`${p.id}:caption`, { ids: [p.id], caption: value });
    if (leaving) return void saving.catch(() => {}); // kept and retried if it failed; the app says so
    setCaption("saving");
    saving.then(() => setCaption("saved"), (e) => setCaption(e instanceof ApiError ? "error" : "waiting"));
  }, p.id);
  const locationField = useSaveOnLeave((value, leaving) => {
    if (value.trim().split(/\s+/).join(" ") === (p.location ?? "")) return;
    const saving = keep(`${p.id}:location`, { ids: [p.id], location: value });
    if (leaving) return void saving.catch(() => {}); // kept and retried if it failed; the app says so
    setLocation("saving");
    saving.then(() => setLocation("saved"), (e) => setLocation(e instanceof ApiError ? "error" : "waiting"));
  }, p.id);
  const placeEdit = useRef<{ id: number; place: typeof place } | null>(null);
  useEffect(
    () => () => {
      const e = placeEdit.current;
      placeEdit.current = null;
      if (e)
        keep(`${e.id}:place`, { ids: [e.id], country: e.place.country.trim(), state: e.place.state.trim(), city: e.place.city.trim() })
          .catch(() => {}); // kept and retried if it failed; the app says so
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
          Caption <span className="t-muted">{caption === "saving" ? "saving…" : caption === "saved" ? "✓ saved" : caption === "error" ? "not saved" : caption === "waiting" ? "not saved yet: it will save when the photos can be reached" : "saved when you're done typing"}</span>
        </span>
        <textarea
          key={`${p.id}:${p.caption ?? ""}`}
          defaultValue={p.caption ?? ""}
          rows={3}
          onChange={captionField.onChange}
          onBlur={captionField.onBlur}
        />
      </label>
      <label className="t-field">
        <span className="t-field__label">
          Location <span className="t-muted">{location === "saving" ? "saving…" : location === "saved" ? "✓ saved" : location === "error" ? "not saved" : location === "waiting" ? "not saved yet: it will save when the photos can be reached" : "a general area, like “Cape Cod” or “Grandma’s farm”"}</span>
        </span>
        <input
          key={`${p.id}:${p.location ?? ""}`}
          defaultValue={p.location ?? ""}
          list="t-locations"
          autoComplete="off"
          enterKeyHint="done"
          {...locationField}
        />
        <datalist id="t-locations">
          {(locations.data ?? []).map((o) => <option key={o} value={o} />)}
        </datalist>
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
            keep(`${p.id}:place`, { ids: [p.id], country: place.country.trim(), state: place.state.trim(), city: place.city.trim() }).then(
              () => setPlaceNote("✓ saved"),
              (e) => setPlaceNote(e instanceof ApiError ? String(e.message) : "Not saved yet: it will save when the photos can be reached."),
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
