import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, faceUrl, pairLabel, type ReviewGroup } from "../../src/api";
import { useLongPress, useRemembered } from "../../src/hooks";
import { useTwins, type Pair } from "../../src/peopleData";
import { sureness, useFaceAction } from "../faces";
import { go, openPhoto } from "../route";
import { Done, Placeholder } from "../ui";

/** The app's name guesses, by person and age bracket: tap the wrong ones, then Confirm. */
export function CheckScreen() {
  const review = useQuery({ queryKey: ["faceReview"], queryFn: api.faceReview });
  const [help, setHelp] = useState(false);
  return (
    <div className="t-check">
      <div className="t-check__intro">
        <span className="t-muted">Tap the faces that are wrong, then Confirm the rest.</span>
        <button className="t-btn t-btn--icon" aria-expanded={help} aria-label="More about checking" onClick={() => setHelp((h) => !h)}>
          ?
        </button>
      </div>
      {help && (
        <p className="t-muted t-small t-pad">
          These are faces the app thinks it recognizes, grouped by person and age, the surest first. Under each face it
          says how sure it is: “Probably”, “Maybe” or “Not sure”. Confirmed faces help it recognize people; the ones you
          tap are remembered as “not them”. “Photos ›” (or pressing and holding a face) shows them in their photos.
        </p>
      )}
      {review.isLoading && <Placeholder kind="cards" />}
      {review.data?.length === 0 && (
        <Done title="All checked. Thank you!" action={
          <button className="t-btn t-btn--big" onClick={() => go("#/people")}>
            Name some faces
          </button>
        }>
          New guesses show up here as more faces get names.
        </Done>
      )}
      <div className="t-cards">
        {review.data?.map((g) => <CheckCard key={`${g.person_id}:${g.age_lo ?? "-"}:${g.year_lo ?? "-"}`} group={g} />)}
      </div>
    </div>
  );
}

const bracket = (g: ReviewGroup) =>
  g.age_lo != null ? `age ${g.age_lo}–${g.age_lo + 4}` : g.year_lo != null ? `${g.year_lo}–${String(g.year_lo + 4).slice(-2)}` : "undated";

function CheckCard({ group }: { group: ReviewGroup }) {
  const { run, busy, error } = useFaceAction();
  const { twinsOf } = useTwins();
  // Kept when you switch tabs and come back: Confirm must never confirm faces you said were wrong.
  const [wrong, setWrong] = useRemembered(`t-check:${group.person_id}:${group.age_lo ?? "-"}:${group.year_lo ?? "-"}`, new Set<number>());
  const good = group.faces.filter((f) => !wrong.has(f.id)).map((f) => f.id);
  const bad = group.faces.filter((f) => wrong.has(f.id)).map((f) => f.id);
  // The card's photos to swipe through, and its faces to point out on them.
  const photoIds = [...new Set(group.faces.map((f) => f.photo_id))];
  const look = (photoId: number) => openPhoto({ id: photoId, ids: photoIds, faces: group.faces.map((f) => f.id) });
  const confirm = (pair?: Pair) =>
    run(async () => {
      if (bad.length) await api.rejectFaces(bad);
      if (good.length) await (pair ? api.assignFaces(good, pair) : api.acceptFaces(good));
    }).then(() => setWrong(new Set()));
  return (
    <div className="t-card t-checkcard">
      <div className="t-checkcard__head">
        <span className="t-checkcard__name">{group.name}</span>
        <span className="t-muted">{bracket(group)}</span>
        <span className="t-muted t-small">
          {good.length} of {group.faces.length} look right
        </span>
        <button className="t-btn t-photos-btn" onClick={() => look(photoIds[0])}>
          Photos ›
        </button>
      </div>
      <div className="t-group__faces">
        {group.faces.map((f) => (
          <CheckFace
            key={f.id}
            src={faceUrl(f.id)}
            dim={wrong.has(f.id)}
            caption={f.suggested_name && f.suggested_alt_name
              ? `${pairLabel(f.suggested_name, f.suggested_alt_name)}?`
              : sureness(f.confidence)}
            onTap={() => setWrong((s) => { const n = new Set(s); n.has(f.id) ? n.delete(f.id) : n.add(f.id); return n; })}
            onHold={() => look(f.photo_id)}
          />
        ))}
      </div>
      <div className="t-checkcard__actions">
        <button className="t-btn t-btn--primary" disabled={busy} onClick={() => confirm()}>
          Confirm
        </button>
        {twinsOf(group.person_id).map((t) => (
          <button key={t.id} className="t-btn" disabled={busy || !good.length} onClick={() => confirm({ person_id: group.person_id, alt_person_id: t.id })}>
            Confirm as {pairLabel(group.name, t.name)}
          </button>
        ))}
      </div>
      {error && <p className="t-error">{error}</p>}
    </div>
  );
}

function CheckFace({ src, caption, dim, onTap, onHold }: { src: string; caption: string; dim: boolean; onTap: () => void; onHold: () => void }) {
  const press = useLongPress(onHold, undefined, { anyPointer: true });
  return (
    <button className={`t-gface ${dim ? "is-dim is-wrong" : ""}`} {...press.handlers} onClick={() => !press.consumeLongPress() && onTap()}>
      <img src={src} alt="" loading="lazy" />
      <span>{caption}</span>
    </button>
  );
}
