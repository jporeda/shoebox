import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, faceUrl, type Cluster, type Face, type Person, type ReviewGroup } from "./api";
import { gridHref, navigate, photoHref } from "./route";

export const usePeople = () => useQuery({ queryKey: ["people"], queryFn: api.people });

function useInvalidateFaces() {
  const qc = useQueryClient();
  return () => {
    for (const k of ["clusters", "people", "person", "personFaces", "photoFaces", "faceStats", "faceReview", "edits", "photos"])
      qc.invalidateQueries({ queryKey: [k] });
  };
}

/** Name box with autocomplete over existing people; Enter submits. */
export function NameInput({
  onSubmit,
  placeholder = "Who is this?",
  autoFocus,
  busy,
}: {
  onSubmit: (name: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  busy?: boolean;
}) {
  const people = usePeople();
  const [name, setName] = useState("");
  return (
    <form
      className="field__inline"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) {
          onSubmit(name.trim());
          setName("");
        }
      }}
    >
      <input
        list="people-names"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={placeholder}
        autoFocus={autoFocus}
      />
      <datalist id="people-names">
        {people.data?.map((p) => <option key={p.id} value={p.name} />)}
      </datalist>
      <button className="btn btn--primary" type="submit" disabled={busy || !name.trim()}>
        Name
      </button>
    </form>
  );
}

export function FaceThumb({
  face,
  selected,
  dimmed,
  onClick,
  caption,
}: {
  face: Face;
  selected?: boolean;
  dimmed?: boolean;
  onClick?: (e: React.MouseEvent) => void;
  caption?: string;
}) {
  return (
    <button
      className={`face ${selected ? "is-selected" : ""} ${dimmed ? "is-dimmed" : ""}`}
      onClick={onClick}
      title={face.person_name ?? (face.est_age != null ? `age ≈${Math.round(face.est_age)}` : "")}
    >
      <img src={faceUrl(face.id)} alt="" loading="lazy" />
      {caption !== undefined && <span className="face__cap">{caption}</span>}
    </button>
  );
}

export function PeoplePage({ tab }: { tab: "name" | "check" | "people" }) {
  const stats = useQuery({ queryKey: ["faceStats"], queryFn: api.faceStats });
  const s = stats.data;
  return (
    <div className="people pad">
      <div className="people__head">
        <div className="segmented segmented--inline">
          <button className={tab === "name" ? "is-active" : ""} onClick={() => navigate("#/people")}>
            To name {s ? `(${s.unnamed_in_clusters + s.unnamed_single})` : ""}
          </button>
          <button className={tab === "check" ? "is-active" : ""} onClick={() => navigate("#/people?tab=check")}>
            Check {s ? `(${s.to_review})` : ""}
          </button>
          <button className={tab === "people" ? "is-active" : ""} onClick={() => navigate("#/people?tab=people")}>
            People {s ? `(${s.people})` : ""}
          </button>
        </div>
        {s && (
          <span className="muted">
            {s.faces} faces found · {s.named} named · {s.ignored} ignored
          </span>
        )}
      </div>
      {tab === "name" ? <ToName /> : tab === "check" ? <Check /> : <PeopleList />}
    </div>
  );
}

function ToName() {
  const clusters = useQuery({ queryKey: ["clusters", false], queryFn: () => api.clusters(false) });
  const [showSingles, setShowSingles] = useState(false);
  if (clusters.isLoading) return <p className="muted">Loading…</p>;
  return (
    <div className="clusters">
      <p className="muted small">
        Faces that look alike are grouped. Click faces to leave them out, then type a name (or pick a suggestion). Children
        often split into several groups by age; give each the same name. Undo with ⌘Z.
      </p>
      {clusters.data?.length === 0 && <p className="empty">No unnamed groups left.</p>}
      {clusters.data?.map((c) => <ClusterCard key={c.cluster_id} cluster={c} />)}
      <button className="btn btn--ghost" onClick={() => setShowSingles((v) => !v)}>
        {showSingles ? "Hide" : "Show"} faces that didn't group with anyone
      </button>
      {showSingles && <Singles />}
    </div>
  );
}

function Check() {
  const review = useQuery({ queryKey: ["faceReview"], queryFn: api.faceReview });
  if (review.isLoading) return <p className="muted">Loading…</p>;
  return (
    <div className="clusters">
      <p className="muted small">
        Faces the app thinks it recognizes, by person, most confident first. <b>?</b> marks names it applied itself;{" "}
        <i>maybe</i> marks suggestions it wasn't sure enough to apply (and every face that looks under 6). Click the wrong
        ones, then Confirm: the rest become confirmed and teach the matcher; the clicked ones are remembered as “not them”.
      </p>
      {review.data?.length === 0 && <p className="empty">Nothing to check. Name more faces, or run “Infer dates & names”.</p>}
      {review.data?.map((g) => <CheckGroup key={g.person_id} group={g} />)}
    </div>
  );
}

function CheckGroup({ group }: { group: ReviewGroup }) {
  const invalidate = useInvalidateFaces();
  const qc = useQueryClient();
  const [wrong, setWrong] = useState<Set<number>>(new Set());
  const run = useMutation({
    mutationFn: async () => {
      const bad = group.faces.filter((f) => wrong.has(f.id)).map((f) => f.id);
      const good = group.faces.filter((f) => !wrong.has(f.id)).map((f) => f.id);
      if (bad.length) await api.rejectFaces(bad);
      if (good.length) await api.acceptFaces(good);
    },
    onSuccess: () => {
      setWrong(new Set());
      invalidate();
      qc.invalidateQueries({ queryKey: ["faceReview"] });
    },
  });
  return (
    <section className="cluster">
      <div className="cluster__actions">
        <a className="person-link" href={`#/people/${group.person_id}`}>
          {group.name}
        </a>
        <span className="muted small">
          {group.faces.length - wrong.size} of {group.faces.length} look right
        </span>
        <button className="btn btn--primary" onClick={() => run.mutate()} disabled={run.isPending}>
          Confirm
        </button>
      </div>
      <div className="cluster__faces">
        {group.faces.map((f) => (
          <FaceThumb
            key={f.id}
            face={f}
            dimmed={wrong.has(f.id)}
            caption={`${f.source === "auto" && f.person_id ? "?" : "maybe"} ${Math.round((f.confidence ?? 0) * 100)}% ${f.year_label}`}
            onClick={() =>
              setWrong((prev) => {
                const next = new Set(prev);
                next.has(f.id) ? next.delete(f.id) : next.add(f.id);
                return next;
              })
            }
          />
        ))}
      </div>
    </section>
  );
}

function ClusterCard({ cluster }: { cluster: Cluster }) {
  const invalidate = useInvalidateFaces();
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [all, setAll] = useState<Face[] | null>(null);
  const [error, setError] = useState("");
  const faces = all ?? cluster.faces;
  // Hidden members of a big group go with the name. Use the ids listed with this group,
  // not a fresh fetch by cluster id: regrouping renumbers clusters.
  const loadAll = async () => {
    const members = new Set(cluster.member_ids);
    setAll((await api.clusterFaces(cluster.cluster_id)).filter((f) => members.has(f.id)));
  };

  const run = useMutation({
    mutationFn: async (action: { name?: string; person_id?: number; ignore?: boolean }) => {
      const ids = cluster.member_ids.filter((id) => !excluded.has(id));
      if (action.ignore) return api.ignoreFaces(ids);
      return api.assignFaces(ids, action.person_id ? { person_id: action.person_id } : { name: action.name });
    },
    onSuccess: invalidate,
    onError: (e) => setError(e instanceof ApiError ? e.message : String(e)),
  });

  const toggle = (id: number) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <section className="cluster">
      <div className="cluster__faces">
        {faces.map((f) => (
          <FaceThumb key={f.id} face={f} dimmed={excluded.has(f.id)} onClick={() => toggle(f.id)} caption={f.year_label || undefined} />
        ))}
        {!all && cluster.size > cluster.faces.length && (
          <button className="btn btn--ghost" onClick={loadAll}>
            +{cluster.size - cluster.faces.length} more
          </button>
        )}
      </div>
      <div className="cluster__actions">
        <span className="muted small">
          {cluster.size - excluded.size} of {cluster.size} faces
        </span>
        {cluster.suggestion && (
          <button className="chip" onClick={() => run.mutate({ person_id: cluster.suggestion!.person_id })} disabled={run.isPending}>
            {cluster.suggestion.name}? <span className="muted">{Math.round(cluster.suggestion.similarity * 100)}%</span>
          </button>
        )}
        <NameInput onSubmit={(name) => run.mutate({ name })} busy={run.isPending} />
        <button className="btn btn--ghost" onClick={() => run.mutate({ ignore: true })} disabled={run.isPending} title="Strangers, background people, not faces">
          Ignore
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </section>
  );
}

function Singles() {
  const singles = useQuery({ queryKey: ["clusters", true], queryFn: () => api.clusters(true) });
  const invalidate = useInvalidateFaces();
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const faces = singles.data?.[0]?.faces ?? [];
  const act = async (fn: () => Promise<unknown>) => {
    await fn();
    setSelected(new Set());
    invalidate();
  };
  return (
    <section className="cluster">
      <div className="cluster__faces">
        {faces.map((f) => (
          <FaceThumb
            key={f.id}
            face={f}
            selected={selected.has(f.id)}
            onClick={() =>
              setSelected((prev) => {
                const next = new Set(prev);
                next.has(f.id) ? next.delete(f.id) : next.add(f.id);
                return next;
              })
            }
            caption={f.year_label || undefined}
          />
        ))}
        {faces.length === 0 && <span className="muted">None.</span>}
      </div>
      {selected.size > 0 && (
        <div className="cluster__actions">
          <span className="muted small">{selected.size} selected</span>
          <NameInput onSubmit={(name) => act(() => api.assignFaces([...selected], { name }))} />
          <button className="btn btn--ghost" onClick={() => act(() => api.ignoreFaces([...selected]))}>
            Ignore
          </button>
        </div>
      )}
    </section>
  );
}

function PeopleList() {
  const people = usePeople();
  if (people.isLoading) return <p className="muted">Loading…</p>;
  if (!people.data?.length) return <p className="empty">Nobody named yet. Start in “To name”.</p>;
  return (
    <div className="person-grid">
      {people.data.map((p) => (
        <a key={p.id} className="person-card" href={`#/people/${p.id}`}>
          {p.cover_face_id ? <img src={faceUrl(p.cover_face_id)} alt="" /> : <span className="person-card__blank" />}
          <span className="person-card__name">{p.name}</span>
          <span className="muted small">
            {p.face_count - p.auto_count} photos{p.auto_count > 0 && ` (+${p.auto_count} to check)`}
            {p.first_year && ` · ${p.first_year}${p.last_year !== p.first_year ? `–${p.last_year}` : ""}`}
            {p.birth_year && ` · b. ${p.birth_year}`}
          </span>
        </a>
      ))}
    </div>
  );
}

export function PersonPage({ id }: { id: number }) {
  const qc = useQueryClient();
  const invalidate = useInvalidateFaces();
  const people = usePeople();
  const person = people.data?.find((p) => p.id === id);
  const faces = useQuery({ queryKey: ["personFaces", id], queryFn: () => api.personFaces(id) });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [error, setError] = useState("");
  useEffect(() => setSelected(new Set()), [id]);

  const save = async (body: Parameters<typeof api.updatePerson>[1]) => {
    setError("");
    try {
      await api.updatePerson(id, body);
      qc.invalidateQueries({ queryKey: ["people"] });
      qc.invalidateQueries({ queryKey: ["edits"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  const act = async (fn: () => Promise<unknown>) => {
    await fn();
    setSelected(new Set());
    invalidate();
  };

  if (!person) return <div className="pad muted">{people.isLoading ? "Loading…" : "Person not found."}</div>;
  return (
    <div className="people pad">
      <a className="btn btn--ghost" href="#/people?tab=people">
        ← People
      </a>
      <div className="person-head">
        <input
          className="person-head__name"
          key={person.name}
          defaultValue={person.name}
          onBlur={(e) => e.target.value.trim() && e.target.value.trim() !== person.name && save({ name: e.target.value.trim() })}
        />
        <label className="field">
          <span className="field__label">Birth year (dates photos from their age)</span>
          <input
            key={String(person.birth_year)}
            type="number"
            defaultValue={person.birth_year ?? ""}
            placeholder="e.g. 1972"
            onBlur={(e) => {
              const v = e.target.value ? Number(e.target.value) : null;
              if (v !== person.birth_year) save({ birth_year: v });
            }}
          />
        </label>
        <label className="field">
          <span className="field__label">Same person as…</span>
          <select
            value=""
            onChange={async (e) => {
              const into = Number(e.target.value);
              const target = people.data?.find((p) => p.id === into);
              if (target && window.confirm(`Move all of ${person.name}'s faces to ${target.name}?`)) {
                await api.mergePerson(id, into);
                invalidate();
                navigate(`#/people/${into}`);
              }
            }}
          >
            <option value="">Merge into…</option>
            {people.data?.filter((p) => p.id !== id).map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <a className="btn btn--ghost" href={gridHref({ person_id: id, order: "chrono" })}>
          Show {person.face_count} photos →
        </a>
        {error && <span className="error">{error}</span>}
      </div>
      <p className="muted small">
        Oldest to newest. A face that doesn't belong stands out here; select it and remove it. Double-click opens the photo.
      </p>
      <div className="strip">
        {faces.data?.map((f) => (
          <span key={f.id} onDoubleClick={() => navigate(photoHref(f.photo_id, { person_id: id, order: "chrono" }))}>
            <FaceThumb
              face={f}
              selected={selected.has(f.id)}
              caption={`${f.source === "auto" ? "? " : ""}${f.year_label || "undated"}`}
              onClick={() =>
                setSelected((prev) => {
                  const next = new Set(prev);
                  next.has(f.id) ? next.delete(f.id) : next.add(f.id);
                  return next;
                })
              }
            />
          </span>
        ))}
      </div>
      {selected.size > 0 && (
        <div className="bulkbar bulkbar--static">
          <span className="muted small">{selected.size} selected</span>
          <button className="btn btn--ghost" onClick={() => act(() => api.acceptFaces([...selected]))}>
            Confirm
          </button>
          <button className="btn btn--ghost" onClick={() => act(() => api.rejectFaces([...selected]))}>
            Not {person.name}
          </button>
          <NameInput placeholder="Move to…" onSubmit={(name) => act(() => api.assignFaces([...selected], { name }))} />
          <button className="btn btn--ghost" onClick={() => act(() => api.ignoreFaces([...selected]))}>
            Ignore
          </button>
        </div>
      )}
    </div>
  );
}

export type { Person };
