import { useEffect, useState } from "react";
import type { ListParams } from "./api";

export type Route =
  | { view: "grid"; list: ListParams }
  | { view: "photo"; id: number; list: ListParams }
  | { view: "jobs" }
  | { view: "people"; tab: "name" | "check" | "people" }
  | { view: "person"; id: number }
  | { view: "backs"; kind: "loose" | "paired"; album_id?: number; undated: boolean };

export function parseHash(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const q = new URLSearchParams(query);
  const list: ListParams = {
    album_id: q.get("album") ? Number(q.get("album")) : undefined,
    person_id: q.get("person") ? Number(q.get("person")) : undefined,
    status: (q.get("status") as ListParams["status"]) || undefined,
    duplicates: q.get("dups") === "1" || undefined,
    order: (q.get("order") as ListParams["order"]) || "album",
  };
  const m = path.match(/^\/photo\/(\d+)/);
  if (m) return { view: "photo", id: Number(m[1]), list };
  if (path.startsWith("/jobs")) return { view: "jobs" };
  const pm = path.match(/^\/people\/(\d+)/);
  if (pm) return { view: "person", id: Number(pm[1]) };
  if (path.startsWith("/people")) {
    const tab = q.get("tab");
    return { view: "people", tab: tab === "people" ? "people" : tab === "check" ? "check" : "name" };
  }
  if (path.startsWith("/backs"))
    return {
      view: "backs",
      kind: q.get("kind") === "paired" ? "paired" : "loose",
      album_id: list.album_id,
      undated: q.get("undated") === "1",
    };
  return { view: "grid", list };
}

export function listQuery(list: ListParams): string {
  const q = new URLSearchParams();
  if (list.album_id !== undefined) q.set("album", String(list.album_id));
  if (list.person_id !== undefined) q.set("person", String(list.person_id));
  if (list.status) q.set("status", list.status);
  if (list.duplicates) q.set("dups", "1");
  if (list.order && list.order !== "album") q.set("order", list.order);
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const backsHref = (album_id?: number, undated = false, kind: "loose" | "paired" = "loose") => {
  const q = new URLSearchParams();
  if (kind === "paired") q.set("kind", "paired");
  if (album_id !== undefined) q.set("album", String(album_id));
  if (undated) q.set("undated", "1");
  const s = q.toString();
  return `#/backs${s ? `?${s}` : ""}`;
};
export const gridHref = (list: ListParams) => `#/${listQuery(list)}`;
export const photoHref = (id: number, list: ListParams) => `#/photo/${id}${listQuery(list)}`;

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export const navigate = (href: string) => {
  window.location.hash = href.replace(/^#/, "");
};
