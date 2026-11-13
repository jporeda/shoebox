import { useEffect, useState } from "react";
import type { ListParams } from "../src/api";
import { listQuery, parseHash } from "../src/route";

/** The touch app's screens. The photo list (filters, order) uses the desktop's address format, so
 *  the same filters read the same in both apps. */
export type TouchRoute =
  | { view: "photos"; list: ListParams }
  | { view: "photo"; id: number; list: ListParams }
  | { view: "date" }
  | { view: "people" }
  | { view: "check" };

export function parseTouch(hash: string): TouchRoute {
  const path = hash.replace(/^#/, "").split("?")[0];
  const list = listOf(hash);
  const m = path.match(/^\/photo\/(\d+)/);
  if (m) return { view: "photo", id: Number(m[1]), list };
  if (path.startsWith("/date")) return { view: "date" };
  if (path.startsWith("/people")) return { view: "people" };
  if (path.startsWith("/check")) return { view: "check" };
  return { view: "photos", list };
}

function listOf(hash: string): ListParams {
  const query = hash.split("?")[1];
  const r = parseHash(`#/${query ? `?${query}` : ""}`);
  return r.view === "grid" ? r.list : {};
}

export const photosHref = (list: ListParams) => `#/${listQuery(list)}`;
export const viewerHref = (id: number, list: ListParams) => `#/photo/${id}${listQuery(list)}`;

export function useTouchRoute(): TouchRoute {
  const [route, setRoute] = useState(() => parseTouch(location.hash));
  useEffect(() => {
    const on = () => setRoute(parseTouch(location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (href: string) => {
  if (location.hash !== href) location.hash = href;
};
/** Same screen, other photo: replace the history entry so Back leaves the viewer in one step. */
export const goReplace = (href: string) => {
  history.replaceState(null, "", href);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
};
