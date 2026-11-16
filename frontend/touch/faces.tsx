import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, faceName, faceUrl, pairLabel, type Face } from "../src/api";
import { useRemembered } from "../src/hooks";
import { usePeople, useTwins, type Pair } from "../src/peopleData";
import { FaceZoom } from "../src/FaceZoom";
import { FACE_ZOOM, useSwitch } from "./settings";

/** The "Enable zoom" switch on a photo's Advanced tab. */
export const useFaceZoomSetting = () => useSwitch(FACE_ZOOM);

/** The photo a face is on, for zooming in on the face: its preview and width / height as shown. */
export type ZoomPhoto = { src: string; aspect: number };

/** Refetch everything a face change can touch (both apps keep these query keys). */
export function useRefreshFaces() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["clusters", "people", "person", "personFaces", "photoFaces", "faceStats", "faceReview", "edits", "photos", "photo"])
      qc.invalidateQueries({ queryKey: [k] });
  };
}

/** Run a face action, then refresh; errors come back as text. */
export function useFaceAction() {
  const refresh = useRefreshFaces();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return { run, busy, error };
}

/** How sure the app is, in words a first-time visitor reads at a glance (no percentages). */
export const sureness = (confidence: number | null | undefined) =>
  (confidence ?? 0) >= 0.8 ? "Probably" : (confidence ?? 0) >= 0.65 ? "Maybe" : "Not sure";

/** The guess as a question: "Probably Sam?", "Maybe Sam?", "Could this be Sam?". */
export const guessQuestion = (name: string, confidence: number | null | undefined) =>
  (confidence ?? 0) >= 0.65 ? `${sureness(confidence)} ${name}?` : `Could this be ${name}?`;

/** The app's guess for a face: its own automatic name, a suggestion, or "Ana or Ben?". */
export function guessOf(f: Face): { name: string; personId: number; pair: boolean } | null {
  if (f.source === "auto" && f.person_name && f.person_id) return { name: f.person_name, personId: f.person_id, pair: false };
  if (f.suggested_name && f.suggested_person_id)
    return f.suggested_alt_name
      ? { name: pairLabel(f.suggested_name, f.suggested_alt_name), personId: f.suggested_person_id, pair: true }
      : { name: f.suggested_name, personId: f.suggested_person_id, pair: false };
  return null;
}

/** A name box with the people (and known twin pairs) as suggestions. Enter or "Name" submits. */
export function NameField({ placeholder = "Who is this?", busy, onName, onPair, autoFocus, rememberAs }: {
  placeholder?: string;
  busy?: boolean;
  onName: (name: string) => void;
  onPair?: (pair: Pair) => void;
  autoFocus?: boolean;
  /** Keep a half-typed name under this key when the field goes away (another tab) and comes back. */
  rememberAs?: string;
}) {
  const people = usePeople();
  const { pairs } = useTwins();
  const [text, setText] = useRemembered(rememberAs ?? null, "");
  const typed = text.trim();
  const said = typed.toLowerCase();
  const pair = onPair ? pairs.find((p) => p.label.toLowerCase() === said || p.label.toLowerCase().split(" or ").reverse().join(" or ") === said) : undefined;
  const isNew = typed !== "" && !pair && !!people.data && !people.data.some((p) => p.name.toLowerCase() === said);
  const list = onPair ? "t-names-pairs" : "t-names";
  return (
    <form
      className="t-namefield"
      onSubmit={(e) => {
        e.preventDefault();
        if (!typed) return;
        if (pair && onPair) onPair(pair.pair);
        else onName(typed);
        setText("");
      }}
    >
      <input
        list={list}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoCorrect="off"
        autoComplete="off"
        spellCheck={false}
        autoCapitalize="words"
        enterKeyHint="done"
      />
      <datalist id={list}>
        {people.data?.map((p) => <option key={p.id} value={p.name} />)}
        {onPair && pairs.map((p) => <option key={p.label} value={p.label} />)}
      </datalist>
      <button className="t-btn t-btn--primary" disabled={busy || !typed}>
        Name
      </button>
      {isNew && <span className="t-namefield__hint">New person: {typed}</span>}
    </form>
  );
}

/** Every face in one photo, each with what you can do about it. `active` is highlighted on the photo. */
export function FacesPanel({ faces, active, onActive, drawn, drawing, onDraw, photo }: {
  faces: Face[];
  /** With "Enable zoom" on (Advanced tab), each naming box can zoom in on its face in this photo. */
  photo?: ZoomPhoto;
  active: number | null;
  onActive: (id: number | null) => void;
  /** A face just drawn: its row comes into view with the name box ready. */
  drawn?: number | null;
  drawing?: boolean;
  /** Start drawing a box around a face that wasn't found. */
  onDraw?: () => void;
}) {
  const [showIgnored, setShowIgnored] = useState(false);
  const [zoomOn] = useFaceZoomSetting();
  const visible = faces.filter((f) => showIgnored || !f.ignored);
  const ignored = faces.filter((f) => f.ignored).length;
  return (
    <div className="t-faces">
      {visible.length === 0 && <p className="t-muted">No faces found in this photo.</p>}
      {visible.map((f) => (
        <FaceRow key={f.id} face={f} active={f.id === active} justDrawn={f.id === drawn} onActive={() => onActive(f.id === active ? null : f.id)}
          zoomPhoto={zoomOn ? photo : undefined} />
      ))}
      {onDraw && (
        <button className="t-btn t-btn--block" disabled={drawing} onClick={onDraw}>
          {drawing ? "Drag a box around the face on the photo…" : "＋ Draw a face that wasn't found"}
        </button>
      )}
      {ignored > 0 && (
        <p className="t-muted t-small">
          <button className="t-link" onClick={() => setShowIgnored((s) => !s)}>
            {showIgnored ? "Hide" : "Show"} {ignored} ignored
          </button>
        </p>
      )}
    </div>
  );
}

function FaceRow({ face: f, active, justDrawn, onActive, zoomPhoto }: {
  face: Face;
  active: boolean;
  justDrawn?: boolean;
  onActive: () => void;
  zoomPhoto?: ZoomPhoto;
}) {
  const { run, busy, error } = useFaceAction();
  const row = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (justDrawn) row.current?.scrollIntoView({ block: "nearest" });
  }, [justDrawn]);
  const { twinsOf } = useTwins();
  const [renaming, setRenaming] = useState(false);
  const [orOpen, setOrOpen] = useState(false); // "one of two people, not sure which" (twins)
  const guess = guessOf(f);
  const named = f.person_name && f.source !== "auto";
  const assign = (who: Parameters<typeof api.assignFaces>[1]) => run(() => api.assignFaces([f.id], who)).then(() => setRenaming(false));
  // A face a person confirmed, of someone with no twin: settled, so it takes one short line. "Change"
  // opens the rest (Not …, Rename, Or…, Ignore). Twins keep the full card: which one is often in doubt.
  const [open, setOpen] = useState(false);
  const settled = !!named && !f.ignored && !f.alt_person_id && twinsOf(f.person_id).length === 0;
  if (settled && !open && !renaming)
    return (
      <div ref={row} className={`t-face t-face--compact ${active ? "is-active" : ""}`}>
        <button className="t-face__crop" onClick={onActive} aria-label={`Show ${f.person_name} on the photo`}>
          <img src={faceUrl(f.id)} alt="" loading="lazy" />
        </button>
        <span className="t-face__name">{faceName(f)}</span>
        <button className="t-btn t-face__change" onClick={() => setOpen(true)} aria-expanded={false} aria-label={`Change ${f.person_name}`}>
          Change
        </button>
      </div>
    );
  return (
    <div ref={row} className={`t-face ${active ? "is-active" : ""} ${f.ignored ? "is-ignored" : ""}`}>
      <button className="t-face__crop" onClick={onActive} aria-label="Show on the photo">
        <img src={faceUrl(f.id)} alt="" loading="lazy" />
      </button>
      <div className="t-face__body">
        {f.ignored ? (
          <>
            <span className="t-muted">Ignored</span>
            <button className="t-btn" disabled={busy} onClick={() => run(() => api.ignoreFaces([f.id], false))}>
              Un-ignore
            </button>
          </>
        ) : guess ? (
          <>
            <span className="t-face__q">{guessQuestion(guess.name, f.confidence)}</span>
            <div className="t-face__actions">
              {/* Two equal answers (neither looks already chosen): the pumpkin fill is for "do this". */}
              <button className="t-btn t-btn--answer" disabled={busy} onClick={() => run(() => api.acceptFaces([f.id]))}>
                ✓ Yes
              </button>
              <button className="t-btn t-btn--answer" disabled={busy} onClick={() => run(() => api.rejectFaces([f.id]))}>
                ✕ No
              </button>
              {!guess.pair &&
                twinsOf(guess.personId).map((t) => (
                  <button key={t.id} className="t-btn" disabled={busy} onClick={() => assign({ person_id: guess.personId, alt_person_id: t.id })}>
                    {pairLabel(guess.name, t.name)}
                  </button>
                ))}
            </div>
            <NameField placeholder="Or someone else…" busy={busy} onName={(name) => assign({ name })} onPair={assign}
              rememberAs={`t-face-or:${f.id}`} />
          </>
        ) : named && !renaming ? (
          <>
            <span className="t-face__name">{faceName(f)}</span>
            <div className="t-face__actions">
              {f.alt_person_id && f.person_id ? (
                <>
                  <button className="t-btn" disabled={busy} onClick={() => assign({ person_id: f.person_id! })}>
                    It's {f.person_name}
                  </button>
                  <button className="t-btn" disabled={busy} onClick={() => assign({ person_id: f.alt_person_id! })}>
                    It's {f.alt_name}
                  </button>
                  <button className="t-btn" disabled={busy} onClick={() => run(() => api.rejectFaces([f.id]))}>
                    Neither
                  </button>
                </>
              ) : (
                <>
                  {twinsOf(f.person_id).map((t) => (
                    <button key={t.id} className="t-btn" disabled={busy} onClick={() => assign({ person_id: f.person_id!, alt_person_id: t.id })}>
                      {pairLabel(f.person_name!, t.name)}
                    </button>
                  ))}
                  <button className="t-btn" disabled={busy} onClick={() => run(() => api.rejectFaces([f.id]))}>
                    Not {f.person_name}
                  </button>
                </>
              )}
              <button className="t-btn" onClick={() => setRenaming(true)}>
                Rename
              </button>
              {!f.alt_person_id && (
                <button className="t-btn" onClick={() => setOrOpen((o) => !o)} title="One of two people, not sure which (twins)">
                  Or…
                </button>
              )}
              {settled && (
                <button className="t-btn t-btn--plain" onClick={() => (setOpen(false), setOrOpen(false))} aria-expanded>
                  Done
                </button>
              )}
            </div>
            {orOpen && (
              <NameField
                autoFocus
                busy={busy}
                placeholder={`${f.person_name} or who?`}
                onName={(alt_name) => assign({ person_id: f.person_id!, alt_name }).then(() => setOrOpen(false))}
              />
            )}
          </>
        ) : (
          <NameField
            busy={busy}
            autoFocus={renaming || justDrawn}
            placeholder={renaming ? "Rename to…" : "Who is this?"}
            rememberAs={`t-face-name:${f.id}`}
            onName={(name) => assign({ name })}
            onPair={assign}
          />
        )}
        {zoomPhoto && !f.ignored && (guess || !named || renaming) && <FaceZoomControls face={f} photo={zoomPhoto} />}
        {f.source === "drawn" ? (
          // A box you drew: take it away (Undo brings it back).
          <button className="t-link t-small" disabled={busy} onClick={() => run(() => api.deleteFace(f.id))}>
            Remove this box
          </button>
        ) : !f.ignored && (
          <button className="t-link t-small" disabled={busy} onClick={() => run(() => api.ignoreFaces([f.id]))}>
            Ignore (stranger or not a face)
          </button>
        )}
        {error && <p className="t-error">{error}</p>}
      </div>
    </div>
  );
}

/** "Zoom in" under a naming box: the face and its surroundings, bigger, with Closer / Wider. */
function FaceZoomControls({ face, photo }: { face: Face; photo: ZoomPhoto }) {
  const [context, setContext] = useState<number | null>(null); // face-sizes across; null = closed
  // As wide as the row has room for (a phone's sheet or an iPad's side panel), at most 240px.
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const fit = () => setSize(Math.min(240, el.clientWidth));
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [context === null]);
  if (context === null)
    return (
      <button className="t-btn" onClick={() => setContext(3)}>
        Zoom in
      </button>
    );
  return (
    <div className="t-facezoom" ref={box}>
      {size > 0 && <FaceZoom src={photo.src} face={face} aspect={photo.aspect} context={context} size={size} />}
      <div className="t-face__actions">
        <button className="t-btn" onClick={() => setContext((c) => Math.max(1.5, (c ?? 3) / 1.5))} disabled={context <= 1.5}>
          ＋ Closer
        </button>
        <button className="t-btn" onClick={() => setContext((c) => Math.min(10, (c ?? 3) * 1.5))} disabled={context >= 10}>
          − Wider
        </button>
        <button className="t-btn t-btn--plain" onClick={() => setContext(null)}>
          Hide zoom
        </button>
      </div>
    </div>
  );
}
