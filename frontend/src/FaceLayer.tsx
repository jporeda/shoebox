import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, ApiError, faceUrl, imgUrl, type Face } from "./api";
import { useTouch } from "./hooks";
import { NameInput } from "./People";

type Box = { x: number; y: number; w: number; h: number };

// Whether face boxes are drawn on photos: a per-browser preference, remembered across photos and visits.
const SHOW_BOXES_KEY = "shoebox.showFaceBoxes";
function readShowBoxes(): boolean {
  try {
    return localStorage.getItem(SHOW_BOXES_KEY) !== "0";
  } catch {
    return true;
  }
}
function writeShowBoxes(show: boolean) {
  try {
    localStorage.setItem(SHOW_BOXES_KEY, show ? "1" : "0");
  } catch {
    // storage blocked: the toggle still works for this page
  }
}

function refreshFaces(qc: QueryClient) {
  for (const k of ["photoFaces", "clusters", "people", "personFaces", "faceStats", "faceReview", "edits"]) qc.invalidateQueries({ queryKey: [k] });
}

/** Photo-view toolbar button: confirm every name the app guessed on this photo, as one undo step. */
export function AcceptGuesses({ photoId }: { photoId: number }) {
  const qc = useQueryClient();
  const faces = useQuery({ queryKey: ["photoFaces", photoId], queryFn: () => api.photoFaces(photoId) });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const guesses = (faces.data ?? []).filter((f) => !f.ignored && guessName(f));
  if (!guesses.length) return error ? <span className="error">{error}</span> : null;

  const accept = async () => {
    setBusy(true);
    setError("");
    try {
      await api.acceptFaces(guesses.map((f) => f.id));
      refreshFaces(qc);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {error && <span className="error">{error}</span>}
      <button className="btn btn--primary" onClick={accept} disabled={busy}>
        Accept {guesses.length === 1 ? "the guess" : `all ${guesses.length} guesses`}
      </button>
    </>
  );
}

/** Front image with face boxes: click a box to name it, or draw a box around a missed face. */
export function FaceLayer({ photoId, sha, rotation, alt }: { photoId: number; sha: string; rotation: number; alt: string }) {
  const qc = useQueryClient();
  const faces = useQuery({ queryKey: ["photoFaces", photoId], queryFn: () => api.photoFaces(photoId) });
  const [active, setActive] = useState<number | null>(null);
  const [drawing, setDrawing] = useState(false);
  // "Draw several": stays in drawing mode after each box, until switched off (or another photo opens).
  const [drawMany, setDrawMany] = useState(false);
  const [drawnCount, setDrawnCount] = useState(0);
  useEffect(() => {
    setDrawMany(false);
    setDrawnCount(0);
  }, [photoId]);
  const drawingNow = drawing || drawMany;
  const [scanLevel, setScanLevel] = useState(3);
  const [scanning, setScanning] = useState(false);
  const [scanNote, setScanNote] = useState("");
  useEffect(() => setScanNote(""), [photoId]);
  const scan = async () => {
    setScanning(true);
    setScanNote("");
    setError("");
    try {
      const r = await api.scanFaces(photoId, scanLevel);
      setScanNote(
        r.rotated
          ? "The photo was rotated during the scan; nothing was saved. Try again."
          : r.found === 0
            ? `No faces found.${scanLevel < 5 ? " Try a higher sensitivity." : ""}`
            : `Found ${r.found} face(s): ${r.added} new${r.updated ? `, ${r.updated} already boxed` : ""}.`,
      );
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setScanning(false);
    }
  };
  const [drag, setDrag] = useState<{ start: [number, number]; box: Box } | null>(null);
  const [showIgnored, setShowIgnored] = useState(false);
  const [showBoxes, setShowBoxes] = useState(readShowBoxes);
  const [zoom, setZoom] = useState<number | null>(null); // context around the face, in face sizes; null = closed
  const [aspect, setAspect] = useState(1); // photo width / height, for the zoomed crop
  const [error, setError] = useState("");
  const frame = useRef<HTMLDivElement>(null);
  const touch = useTouch();

  const refresh = () => refreshFaces(qc);
  const act = async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
      setActive(null);
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  // Box drawing uses pointer events, so a mouse, a finger or a pen all draw. The frame captures the
  // pointer, so a drag that leaves the photo keeps drawing (clamped to the edge) until released.
  const point = (e: React.PointerEvent): [number, number] => {
    const r = frame.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const onDown = (e: React.PointerEvent) => {
    if (!drawingNow || !e.isPrimary) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = point(e);
    setDrag({ start: p, box: { x: p[0], y: p[1], w: 0, h: 0 } });
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag || !e.isPrimary) return;
    const [x, y] = point(e);
    const [sx, sy] = drag.start;
    setDrag({ ...drag, box: { x: Math.min(x, sx), y: Math.min(y, sy), w: Math.abs(x - sx), h: Math.abs(y - sy) } });
  };
  const onUp = async () => {
    if (!drag) return;
    const box = drag.box;
    setDrag(null);
    if (box.w < 0.01 || box.h < 0.01) return;
    if (drawMany) {
      // Keep drawing: no naming pop-up between boxes; name them afterwards from the list.
      await act(async () => {
        await api.drawFace(photoId, box);
        setDrawnCount((n) => n + 1);
      });
      return;
    }
    setDrawing(false);
    await act(async () => {
      const f = await api.drawFace(photoId, box);
      setTimeout(() => setActive(f.id), 0);
    });
  };

  const visible = (faces.data ?? []).filter((f) => showIgnored || !f.ignored);
  const current = visible.find((f) => f.id === active);
  const hidden = (faces.data ?? []).filter((f) => f.ignored).length;

  return (
    <div className="facelayer">
      <div
        ref={frame}
        className={`photo-frame ${drawingNow ? "is-drawing" : ""}`}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => setDrag(null)}
      >
        <img
          src={imgUrl(sha, "preview", rotation)}
          alt={alt}
          draggable={false}
          onLoad={(e) => e.currentTarget.naturalHeight && setAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
        />
        {showBoxes &&
          visible.map((f) => (
            <FaceBox key={f.id} face={f} active={f.id === active} onClick={() => setActive(f.id === active ? null : f.id)} />
          ))}
        {drag && <div className="facebox facebox--draft" style={pct(drag.box)} />}
      </div>
      <div className="facelayer__bar">
        <span className="muted small">
          {faces.data ? `${visible.filter((f) => !f.ignored).length} face(s)` : ""}
          {hidden > 0 && (
            <button className="linkish" onClick={() => setShowIgnored((v) => !v)}>
              {showIgnored ? "hide" : "show"} {hidden} ignored
            </button>
          )}
        </span>
        <span className="facelayer__tools">
          <button
            className="btn btn--ghost"
            aria-pressed={!showBoxes}
            onClick={() => {
              const next = !showBoxes;
              setShowBoxes(next);
              writeShowBoxes(next);
            }}
            title="Show or hide the boxes and names drawn on the photo"
          >
            {showBoxes ? "Hide face boxes" : "Show face boxes"}
          </button>
          <button
            className={`btn btn--ghost ${drawMany ? "is-on" : ""}`}
            disabled={drawing}
            onClick={() => {
              setDrawMany((m) => !m);
              setDrawnCount(0);
              setActive(null);
            }}
            title="Drag boxes around several missed faces, one after another; click again when done"
          >
            {drawMany ? `Done drawing${drawnCount ? ` (${drawnCount} added)` : ""}` : "＋ Draw several"}
          </button>
          <button className={`btn btn--ghost ${drawing ? "is-on" : ""}`} disabled={drawMany} onClick={() => setDrawing((d) => !d)} title="Drag a box around a face the detector missed">
            {drawing ? "Drag around the face…" : "＋ Draw face"}
          </button>
        </span>
      </div>
      <div className="facescan">
        <button className="btn btn--ghost" onClick={scan} disabled={scanning} title="Run face detection on this photo now; adds faces it finds, never removes any">
          {scanning ? "Scanning…" : "Find faces"}
        </button>
        <label className="facescan__level">
          <span className="muted small">Sensitivity</span>
          <input type="range" min={1} max={5} step={1} value={scanLevel} onChange={(e) => setScanLevel(Number(e.target.value))} aria-valuetext={SCAN_LEVELS[scanLevel]} />
          <span className="small">{SCAN_LEVELS[scanLevel]}</span>
        </label>
        {scanNote && <span className="muted small">{scanNote}</span>}
        {error && !current && <span className="error small">{error}</span>}
      </div>
      {visible.length > 0 && (
        <div className="facelist">
          <ul>
            {visible.map((f) => {
              const guess = guessName(f);
              return (
                <li key={f.id}>
                  <button className={`facelist__row ${f.id === active ? "is-active" : ""}`} onClick={() => setActive(f.id === active ? null : f.id)}>
                    <img src={faceUrl(f.id)} alt="" loading="lazy" />
                    {f.ignored ? (
                      <span className="muted">Ignored</span>
                    ) : guess ? (
                      <span>
                        {guess}?{matchPercent(f)} <span className="muted small">{f.source === "auto" && f.person_name ? "named by the app" : "suggested"}</span>
                      </span>
                    ) : f.person_name ? (
                      <span className="facelist__confirmed">{f.person_name}</span>
                    ) : (
                      <span className="muted">Unnamed</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {current && (
        <div className="facepop">
          <span className="facepop__who">
            {current.source === "auto" && current.person_name
              ? `Looks like ${current.person_name}${matchPercent(current)}`
              : current.suggested_name
                ? `Maybe ${current.suggested_name}?${matchPercent(current)}`
                : (current.person_name ?? "Unnamed face")}
          </span>
          {((current.source === "auto" && current.person_name) || current.suggested_name) && (
            <>
              <button className="btn btn--primary" onClick={() => act(() => api.acceptFaces([current.id]))}>
                Yes
              </button>
              <button className="btn btn--ghost" onClick={() => act(() => api.rejectFaces([current.id]))}>
                No
              </button>
            </>
          )}
          {/* On touch, don't pop the keyboard up over Yes / No on every tap of a face. */}
          <NameInput autoFocus={!touch} onSubmit={(name) => act(() => api.assignFaces([current.id], { name }))} placeholder={current.person_name ? "Rename to…" : "Who is this?"} />
          {current.person_name && current.source !== "auto" && (
            <button className="btn btn--ghost" onClick={() => act(() => api.rejectFaces([current.id]))}>
              Not {current.person_name}
            </button>
          )}
          {current.ignored ? (
            <button className="btn btn--ghost" onClick={() => act(() => api.ignoreFaces([current.id], false))}>
              Un-ignore
            </button>
          ) : (
            <button className="btn btn--ghost" onClick={() => act(() => api.ignoreFaces([current.id]))} title="Stranger, background person, or not a face">
              Ignore
            </button>
          )}
          {current.source === "drawn" && (
            <button className="btn btn--ghost" onClick={() => act(() => api.deleteFace(current.id))}>
              Delete box
            </button>
          )}
          <button className="btn btn--ghost" aria-pressed={zoom !== null} onClick={() => setZoom((z) => (z === null ? 3 : null))}>
            {zoom === null ? "Zoom in" : "Hide zoom"}
          </button>
          <button className="btn btn--ghost" onClick={() => setActive(null)}>
            Close
          </button>
          {error && <span className="error">{error}</span>}
          {zoom !== null && (
            <div className="facezoom">
              <FaceZoom src={imgUrl(sha, "preview", rotation)} face={current} aspect={aspect} context={zoom} />
              <span className="facezoom__controls">
                <button className="btn btn--ghost" onClick={() => setZoom((z) => Math.max(1.5, (z ?? 3) / 1.5))} disabled={zoom <= 1.5} title="Closer">
                  ＋ Closer
                </button>
                <button className="btn btn--ghost" onClick={() => setZoom((z) => Math.min(10, (z ?? 3) * 1.5))} disabled={zoom >= 10} title="Show more of the surroundings">
                  − Wider
                </button>
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function pct(b: Box): React.CSSProperties {
  return { left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` };
}

const SCAN_LEVELS: Record<number, string> = {
  1: "1 · normal",
  2: "2 · a little deeper",
  3: "3 · deeper",
  4: "4 · deep (as Deep face scan)",
  5: "5 · most aggressive (more false boxes)",
};

/** On-screen size of the face zoom: 240px, or 70% of a narrow screen. */
const zoomPx = () => Math.min(240, Math.round(window.innerWidth * 0.7));

/** The face and its surroundings, cropped from the photo: `context` face-sizes across, square, with
 *  the face's box outlined. Box coordinates are fractions of the (rotated) photo. */
function FaceZoom({ src, face, aspect, context }: { src: string; face: Face; aspect: number; context: number }) {
  // Work in units of the photo's height so the crop is square on screen.
  const W = aspect;
  const side = Math.min(Math.max(face.w * W, face.h) * context, W, 1);
  const rw = side / W;
  const rh = side;
  const rx = Math.min(Math.max(face.x + face.w / 2 - rw / 2, 0), 1 - rw);
  const ry = Math.min(Math.max(face.y + face.h / 2 - rh / 2, 0), 1 - rh);
  const px = zoomPx();
  const scale = px / side; // screen px per photo-height unit
  return (
    <div className="facezoom__view" style={{ width: px, height: px }}>
      <img
        src={src}
        alt=""
        draggable={false}
        style={{ width: W * scale, height: scale, left: -rx * W * scale, top: -ry * scale }}
      />
      <span
        className="facezoom__box"
        style={{ left: (face.x - rx) * W * scale, top: (face.y - ry) * scale, width: face.w * W * scale, height: face.h * scale }}
      />
    </div>
  );
}

/** The name the app guessed for this face (its own automatic name, or a suggestion), if any. */
/** " · 62%": how closely a guessed face matches the person (the match score), or "" if unknown. */
function matchPercent(face: Face): string {
  return face.confidence != null ? ` · ${Math.round(face.confidence * 100)}%` : "";
}

function guessName(face: Face): string | null {
  return (face.source === "auto" && face.person_name) || face.suggested_name || null;
}

function FaceBox({ face, active, onClick }: { face: Face; active: boolean; onClick: () => void }) {
  const guess = guessName(face);
  const cls = face.ignored ? "is-ignored" : guess ? "is-guess" : face.person_name ? "is-named" : "is-unnamed";
  return (
    <button
      className={`facebox ${cls} ${active ? "is-active" : ""}`}
      style={pct(face)}
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <span className="facebox__label">{guess ? `${guess}?${matchPercent(face)}` : (face.person_name ?? "?")}</span>
    </button>
  );
}
