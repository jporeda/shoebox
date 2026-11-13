import { usePeople, useTwins } from "../../src/peopleData";
import { backOr } from "../route";
import { plural } from "../ui";
import { Photos } from "./Photos";

/** One person: "‹ People", their name, and their photos oldest first. Under the People tab. */
export function PersonScreen({ id }: { id: number }) {
  const people = usePeople();
  const { twinsOf } = useTwins();
  const person = people.data?.find((p) => p.id === id);
  const back = () => backOr("#/people?tab=everyone");
  return (
    <div className="t-person-page">
      <div className="t-subhead">
        <button className="t-btn t-btn--plain t-subhead__back" onClick={back} aria-label="Back to People">
          ‹ People
        </button>
        <span className="t-subhead__title">{person?.name ?? "…"}</span>
        {person && (
          <span className="t-muted t-small">
            {plural(person.photo_count ?? person.face_count, "photo")}
            {twinsOf(id).length > 0 && ` · twin of ${twinsOf(id).map((t) => t.name).join(", ")}`}
          </span>
        )}
      </div>
      <Photos list={{ person_id: id, order: "chrono" }} bare />
    </div>
  );
}
