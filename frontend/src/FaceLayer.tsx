import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, imgUrl, type Face } from "./api";
import { NameInput } from "./People";

type Box = { x: number; y: number; w: number; h: number };

/** Front image with face boxes: click a box to name it, or draw a box around a missed face. */
export function FaceLayer({ photoId, sha, rotation, alt }: { photoId: number; sha: string; rotation: number; alt: string }) {
  const qc = useQueryClient();
  const faces = useQuery({ queryKey: ["photoFaces", photoId], queryFn: () => api.photoFaces(photoId) });
  const [active, setActive] = useState<number | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [drag, setDrag] = useState<{ start: [number, number]; box: Box } | null>(null);
  const [showIgnored, setShowIgnored] = useState(false);
  const [error, setError] = useState("");
  const frame = useRef<HTMLDivElement>(null);

  const refresh = () => {
    for (const k of ["photoFaces", "clusters", "people", "personFaces", "faceStats", "edits"]) qc.invalidateQueries({ queryKey: [k] });
  };
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

  const point = (e: React.MouseEvent): [number, number] => {
    const r = frame.current!.getBoundingClientRect();
    return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
  };
  const onDown = (e: React.MouseEvent) => {
    if (!drawing) return;
    e.preventDefault();
    const p = point(e);
    setDrag({ start: p, box: { x: p[0], y: p[1], w: 0, h: 0 } });
  };
  const onMove = (e: React.MouseEvent) => {
    if (!drag) return;
    const [x, y] = point(e);
    const [sx, sy] = drag.start;
    setDrag({ ...drag, box: { x: Math.min(x, sx), y: Math.min(y, sy), w: Math.abs(x - sx), h: Math.abs(y - sy) } });
  };
  const onUp = async () => {
    if (!drag) return;
    const box = drag.box;
    setDrag(null);
    if (box.w < 0.01 || box.h < 0.01) return;
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
        className={`photo-frame ${drawing ? "is-drawing" : ""}`}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={onUp}
        onMouseLeave={() => setDrag(null)}
      >
        <img src={imgUrl(sha, "preview", rotation)} alt={alt} draggable={false} />
        {visible.map((f) => (
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
        <button className={`btn btn--ghost ${drawing ? "is-on" : ""}`} onClick={() => setDrawing((d) => !d)} title="Drag a box around a face the detector missed">
          {drawing ? "Drag around the face…" : "＋ Draw face"}
        </button>
      </div>
      {current && (
        <div className="facepop">
          <span className="facepop__who">
            {current.source === "auto" && current.person_name
              ? `Looks like ${current.person_name}`
              : current.suggested_name
                ? `Maybe ${current.suggested_name}?`
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
          <NameInput autoFocus onSubmit={(name) => act(() => api.assignFaces([current.id], { name }))} placeholder={current.person_name ? "Rename to…" : "Who is this?"} />
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
          <button className="btn btn--ghost" onClick={() => setActive(null)}>
            Close
          </button>
          {error && <span className="error">{error}</span>}
        </div>
      )}
    </div>
  );
}

function pct(b: Box): React.CSSProperties {
  return { left: `${b.x * 100}%`, top: `${b.y * 100}%`, width: `${b.w * 100}%`, height: `${b.h * 100}%` };
}

function FaceBox({ face, active, onClick }: { face: Face; active: boolean; onClick: () => void }) {
  const guess = (face.source === "auto" && face.person_name) || face.suggested_name;
  const cls = face.ignored ? "is-ignored" : guess ? "is-guess" : face.person_name ? "is-named" : "is-unnamed";
  return (
    <button
      className={`facebox ${cls} ${active ? "is-active" : ""}`}
      style={pct(face)}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <span className="facebox__label">{guess ? `${guess}?` : (face.person_name ?? "?")}</span>
    </button>
  );
}
