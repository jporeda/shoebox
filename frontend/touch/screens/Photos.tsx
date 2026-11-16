import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { albumLabel, imgUrl, type ListParams, type PhotoSummary } from "../../src/api";
import { useGridPinch } from "../../src/gridPinch";
import { NewGrid, useNewGrid } from "../../src/GridNew";
import { useAlbums, useEventInstances, useLongPress, useMedia, usePhotos, useRemembered, useUpdatePhotos, useYears } from "../../src/hooks";
import { usePeople } from "../../src/peopleData";
import { go, LAST_PHOTO_EVENT, openPhoto, photosHref } from "../route";
import { DatePanel, EventPanel } from "../dating";
import { Placeholder, plural, Sheet, StatusDot } from "../ui";

const ids = (text?: string) => new Set((text ?? "").split(",").filter(Boolean).map(Number));
const text = (set: Set<number>) => (set.size ? [...set].sort((a, b) => a - b).join(",") : undefined);
const toggled = (set: Set<number>, id: number) => {
  const next = new Set(set);
  next.has(id) ? next.delete(id) : next.add(id);
  return text(next);
};
function albumsOf(list: ListParams) {
  const s = ids(list.albums);
  if (list.album_id !== undefined) s.add(list.album_id);
  return s;
}
function peopleOf(list: ListParams) {
  const s = ids(list.people);
  if (list.person_id !== undefined) s.add(list.person_id);
  return s;
}
const STATUS: { value: ListParams["status"]; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "untagged", label: "Undated" },
  { value: "inferred", label: "Estimated" },
  { value: "reviewed", label: "Dated" },
];

// Scroll position per list, so coming back from a photo lands where you were.
const scrollMemory = new Map<string, number>();

/** The photo grid. `bare`: without the filter bar (a person's page, which has its own header). */
export function Photos({ list, bare }: { list: ListParams; bare?: boolean }) {
  const photos = usePhotos(list);
  const items = photos.data?.items ?? [];
  const gridEl = useRef<HTMLDivElement>(null);
  const phone = useMedia("(max-width: 600px)");
  const wide = useMedia("(min-width: 1100px)");
  // The new grid (a preview, switched on in the ⋯ menu): pinching changes how many photos fill a
  // row, which sets the row height; the same column steps as the classic grid.
  const look = useNewGrid(true);
  const defaultCols = phone ? 3 : wide ? 7 : 5;
  const pinch = useGridPinch(gridEl, true, phone, look.on ? defaultCols : undefined);
  const perRow = pinch.cols ?? defaultCols;
  const rowHeight = pinch.width ? Math.max(48, (pinch.width - 2 * (perRow - 1)) / perRow / 1.3) : 110;
  const [filters, setFilters] = useState(false);
  const [action, setAction] = useState<"" | "date" | "event">("");
  const update = useUpdatePhotos();
  const key = photosHref(list);
  // Kept per list when you switch tabs and come back; a new filter starts a new selection.
  const [selecting, setSelecting] = useRemembered(`t-selecting:${key}`, false);
  const [selected, setSelected] = useRemembered(`t-selected:${key}`, new Set<number>());
  const shownKey = useRef(key);

  useLayoutEffect(() => {
    const el = gridEl.current;
    if (el && photos.data && !photos.isPlaceholderData) el.scrollTop = scrollMemory.get(key) ?? 0;
  }, [key, photos.data, photos.isPlaceholderData]);
  useEffect(() => {
    if (shownKey.current === key) return;
    shownKey.current = key;
    setSelected(new Set());
    setSelecting(false);
  }, [key]);
  // Back from a photo: show (and briefly outline) the one you ended on, wherever swiping took you.
  useEffect(() => {
    const on = (e: Event) => {
      const id = (e as CustomEvent<number>).detail;
      requestAnimationFrame(() => {
        const grid = gridEl.current;
        const tile = grid?.querySelector<HTMLElement>(`[data-id="${id}"]`);
        if (!grid || !tile) return;
        const g = grid.getBoundingClientRect(), t = tile.getBoundingClientRect();
        if (t.top < g.top || t.bottom > g.bottom) tile.scrollIntoView({ block: "center" });
        tile.classList.add("is-recent");
        window.setTimeout(() => tile.classList.remove("is-recent"), 1500);
      });
    };
    window.addEventListener(LAST_PHOTO_EVENT, on);
    return () => window.removeEventListener(LAST_PHOTO_EVENT, on);
  }, []);

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const chosen = items.filter((p) => selected.has(p.id)).map((p) => p.id);

  return (
    <div className="t-photos">
      {!bare && <FilterBar list={list} total={photos.data?.total} onOpen={() => setFilters(true)} />}
      <div
        ref={gridEl}
        className={`t-grid ${look.on ? "grid--new" : pinch.className}`}
        style={look.on ? undefined : pinch.style}
        onScroll={(e) => scrollMemory.set(key, e.currentTarget.scrollTop)}
      >
        {look.on ? (
          <NewGrid touchApp items={items} list={list} row={rowHeight} selected={selected} selecting={selecting}
            onOpen={(p) => openPhoto({ id: p.id })}
            onTap={(p) => {
              if (pinch.suppressed()) return;
              if (selecting) toggle(p.id);
              else openPhoto({ id: p.id });
            }}
            onLongPress={(p) => {
              if (pinch.suppressed()) return;
              setSelecting(true);
              toggle(p.id);
            }}
          />
        ) : items.map((p) => (
          <Tile
            key={p.id}
            p={p}
            big={pinch.big}
            selecting={selecting}
            selected={selected.has(p.id)}
            onTap={() => {
              if (pinch.suppressed()) return;
              if (selecting) toggle(p.id);
              else openPhoto({ id: p.id });
            }}
            onLongPress={() => {
              if (pinch.suppressed()) return;
              setSelecting(true);
              toggle(p.id);
            }}
          />
        ))}
        {photos.isLoading && <Placeholder kind="grid" />}
        {!photos.isLoading && items.length === 0 && (
          <p className="t-empty">
            {Object.values(list).some((v) => v !== undefined && v !== "album") ? (
              <>
                No photos match these filters.{" "}
                <button className="t-link" onClick={() => go(photosHref({}))}>
                  Show all photos
                </button>
              </>
            ) : (
              "No photos yet."
            )}
          </p>
        )}
      </div>
      {selecting && (
        <div className="t-selbar">
          <span className="t-selbar__count">{chosen.length ? plural(chosen.length, "photo") : "Tap photos"}</span>
          <button className="t-btn" disabled={!chosen.length} onClick={() => setAction("date")}>
            Date…
          </button>
          <button className="t-btn" disabled={!chosen.length} onClick={() => setAction("event")}>
            Event…
          </button>
          <button className="t-btn t-btn--icon" disabled={!chosen.length} aria-label="Rotate left" onClick={() => update.mutate({ ids: chosen, rotate_by: 270 })}>
            ⟲
          </button>
          <button className="t-btn t-btn--icon" disabled={!chosen.length} aria-label="Rotate right" onClick={() => update.mutate({ ids: chosen, rotate_by: 90 })}>
            ⟳
          </button>
          <button className="t-btn t-btn--primary" onClick={() => (setSelecting(false), setSelected(new Set()))}>
            Done
          </button>
        </div>
      )}
      <Sheet open={action === "date"} onClose={() => setAction("")} title={`Date ${plural(chosen.length, "photo")}`}>
        <DatePanel ids={chosen} onDone={() => setAction("")} />
      </Sheet>
      <Sheet open={action === "event"} onClose={() => setAction("")} title={`Event for ${plural(chosen.length, "photo")}`}>
        <EventPanel ids={chosen} onDone={() => setAction("")} />
      </Sheet>
      <FilterSheet list={list} open={filters} onClose={() => setFilters(false)} />
    </div>
  );
}

function Tile({ p, big, selecting, selected, onTap, onLongPress }: {
  p: PhotoSummary;
  big: boolean;
  selecting: boolean;
  selected: boolean;
  onTap: () => void;
  onLongPress: () => void;
}) {
  const press = useLongPress(onLongPress, undefined, { anyPointer: true });
  return (
    <button
      className={`tile t-tile ${selected ? "is-selected" : ""} ${selecting ? "is-selecting" : ""}`}
      data-id={p.id}
      {...press.handlers}
      onClick={() => {
        if (press.consumeLongPress()) return;
        onTap();
      }}
      aria-label={`${p.rel_path}${p.date_label ? `, ${p.date_label}` : ", no date"}`}
    >
      <img src={imgUrl(p.sha256, big ? "preview" : "thumb", p.rotation)} loading="lazy" alt="" draggable={false} />
      <StatusDot status={p.status} />
      {selecting && <span className="t-tile__check" aria-hidden>{selected ? "✓" : ""}</span>}
    </button>
  );
}

/** What's showing, always in view: "All photos" or a chip per filter (tap ✕ to drop it). */
function FilterBar({ list, total, onOpen }: { list: ListParams; total?: number; onOpen: () => void }) {
  const albums = useAlbums();
  const people = usePeople();
  const events = useEventInstances();
  const set = (patch: Partial<ListParams>) => go(photosHref({ ...list, ...patch }));
  const chips: { key: string; label: string; off: () => void }[] = [];
  const a = albumsOf(list);
  for (const id of a) {
    const al = albums.data?.find((x) => x.id === id);
    chips.push({ key: `a${id}`, label: al ? albumLabel(al) : "Album", off: () => set({ albums: text(new Set([...a].filter((x) => x !== id))), album_id: undefined }) });
  }
  for (const y of ids(list.years)) chips.push({ key: `y${y}`, label: String(y), off: () => set({ years: toggled(ids(list.years), y) }) });
  const pp = peopleOf(list);
  for (const id of pp)
    chips.push({ key: `p${id}`, label: people.data?.find((x) => x.id === id)?.name ?? "Person", off: () => set({ people: text(new Set([...pp].filter((x) => x !== id))), person_id: undefined }) });
  for (const id of ids(list.events))
    chips.push({ key: `e${id}`, label: events.data?.find((x) => x.id === id)?.label ?? "Event", off: () => set({ events: toggled(ids(list.events), id) }) });
  const st = STATUS.find((s) => s.value === list.status && s.value);
  if (st) chips.push({ key: "s", label: st.label, off: () => set({ status: undefined }) });
  if (list.ids) chips.push({ key: "i", label: "Chosen photos", off: () => set({ ids: undefined }) });
  return (
    <div className="t-filterbar">
      <button className="t-chip t-chip--filter" onClick={onOpen}>
        {chips.length ? "Filter" : "All photos"} ▾
      </button>
      {chips.map((c) => (
        <button key={c.key} className="t-chip is-on" onClick={c.off} aria-label={`Stop filtering by ${c.label}`}>
          {c.label} <span aria-hidden>✕</span>
        </button>
      ))}
      {total !== undefined && <span className="t-filterbar__count">{plural(total, "photo")}</span>}
    </div>
  );
}

function FilterSheet({ list, open, onClose }: { list: ListParams; open: boolean; onClose: () => void }) {
  const albums = useAlbums();
  const years = useYears();
  const people = usePeople();
  const events = useEventInstances();
  const set = (patch: Partial<ListParams>) => go(photosHref({ ...list, ...patch }));
  const a = albumsOf(list);
  const y = ids(list.years);
  const pp = peopleOf(list);
  const ev = ids(list.events);
  return (
    <Sheet open={open} onClose={onClose} title="Show" full>
      <section className="t-section">
        <div className="t-seg">
          {STATUS.map((s) => (
            <button key={s.label} className={list.status === s.value ? "is-on" : ""} onClick={() => set({ status: s.value })}>
              {s.label}
            </button>
          ))}
        </div>
        <div className="t-seg">
          <button className={list.order !== "chrono" ? "is-on" : ""} onClick={() => set({ order: "album" })}>
            Album order
          </button>
          <button className={list.order === "chrono" ? "is-on" : ""} onClick={() => set({ order: "chrono" })}>
            By date
          </button>
        </div>
      </section>
      <section className="t-section">
        <h3 className="t-h3">Years</h3>
        <div className="t-chips">
          {years.data?.map((yy) => (
            <button key={yy.year} className={`t-chip ${y.has(yy.year) ? "is-on" : ""}`} onClick={() => set({ years: toggled(y, yy.year) })}>
              {yy.year}
            </button>
          ))}
          {years.data?.length === 0 && <span className="t-muted">No dated photos yet.</span>}
        </div>
      </section>
      <section className="t-section">
        <h3 className="t-h3">People</h3>
        {pp.size > 1 && (
          <div className="t-seg">
            <button className={list.people_mode !== "any" ? "is-on" : ""} onClick={() => set({ people_mode: undefined })}>
              All together
            </button>
            <button className={list.people_mode === "any" ? "is-on" : ""} onClick={() => set({ people_mode: "any" })}>
              Any of them
            </button>
          </div>
        )}
        <div className="t-chips">
          {people.data?.map((p) => (
            <button key={p.id} className={`t-chip ${pp.has(p.id) ? "is-on" : ""}`} onClick={() => set({ people: toggled(pp, p.id), person_id: undefined })}>
              {p.name}
            </button>
          ))}
          {people.data?.length === 0 && <span className="t-muted">Nobody named yet.</span>}
        </div>
      </section>
      <section className="t-section">
        <h3 className="t-h3">Events</h3>
        <div className="t-chips">
          {events.data?.filter((e) => e.count > 0 || ev.has(e.id)).map((e) => (
            <button key={e.id} className={`t-chip ${ev.has(e.id) ? "is-on" : ""}`} onClick={() => set({ events: toggled(ev, e.id) })}>
              {e.label}
            </button>
          ))}
          {events.data?.every((e) => e.count === 0) && <span className="t-muted">No events yet.</span>}
        </div>
      </section>
      <section className="t-section">
        <h3 className="t-h3">Albums</h3>
        <div className="t-list">
          {albums.data?.map((al) => (
            <button key={al.id} className={`t-row ${a.has(al.id) ? "is-on" : ""}`} onClick={() => set({ albums: toggled(a, al.id), album_id: undefined })}>
              <span>{albumLabel(al)}</span>
              <span className="t-muted">
                {al.dated}/{al.count} dated {a.has(al.id) ? "✓" : ""}
              </span>
            </button>
          ))}
        </div>
      </section>
      <button className="t-btn t-btn--block" onClick={() => (go(photosHref({ order: list.order })), onClose())}>
        Clear all filters
      </button>
    </Sheet>
  );
}
