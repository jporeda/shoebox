import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, imgUrl, type ListParams } from "../../src/api";
import { usePhotos } from "../../src/hooks";
import { DatePanel } from "../dating";
import { openPhoto, replaceScreen } from "../route";

const QUEUES: { key: "untagged" | "inferred"; label: string; list: ListParams }[] = [
  { key: "untagged", label: "Undated", list: { status: "untagged" } },
  { key: "inferred", label: "Least sure", list: { status: "inferred", order: "confidence" } },
];

// Skipped photos, per queue, for as long as the app is open: they survive opening a photo, other
// tabs and coming back.
const skippedMemory = { untagged: new Set<number>(), inferred: new Set<number>() };
// The photo on screen, per queue. The list is refetched when a background job finishes (each dating
// starts one) and "Least sure" then sorts differently: that may change which photo comes next, but
// never the one you're looking at, or a tap would date a photo you never saw.
const shownMemory: { untagged?: number; inferred?: number } = {};
// The photo that just left the queue by being dated: if it comes back (Undo), it's shown again.
const datedMemory: { untagged?: number; inferred?: number } = {};

/** One photo at a time: one tap dates it and the next one comes up. */
export function DateQueue({ q: chosen }: { q?: "untagged" | "inferred" }) {
  const undated = usePhotos(QUEUES[0].list);
  const unsure = usePhotos(QUEUES[1].list);
  // Start with undated photos while there are any (the address keeps the queue you pick).
  const q = chosen ?? (undated.data && undated.data.items.every((p) => p.status !== "untagged") ? "inferred" : "untagged");
  const source = q === "untagged" ? undated : unsure;
  const [version, redraw] = useState(0);
  const skipped = skippedMemory[q];
  // A photo just dated is updated in place in the cached list (not refetched): it drops out here by
  // its new status.
  const items = useMemo(
    () => (source.data?.items ?? []).filter((p) => p.status === q && !skipped.has(p.id)),
    [source.data, q, skipped, version],
  );
  // Until it's dated (its status changes) or skipped, it stays; a photo just dated and then undone
  // comes back on screen.
  const pinned = shownMemory[q];
  if (pinned !== undefined && !skipped.has(pinned) && !items.some((p) => p.id === pinned)) datedMemory[q] = pinned;
  const undone = items.find((p) => p.id === datedMemory[q]);
  if (undone) datedMemory[q] = undefined;
  const current = undone ?? items.find((p) => p.id === pinned) ?? items[0];
  shownMemory[q] = current?.id;
  const detail = useQuery({ queryKey: ["photo", current?.id], queryFn: () => api.photo(current!.id), enabled: !!current });
  // The photo before it in the album, if dated: often the same day.
  const albumList = usePhotos(current ? { albums: String(current.album_id) } : { ids: "0" });
  const album = albumList.data?.items ?? [];
  const before = current ? album[album.findIndex((p) => p.id === current.id) - 1] : undefined;
  const p = detail.data;
  const skip = () => {
    // The photo shown right now (a quick second tap skips the next one, not the same one again).
    const left = (source.data?.items ?? []).filter((x) => x.status === q && !skipped.has(x.id));
    const shown = left.find((x) => x.id === shownMemory[q]) ?? left[0];
    if (shown) skipped.add(shown.id);
    shownMemory[q] = left.find((x) => !skipped.has(x.id))?.id;
    redraw((n) => n + 1);
  };

  return (
    <div className="t-queue">
      <div className="t-seg t-queue__switch">
        {QUEUES.map((x) => {
          // Counted from the list, so a photo dated here stops counting at once.
          const n = (x.key === "untagged" ? undated : unsure).data?.items.filter((p) => p.status === x.key).length;
          return (
            <button key={x.key} className={q === x.key ? "is-on" : ""} onClick={() => replaceScreen(`#/date?q=${x.key}`)}>
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
            <button className="t-btn" onClick={() => (skipped.clear(), redraw((n) => n + 1))}>
              Go through the skipped ones
            </button>
          )}
        </div>
      ) : (
        <div className="t-card t-queue__card">
          <button className="t-queue__photo" onClick={() => openPhoto({ id: current.id })} aria-label="Open this photo">
            <img src={imgUrl(current.sha256, "preview", current.rotation)} alt={current.rel_path} />
          </button>
          <div className="t-queue__controls">
            <div className="t-queue__head">
              <span className="t-muted">
                {items.length} to go{skipped.size ? ` · ${skipped.size} skipped` : ""}
              </span>
              <button className="t-btn" onClick={skip}>
                Skip
              </button>
            </div>
            {p && p.id === current.id ? (
              <DatePanel ids={[p.id]} photo={p} previous={before?.status === "reviewed" ? before.date_label : null} />
            ) : (
              <p className="t-muted">Loading…</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
