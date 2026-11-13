import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type BulkUpdate, type ListParams, type PhotoPage } from "./api";

export const useAlbums = () => useQuery({ queryKey: ["albums"], queryFn: api.albums });
export const useStats = () => useQuery({ queryKey: ["stats"], queryFn: api.stats });
export const useEventKinds = () => useQuery({ queryKey: ["eventKinds"], queryFn: api.eventKinds });
export const usePlaces = () => useQuery({ queryKey: ["places"], queryFn: api.places });
export const useYears = () => useQuery({ queryKey: ["years"], queryFn: api.years });
export const useEventInstances = () => useQuery({ queryKey: ["eventInstances"], queryFn: api.eventInstances });
const ACTIVE = ["queued", "running", "cancelling"];

/** The job list, polled every second while a job is queued or running, else every 10 s. */
export const useJobs = () =>
  useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: (q) => (q.state.data?.some((j) => ACTIVE.includes(j.state)) ? 1000 : 10000),
  });

/** Mounted once in App: when any job finishes, on any page, the data it may have changed is
 *  refetched (e.g. the "Infer dates & names" run that confirming faces on Check starts). Compares
 *  finished job ids rather than watching running ones, so a job that starts and finishes between
 *  two polls still counts. */
export function useJobWatcher() {
  const qc = useQueryClient();
  const jobs = useJobs();
  const finished = useRef<Set<number> | null>(null);
  useEffect(() => {
    if (!jobs.data) return;
    const now = new Set(jobs.data.filter((j) => !ACTIVE.includes(j.state)).map((j) => j.id));
    const seen = finished.current;
    finished.current = now;
    if (seen && [...now].some((id) => !seen.has(id))) qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs" });
  }, [jobs.data, qc]);
}

export const usePhotos = (list: ListParams) =>
  useQuery({ queryKey: ["photos", list], queryFn: () => api.photos(list), placeholderData: (prev) => prev });

/** Bulk edit; patches cached photo lists in place so the grid doesn't reload. */
export function useUpdatePhotos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkUpdate) => api.update(body),
    onSuccess: (res) => {
      const byId = new Map(res.photos.map((p) => [p.id, p]));
      qc.setQueriesData<PhotoPage>({ queryKey: ["photos"] }, (page) =>
        page ? { ...page, items: page.items.map((p) => byId.get(p.id) ?? p) } : page,
      );
      for (const p of res.photos) qc.invalidateQueries({ queryKey: ["photo", p.id] });
      qc.invalidateQueries({ queryKey: ["albums"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["edits"] });
      qc.invalidateQueries({ queryKey: ["eventInstances"] });
      qc.invalidateQueries({ queryKey: ["places"] });
      qc.invalidateQueries({ queryKey: ["years"] });
    },
  });
}

export function useUndo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (opts?: { batchId?: string; skip?: boolean }) => api.undo(opts?.batchId, opts?.skip),
    onSuccess: () => qc.invalidateQueries(),
  });
}

/** Live result of a CSS media query (e.g. "(pointer: coarse)"). */
export function useMedia(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return matches;
}

/** A finger (or pen) is the main pointer: phones and tablets. Same test as the CSS. */
export const useTouch = () => useMedia("(pointer: coarse)");
/** Phone-width layout (matches the CSS breakpoint `max-width: 600px`). */
export const usePhone = () => useMedia("(max-width: 600px)");

export const LONG_PRESS_MS = 450;

/** Long-press for finger pointers only. Mouse and pen are left alone, so desktop clicks (and a pen
 *  or stylus, which clicks precisely) keep their click behaviour.
 *  Spread `handlers` on the element; in its onClick, `consumeLongPress()` returns true (once) for
 *  the click that ends a long press, which should then be ignored, and `wasTouch()` says whether
 *  the last press was a finger. */
export function useLongPress(onLongPress: () => void, ms = LONG_PRESS_MS, opts: { anyPointer?: boolean } = {}) {
  const timer = useRef<number | undefined>(undefined);
  const start = useRef<[number, number] | null>(null);
  const fired = useRef(false);
  const touch = useRef(false);
  const cb = useRef(onLongPress);
  cb.current = onLongPress;
  const cancel = () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
    start.current = null;
  };
  useEffect(() => cancel, []);
  const handlers = {
    onPointerDown: (e: React.PointerEvent) => {
      touch.current = e.pointerType === "touch";
      fired.current = false;
      cancel();
      // The touch app treats every pointer as a finger (so it also works with a mouse).
      if ((!touch.current && !opts.anyPointer) || !e.isPrimary) return;
      start.current = [e.clientX, e.clientY];
      timer.current = window.setTimeout(() => {
        timer.current = undefined;
        start.current = null;
        fired.current = true;
        navigator.vibrate?.(10);
        cb.current();
      }, ms);
    },
    onPointerMove: (e: React.PointerEvent) => {
      const s = start.current;
      if (s && Math.hypot(e.clientX - s[0], e.clientY - s[1]) > 10) cancel();
    },
    onPointerUp: cancel,
    onPointerCancel: cancel,
    onContextMenu: (e: React.MouseEvent) => {
      // Android opens a context menu on a long press; iOS shows a callout (blocked in CSS).
      if (touch.current) e.preventDefault();
    },
  };
  return {
    handlers,
    wasTouch: () => touch.current,
    consumeLongPress: () => {
      const f = fired.current;
      fired.current = false;
      return f;
    },
  };
}

/** Open/closed state of the phone sidebar drawer; closes itself when the route changes. */
export function useDrawer() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("hashchange", close);
    return () => window.removeEventListener("hashchange", close);
  }, []);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return [open, setOpen] as const;
}
