import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, faceName, faceUrl, pairLabel, type Face } from "../src/api";
import { usePeople, useTwins, type Pair } from "../src/peopleData";

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
export function NameField({ placeholder = "Who is this?", busy, onName, onPair, autoFocus }: {
  placeholder?: string;
  busy?: boolean;
  onName: (name: string) => void;
  onPair?: (pair: Pair) => void;
  autoFocus?: boolean;
}) {
  const people = usePeople();
  const { pairs } = useTwins();
  const [text, setText] = useState("");
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
export function FacesPanel({ faces, active, onActive, desktopHref }: {
  faces: Face[];
  active: number | null;
  onActive: (id: number | null) => void;
  desktopHref: string;
}) {
  const [showIgnored, setShowIgnored] = useState(false);
  const visible = faces.filter((f) => showIgnored || !f.ignored);
  const ignored = faces.filter((f) => f.ignored).length;
  return (
    <div className="t-faces">
      {visible.length === 0 && <p className="t-muted">No faces found in this photo.</p>}
      {visible.map((f) => (
        <FaceRow key={f.id} face={f} active={f.id === active} onActive={() => onActive(f.id === active ? null : f.id)} />
      ))}
      <p className="t-muted t-small">
        {ignored > 0 && (
          <button className="t-link" onClick={() => setShowIgnored((s) => !s)}>
            {showIgnored ? "Hide" : "Show"} {ignored} ignored ·{" "}
          </button>
        )}
        A face wasn't found? Draw it on the <a href={desktopHref}>desktop site</a>.
      </p>
    </div>
  );
}

function FaceRow({ face: f, active, onActive }: { face: Face; active: boolean; onActive: () => void }) {
  const { run, busy, error } = useFaceAction();
  const { twinsOf } = useTwins();
  const [renaming, setRenaming] = useState(false);
  const [orOpen, setOrOpen] = useState(false); // "one of two people, not sure which" (twins)
  const guess = guessOf(f);
  const named = f.person_name && f.source !== "auto";
  const assign = (who: Parameters<typeof api.assignFaces>[1]) => run(() => api.assignFaces([f.id], who)).then(() => setRenaming(false));
  return (
    <div className={`t-face ${active ? "is-active" : ""} ${f.ignored ? "is-ignored" : ""}`}>
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
            <span className="t-face__q">
              {guess.name}? <span className="t-muted">{Math.round((f.confidence ?? 0) * 100)}% match</span>
            </span>
            <div className="t-face__actions">
              <button className="t-btn t-btn--primary" disabled={busy} onClick={() => run(() => api.acceptFaces([f.id]))}>
                Yes
              </button>
              <button className="t-btn" disabled={busy} onClick={() => run(() => api.rejectFaces([f.id]))}>
                No
              </button>
              {!guess.pair &&
                twinsOf(guess.personId).map((t) => (
                  <button key={t.id} className="t-btn" disabled={busy} onClick={() => assign({ person_id: guess.personId, alt_person_id: t.id })}>
                    {pairLabel(guess.name, t.name)}
                  </button>
                ))}
            </div>
            <NameField placeholder="Or someone else…" busy={busy} onName={(name) => assign({ name })} onPair={assign} />
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
            autoFocus={renaming}
            placeholder={renaming ? "Rename to…" : "Who is this?"}
            onName={(name) => assign({ name })}
            onPair={assign}
          />
        )}
        {!f.ignored && (
          <button className="t-link t-small" disabled={busy} onClick={() => run(() => api.ignoreFaces([f.id]))}>
            Ignore (stranger or not a face)
          </button>
        )}
        {error && <p className="t-error">{error}</p>}
      </div>
    </div>
  );
}
