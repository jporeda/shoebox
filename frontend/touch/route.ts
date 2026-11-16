import { useEffect, useState } from "react";
import type { ListParams } from "../src/api";
import { listQuery, parseHash } from "../src/route";

/** The screen underneath. The photo list (filters, order) uses the desktop's address format, so the
 *  same filters read the same in both apps. */
export type Screen =
  | { view: "photos"; list: ListParams }
  | { view: "person"; id: number }
  | { view: "date"; q?: "untagged" | "inferred" }
  | { view: "people"; tab: "name" | "everyone" }
  | { view: "check" };

/** A photo open on top of the screen, which stays loaded underneath (its scroll, marks, half-typed
 *  names and queue all survive). `ids`: the photos to swipe through when not the screen's own list
 *  (one Check or To name group); `faces`: the faces to point out on them. */
export type Overlay = { id: number; ids?: number[]; faces?: number[] };

export type TouchRoute = { screen: Screen; overlay: Overlay | null; base: string };

const OVERLAY_KEYS = ["view", "vids", "vfaces"];
const numbers = (s: string | null) => (s ? s.split(",").filter(Boolean).map(Number) : undefined);

export function parseTouch(hash: string): TouchRoute {
  const raw = hash.replace(/^#/, "") || "/";
  let [path, query = ""] = raw.split("?");
  const q = new URLSearchParams(query);
  // Older links: #/photo/12?filters → the photos screen with photo 12 open.
  const legacy = path.match(/^\/photo\/(\d+)/);
  if (legacy) {
    path = "/";
    q.set("view", legacy[1]);
  }
  const view = q.get("view");
  const overlay: Overlay | null = view
    ? { id: Number(view), ids: numbers(q.get("vids")), faces: numbers(q.get("vfaces")) }
    : null;
  for (const k of OVERLAY_KEYS) q.delete(k);
  const rest = q.toString();
  const base = `#${path}${rest ? `?${rest}` : ""}`;
  return { screen: screenOf(path, q), overlay, base };
}

function screenOf(path: string, q: URLSearchParams): Screen {
  const person = path.match(/^\/person\/(\d+)/);
  if (person) return { view: "person", id: Number(person[1]) };
  if (path.startsWith("/date")) return { view: "date", q: q.get("q") === "inferred" ? "inferred" : q.get("q") === "untagged" ? "untagged" : undefined };
  if (path.startsWith("/people")) return { view: "people", tab: q.get("tab") === "everyone" ? "everyone" : "name" };
  if (path.startsWith("/check")) return { view: "check" };
  const r = parseHash(`#/${q.toString() ? `?${q}` : ""}`);
  return { view: "photos", list: r.view === "grid" ? r.list : {} };
}

/** The photos the viewer swipes through, for this screen (unless the overlay names its own). */
export function viewerList(screen: Screen, overlay: Overlay): ListParams {
  if (overlay.ids?.length) return { ids: overlay.ids.join(",") };
  if (screen.view === "photos") return screen.list;
  if (screen.view === "person") return { person_id: screen.id, order: "chrono" };
  if (screen.view === "date") return screen.q === "inferred" ? { status: "inferred", order: "confidence" } : { status: "untagged" };
  return { ids: String(overlay.id) };
}

export const photosHref = (list: ListParams) => `#/${listQuery(list)}`;
export const personHref = (id: number) => `#/person/${id}`;

function withOverlay(base: string, o: Overlay): string {
  const [path, query = ""] = base.split("?");
  const q = new URLSearchParams(query);
  q.set("view", String(o.id));
  if (o.ids?.length) q.set("vids", o.ids.join(","));
  if (o.faces?.length) q.set("vfaces", o.faces.join(","));
  return `${path}?${q}`;
}

const notify = () => window.dispatchEvent(new HashChangeEvent("hashchange"));

/** Open a photo over the current screen. The history entry is marked as ours, so closing it is a
 *  real Back (the phone's own back gesture agrees) instead of a new entry. */
export function openPhoto(o: Overlay) {
  const { base } = parseTouch(location.hash);
  history.pushState({ shoeboxOverlay: true }, "", withOverlay(base, o));
  notify();
}

/** Another photo in the same viewer (the same group of photos and faces): replaces the entry, so
 *  Back still closes the viewer in one step. */
export function stepPhoto(id: number) {
  const { base, overlay } = parseTouch(location.hash);
  history.replaceState(history.state, "", withOverlay(base, { ...overlay, id }));
  notify();
}

/** Close the viewer: Back if we opened it, else (a link straight to a photo) the screen under it. */
export function closePhoto() {
  if (history.state?.shoeboxOverlay) history.back();
  else {
    history.replaceState(null, "", parseTouch(location.hash).base);
    notify();
  }
}

export function useTouchRoute(): TouchRoute {
  const [route, setRoute] = useState(() => parseTouch(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseTouch(location.hash));
    window.addEventListener("hashchange", on);
    window.addEventListener("popstate", on);
    return () => {
      window.removeEventListener("hashchange", on);
      window.removeEventListener("popstate", on);
    };
  }, []);
  return route;
}

/** Another screen (a tab, a person): a normal new history entry. */
export const go = (href: string) => {
  if (location.hash !== href) location.hash = href;
};

/** Tells the screen underneath which photo the viewer was last on (the grid scrolls to it). */
export const LAST_PHOTO_EVENT = "shoebox:lastphoto";

/** Open another screen from this one (a person from People), remembering we came from here, so its
 *  "‹ Back" is a real Back. */
export function pushScreen(href: string) {
  history.pushState({ shoeboxFrom: location.hash || "#/" }, "", href);
  notify();
}

/** Back to the screen we came from, or (opened from a link or Home Screen) to `fallback`. */
export function backOr(fallback: string) {
  if (history.state?.shoeboxFrom) history.back();
  else go(fallback);
}

/** Switch a screen's own tab (To name / Everyone) without adding a history step. */
export function replaceScreen(href: string) {
  history.replaceState(history.state, "", href);
  notify();
}
