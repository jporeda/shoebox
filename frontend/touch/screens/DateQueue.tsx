import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, imgUrl, type ListParams } from "../../src/api";
import { usePhotos } from "../../src/hooks";
import { DatePanel } from "../dating";
import { go, viewerHref } from "../route";

const QUEUES: { key: "untagged" | "inferred"; label: string; list: ListParams }[] = [
  { key: "untagged", label: "Undated", list: { status: "untagged" } },
  { key: "inferred", label: "Least sure", list: { status: "inferred", order: "confidence" } },
];

/** One photo at a time: one tap dates it and the next one comes up. */
export function DateQueue() {
  const undated = usePhotos(QUEUES[0].list);
  const unsure = usePhotos(QUEUES[1].list);
  const [which, setWhich] = useState<"untagged" | "inferred" | null>(null);
  // Start with undated photos while there are any.
  const q = which ?? (undated.data && undated.data.total === 0 ? "inferred" : "untagged");
  const source = q === "untagged" ? undated : unsure;
  const queue = QUEUES.find((x) => x.key === q)!;
  const [skipped, setSkipped] = useState<Set<number>>(new Set());
  useEffect(() => setSkipped(new Set()), [q]);
  // A photo just dated is updated in place in the cached list (not refetched): it drops out here by
  // its new status.
  const items = useMemo(
    () => (source.data?.items ?? []).filter((p) => p.status === q && !skipped.has(p.id)),
    [source.data, skipped, q],
  );
  const current = items[0];
  const detail = useQuery({ queryKey: ["photo", current?.id], queryFn: () => api.photo(current!.id), enabled: !!current });
  // The photo before it in the album, if dated: often the same day.
  const albumList = usePhotos(current ? { albums: String(current.album_id) } : { ids: "0" });
  const album = albumList.data?.items ?? [];
  const before = current ? album[album.findIndex((p) => p.id === current.id) - 1] : undefined;
  const p = detail.data;

  return (
    <div className="t-queue">
      <div className="t-seg t-queue__switch">
        {QUEUES.map((x) => {
          // Counted from the list, so a photo dated here stops counting at once.
          const n = (x.key === "untagged" ? undated : unsure).data?.items.filter((p) => p.status === x.key).length;
          return (
            <button key={x.key} className={q === x.key ? "is-on" : ""} onClick={() => setWhich(x.key)}>
              {x.label}
              {n !== undefined && ` (${n})`}
            </button>
          );
        })}
      </div>
      {source.isLoading ? (
        <p className="t-muted t-pad">Loading…</p>
      ) : !current ? (
        <div className="t-empty">
          <p>{skipped.size ? "That's all of them, apart from the ones you skipped." : q === "untagged" ? "Every photo has at least an estimated date." : "No estimated dates left to check."}</p>
          {skipped.size > 0 && (
            <button className="t-btn" onClick={() => setSkipped(new Set())}>
              Go through the skipped ones
            </button>
          )}
        </div>
      ) : (
        <div className="t-card t-queue__card">
          <button className="t-queue__photo" onClick={() => go(viewerHref(current.id, queue.list))} aria-label="Open this photo">
            <img src={imgUrl(current.sha256, "preview", current.rotation)} alt={current.rel_path} />
          </button>
          <div className="t-queue__controls">
            <div className="t-queue__head">
              <span className="t-muted">{items.length} to go</span>
              <button className="t-btn" onClick={() => setSkipped((s) => new Set(s).add(current.id))}>
                Skip
              </button>
            </div>
            {p && p.id === current.id ? (
              <DatePanel
                ids={[p.id]}
                photo={p}
                previous={before?.status === "reviewed" ? before.date_label : null}
                // The dated photo leaves this list by itself; nothing else to do.
              />
            ) : (
              <p className="t-muted">Loading…</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
