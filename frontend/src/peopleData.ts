import { useQuery } from "@tanstack/react-query";
import { api, pairLabel, type Person } from "./api";

/** People data shared by the desktop and touch apps (no components here). */
export const usePeople = () => useQuery({ queryKey: ["people"], queryFn: api.people });

/** "A or B": a face that's one of two people, not known which (twins). */
export type Pair = { person_id: number; alt_person_id: number };

/** Known twins: people someone has paired on a "one of two" face. twinsOf(id) lists them, and
 *  pairs are each such pair once, "Ana or Ben" (never also "Ben or Ana"), for the name box. */
export function useTwins() {
  const people = usePeople();
  const byId = new Map((people.data ?? []).map((p) => [p.id, p]));
  const twinsOf = (id: number | null | undefined): Person[] =>
    id == null ? [] : (byId.get(id)?.twin_ids ?? []).flatMap((t) => byId.get(t) ?? []);
  const pairs = (people.data ?? []).flatMap((p) =>
    twinsOf(p.id)
      .map((t) => ({ label: pairLabel(p.name, t.name), pair: { person_id: p.id, alt_person_id: t.id } }))
      .filter((x) => x.label.startsWith(`${p.name} or `)), // each pair once, from its first name
  );
  return { twinsOf, pairs };
}
