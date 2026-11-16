import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, faceUrl, type Cluster } from "../../src/api";
import { useLongPress, useRemembered } from "../../src/hooks";
import { usePeople, useTwins } from "../../src/peopleData";
import { NameField, useFaceAction } from "../faces";
import { openPhoto, personHref, pushScreen, replaceScreen } from "../route";
import { Placeholder, plural } from "../ui";

/** "To name": groups of unnamed faces (name box first, faces below). "Everyone": each person named. */
export function PeopleScreen({ tab }: { tab: "name" | "everyone" }) {
  const clusters = useQuery({ queryKey: ["clusters", false], queryFn: () => api.clusters(false) });
  const singles = useQuery({ queryKey: ["clusters", true], queryFn: () => api.clusters(true) });
  const people = usePeople();
  const groups = clusters.data ?? [];
  const toName = groups.reduce((n, c) => n + c.size, 0) + (singles.data?.[0]?.size ?? 0);
  return (
    <div className="t-people">
      <div className="t-seg t-people__tabs" role="tablist">
        {/* Replacing the entry: switching between the two isn't something Back should step through. */}
        <button role="tab" aria-selected={tab === "name"} className={tab === "name" ? "is-on" : ""}
          onClick={() => replaceScreen("#/people")}>
          To name{clusters.data ? ` (${toName})` : ""}
        </button>
        <button role="tab" aria-selected={tab === "everyone"} className={tab === "everyone" ? "is-on" : ""}
          onClick={() => replaceScreen("#/people?tab=everyone")}>
          Everyone{people.data ? ` (${people.data.length})` : ""}
        </button>
      </div>
      {tab === "name" ? <ToName groups={groups} loners={singles.data?.[0]} loading={clusters.isLoading} /> : <Everyone />}
    </div>
  );
}

function ToName({ groups, loners, loading }: { groups: Cluster[]; loners?: Cluster; loading: boolean }) {
  const [showSingles, setShowSingles] = useState(false);
  if (loading) return <Placeholder kind="cards" />;
  return (
    <section className="t-section">
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
  );
}

function Everyone() {
  const people = usePeople();
  const { twinsOf } = useTwins();
  if (people.isLoading) return <Placeholder kind="cards" />;
  return (
    <section className="t-section">
      {people.data?.length === 0 && <p className="t-muted">Nobody named yet.</p>}
      <div className="t-persons">
        {people.data?.map((p) => (
          <a key={p.id} className="t-person" href={personHref(p.id)} onClick={(e) => (e.preventDefault(), pushScreen(personHref(p.id)))}>
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
  );
}

/** One group of look-alike faces: the name box on top; tap a face to leave it out, "Photos ›" (or
 *  press and hold a face) to see them in their photos. */
function NameGroup({ cluster, loners }: { cluster: Cluster; loners?: boolean }) {
  const { run, busy, error } = useFaceAction();
  // Kept when you switch tabs and come back, like the half-typed name below.
  const [out, setOut] = useRemembered(`t-out:${loners ? "loners" : cluster.cluster_id}`, new Set<number>());
  // Loners aren't one person: you pick the ones you're naming instead of the ones to leave out.
  const chosen = loners ? cluster.faces.filter((f) => out.has(f.id)).map((f) => f.id) : cluster.member_ids.filter((id) => !out.has(id));
  const toggle = (id: number) => setOut((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const assign = (who: Parameters<typeof api.assignFaces>[1]) => run(() => api.assignFaces(chosen, who)).then(() => setOut(new Set()));
  const photoIds = [...new Set(cluster.faces.map((f) => f.photo_id))];
  const look = (photoId: number) => openPhoto({ id: photoId, ids: photoIds, faces: cluster.faces.map((f) => f.id) });
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
        <button className="t-btn t-photos-btn" onClick={() => look(photoIds[0])}>
          Photos ›
        </button>
      </div>
      <NameField busy={busy || !chosen.length} onName={(name) => assign({ name })} onPair={assign}
        rememberAs={`t-name:${loners ? "loners" : cluster.cluster_id}`} />
      <div className="t-group__faces">
        {cluster.faces.map((f) => (
          <GroupFace
            key={f.id}
            src={faceUrl(f.id)}
            caption={f.year_label}
            dim={loners ? !out.has(f.id) : out.has(f.id)}
            onTap={() => toggle(f.id)}
            onHold={() => look(f.photo_id)}
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
