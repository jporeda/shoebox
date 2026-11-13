import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, faceUrl, type Cluster } from "../../src/api";
import { useLongPress } from "../../src/hooks";
import { usePeople, useTwins } from "../../src/peopleData";
import { NameField, useFaceAction } from "../faces";
import { go, photosHref, viewerHref } from "../route";
import { plural } from "../ui";

/** Groups of unnamed faces to name (name box first, faces below), then everyone named so far. */
export function PeopleScreen() {
  const clusters = useQuery({ queryKey: ["clusters", false], queryFn: () => api.clusters(false) });
  const singles = useQuery({ queryKey: ["clusters", true], queryFn: () => api.clusters(true) });
  const people = usePeople();
  const { twinsOf } = useTwins();
  const [showSingles, setShowSingles] = useState(false);
  const groups = clusters.data ?? [];
  const loners = singles.data?.[0];
  if (clusters.isLoading || people.isLoading) return <p className="t-empty">Loading…</p>;
  return (
    <div className="t-people">
      {(groups.length > 0 || loners) && (
        <section className="t-section">
          <h2 className="t-h2">To name</h2>
          {groups.length === 0 && <p className="t-muted">No groups left to name.</p>}
          <div className="t-cards">
            {groups.map((c) => <NameGroup key={c.cluster_id} cluster={c} />)}
          </div>
          {loners && loners.faces.length > 0 && (
            <>
              <button className="t-btn t-btn--block" onClick={() => setShowSingles((s) => !s)}>
                {showSingles ? "Hide" : "Show"} {plural(loners.size, "face")} that didn't group with anyone
              </button>
              {showSingles && <NameGroup cluster={loners} loners />}
            </>
          )}
        </section>
      )}
      <section className="t-section">
        <h2 className="t-h2">Everyone named</h2>
        {people.data?.length === 0 && <p className="t-muted">Nobody named yet.</p>}
        <div className="t-persons">
          {people.data?.map((p) => (
            <a key={p.id} className="t-person" href={photosHref({ person_id: p.id, order: "chrono" })}>
              {p.cover_face_id ? <img src={faceUrl(p.cover_face_id)} alt="" loading="lazy" /> : <span className="t-person__blank" />}
              <span className="t-person__name">{p.name}</span>
              <span className="t-muted t-small">
                {plural(p.photo_count ?? p.face_count, "photo")}
                {p.first_year ? ` · ${p.first_year}${p.last_year !== p.first_year ? `–${p.last_year}` : ""}` : ""}
                {twinsOf(p.id).length > 0 && ` · twin of ${twinsOf(p.id).map((t) => t.name).join(", ")}`}
                {p.kind === "pet" && " · pet"}
              </span>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}

/** One group of look-alike faces: the name box on top; tap a face to leave it out, press and hold
 *  to open its photo. */
function NameGroup({ cluster, loners }: { cluster: Cluster; loners?: boolean }) {
  const { run, busy, error } = useFaceAction();
  const [out, setOut] = useState<Set<number>>(new Set());
  // Loners aren't one person: you pick the ones you're naming instead of the ones to leave out.
  const chosen = loners ? cluster.faces.filter((f) => out.has(f.id)).map((f) => f.id) : cluster.member_ids.filter((id) => !out.has(id));
  const toggle = (id: number) => setOut((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const assign = (who: Parameters<typeof api.assignFaces>[1]) => run(() => api.assignFaces(chosen, who)).then(() => setOut(new Set()));
  return (
    <div className="t-card t-group">
      <div className="t-group__head">
        <span className="t-muted">
          {loners ? (chosen.length ? `${chosen.length} chosen` : "Tap the faces to name") : `${chosen.length} of ${cluster.size} faces`}
        </span>
        {!loners && cluster.suggestion && (
          <button className="t-chip is-on" disabled={busy || !chosen.length} onClick={() => assign({ person_id: cluster.suggestion!.person_id })}>
            {cluster.suggestion.name}? {Math.round(cluster.suggestion.similarity * 100)}%
          </button>
        )}
      </div>
      <NameField busy={busy || !chosen.length} onName={(name) => assign({ name })} onPair={assign} />
      <div className="t-group__faces">
        {cluster.faces.map((f) => (
          <GroupFace
            key={f.id}
            src={faceUrl(f.id)}
            caption={f.year_label}
            dim={loners ? !out.has(f.id) : out.has(f.id)}
            onTap={() => toggle(f.id)}
            onHold={() => go(viewerHref(f.photo_id, { ids: String(f.photo_id) }))}
          />
        ))}
        {!loners && cluster.size > cluster.faces.length && <span className="t-muted t-small">+{cluster.size - cluster.faces.length} more, named with them</span>}
      </div>
      <button className="t-link t-small" disabled={busy || !chosen.length} onClick={() => run(() => api.ignoreFaces(chosen))}>
        Ignore {loners ? "these" : "this group"} (strangers, not faces)
      </button>
      {error && <p className="t-error">{error}</p>}
    </div>
  );
}

function GroupFace({ src, caption, dim, onTap, onHold }: { src: string; caption: string; dim: boolean; onTap: () => void; onHold: () => void }) {
  const press = useLongPress(onHold, undefined, { anyPointer: true });
  return (
    <button className={`t-gface ${dim ? "is-dim" : ""}`} {...press.handlers} onClick={() => !press.consumeLongPress() && onTap()}>
      <img src={src} alt="" loading="lazy" />
      {caption && <span>{caption}</span>}
    </button>
  );
}
