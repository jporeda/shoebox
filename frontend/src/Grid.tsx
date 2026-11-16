import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { albumLabel, imgUrl, type ListParams, type PhotoSummary } from "./api";
import { FiltersButton, SidebarDrawer } from "./Drawer";
import { DateEventEditor, PlaceEditor } from "./Editor";
import { useGridPinch } from "./gridPinch";
import { NewGrid, useNewGrid } from "./GridNew";
import { useAlbums, useDrawer, useEventInstances, useLongPress, usePhone, usePhotos, useTouch, useUpdatePhotos, useYears } from "./hooks";
import { isTypingTarget } from "./keys";
import { usePeople } from "./People";
import { backsHref, gridHref, listQuery, navigate, photoHref, takeLastPhoto } from "./route";

const STATUS_FILTERS: { value: ListParams["status"]; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "untagged", label: "Undated" },
  { value: "inferred", label: "Estimated" },
  { value: "reviewed", label: "Confirmed" },
];

function Sidebar({ list, open, onClose }: { list: ListParams; open: boolean; onClose: () => void }) {
  const touch = useTouch();
  const go = (patch: Partial<ListParams>) => navigate(gridHref({ ...list, ...patch }));
  return (
    <SidebarDrawer open={open} onClose={onClose}>
      <FilterSection list={list} />
      <section>
        <h3 className="sidebar__heading">Show</h3>
        <div className="segmented">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.label}
              className={list.status === f.value ? "is-active" : ""}
              onClick={() => go({ status: f.value })}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="check">
          <input type="checkbox" checked={!!list.duplicates} onChange={(e) => go({ duplicates: e.target.checked || undefined })} />
          Only possible duplicates
        </label>
      </section>
      <section>
        <h3 className="sidebar__heading">Order</h3>
        <div className="segmented">
          <button className={!list.order || list.order === "album" ? "is-active" : ""} onClick={() => go({ order: "album" })}>
            Album
          </button>
          <button className={list.order === "chrono" ? "is-active" : ""} onClick={() => go({ order: "chrono" })}>
            By date
          </button>
          <button
            className={list.order === "confidence" ? "is-active" : ""}
            onClick={() => go({ order: "confidence", status: "inferred" })}
            title="Estimated dates, least sure first: the review queue"
          >
            Least sure
          </button>
        </div>
      </section>
      {touch ? (
        <section className="sidebar__help muted">
          Tap to open · Press and hold to select · Then tap to add or remove, or press and hold another photo to select
          everything between · Pinch with two fingers for bigger or smaller photos
        </section>
      ) : (
        <section className="sidebar__help muted">
          Click to select · Shift-click for a range · ⌘-click to add · Double-click to open · <kbd>Y</kbd> date ·{" "}
          <kbd>R</kbd> rotate · <kbd>⌘Z</kbd> undo
        </section>
      )}
    </SidebarDrawer>
  );
}

const idSet = (text?: string) => new Set((text ?? "").split(",").filter(Boolean).map(Number));
const idText = (ids: Set<number>) => (ids.size ? [...ids].sort((a, b) => a - b).join(",") : undefined);
/** Ticked albums; an older single-album link (album_id, e.g. from a back) counts as one ticked. */
function chosenAlbums(list: ListParams): Set<number> {
  const set = idSet(list.albums);
  if (list.album_id !== undefined) set.add(list.album_id);
  return set;
}

/** Years, people and events, in any combination: any of the ticked years or events; the ticked
 *  people all together (or any of them). Each choice is part of the address, so Back undoes it. */
function FilterSection({ list }: { list: ListParams }) {
  const albums = useAlbums();
  const years = useYears();
  const people = usePeople();
  const events = useEventInstances();
  const go = (patch: Partial<ListParams>) => navigate(gridHref({ ...list, ...patch }));
  // Arriving from a person's page ("Show N photos") counts as that person being ticked.
  const chosenPeople = idSet(list.people);
  if (list.person_id !== undefined) chosenPeople.add(list.person_id);
  const chosenAlbumSet = chosenAlbums(list);
  const chosenYears = idSet(list.years);
  const chosenEvents = idSet(list.events);
  const toggle = (set: Set<number>, id: number) => {
    const next = new Set(set);
    next.has(id) ? next.delete(id) : next.add(id);
    return idText(next);
  };
  const active = chosenAlbumSet.size + chosenYears.size + chosenPeople.size + chosenEvents.size;
  return (
    <section className="filters">
      <h3 className="sidebar__heading">
        Filter
        {active > 0 && (
          <button
            className="linkish"
            onClick={() =>
              go({ albums: undefined, album_id: undefined, years: undefined, people: undefined, people_mode: undefined, events: undefined, person_id: undefined })
            }
          >
            clear {active}
          </button>
        )}
      </h3>
      <details>
        <summary>Albums{chosenAlbumSet.size ? ` · ${chosenAlbumSet.size}` : ""}</summary>
        <ul className="filters__list filters__list--albums">
          {albums.data?.map((a) => (
            <li key={a.id}>
              <label className={`check ${a.missing ? "is-missing" : ""}`} title={a.rel_path}>
                <input
                  type="checkbox"
                  checked={chosenAlbumSet.has(a.id)}
                  onChange={() => go({ albums: toggle(chosenAlbumSet, a.id), album_id: undefined })}
                />
                <span className="filters__name" title={albumLabel(a)}>{albumLabel(a)}</span>
                <Progress done={a.dated} total={a.count} />
              </label>
            </li>
          ))}
        </ul>
      </details>
      <details open={chosenYears.size > 0}>
        <summary>Years{chosenYears.size ? ` · ${[...chosenYears].sort().join(", ")}` : ""}</summary>
        <div className="filters__chips">
          {years.data?.map((y) => (
            <button
              key={y.year}
              className={`chip ${chosenYears.has(y.year) ? "is-on" : ""}`}
              aria-pressed={chosenYears.has(y.year)}
              title={`${y.count} photo${y.count === 1 ? "" : "s"}`}
              onClick={() => go({ years: toggle(chosenYears, y.year) })}
            >
              {y.year}
            </button>
          ))}
          {years.data?.length === 0 && <span className="muted small">No dated photos yet.</span>}
        </div>
      </details>
      <details open={chosenPeople.size > 0}>
        <summary>People{chosenPeople.size ? ` · ${chosenPeople.size}` : ""}</summary>
        {chosenPeople.size > 1 && (
          <div className="segmented filters__mode">
            <button className={list.people_mode !== "any" ? "is-active" : ""} onClick={() => go({ people_mode: undefined })}>
              All together
            </button>
            <button className={list.people_mode === "any" ? "is-active" : ""} onClick={() => go({ people_mode: "any" })}>
              Any of them
            </button>
          </div>
        )}
        <ul className="filters__list">
          {people.data?.map((p) => (
            <li key={p.id}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={chosenPeople.has(p.id)}
                  onChange={() => go({ people: toggle(chosenPeople, p.id), person_id: undefined })}
                />
                <span className="filters__name">{p.name}</span>
                <span className="muted small">{p.face_count}</span>
              </label>
            </li>
          ))}
          {people.data?.length === 0 && <li className="muted small">Nobody named yet.</li>}
        </ul>
      </details>
      <details open={chosenEvents.size > 0}>
        <summary>Events{chosenEvents.size ? ` · ${chosenEvents.size}` : ""}</summary>
        <ul className="filters__list">
          {events.data
            ?.filter((ev) => ev.count > 0 || chosenEvents.has(ev.id))
            .map((ev) => (
              <li key={ev.id}>
                <label className="check">
                  <input type="checkbox" checked={chosenEvents.has(ev.id)} onChange={() => go({ events: toggle(chosenEvents, ev.id) })} />
                  <span className="filters__name">{ev.label}</span>
                  <span className="muted small">{ev.count}</span>
                </label>
              </li>
            ))}
          {events.data?.every((ev) => ev.count === 0) && <li className="muted small">No events tagged yet.</li>}
        </ul>
      </details>
    </section>
  );
}

/** The list with every filter off (the order is kept). */
function clearedFilters(list: ListParams): ListParams {
  return { order: list.order };
}

function filterCount(list: ListParams): number {
  const people = idSet(list.people);
  if (list.person_id !== undefined) people.add(list.person_id);
  return chosenAlbums(list).size + idSet(list.years).size + people.size + idSet(list.events).size
    + (list.status ? 1 : 0) + (list.duplicates ? 1 : 0) + (list.ids ? 1 : 0);
}

const STATUS_NAMES: Record<string, string> = { untagged: "Undated", inferred: "Estimated", reviewed: "Confirmed" };

/** What the grid is showing, as removable chips over the photos: always in view (the sidebar may be
 *  scrolled or, on a phone, closed), and one tap takes any of them off. */
function ActiveFilters({ list }: { list: ListParams }) {
  const albums = useAlbums();
  const people = usePeople();
  const events = useEventInstances();
  const go = (patch: Partial<ListParams>) => navigate(gridHref({ ...list, ...patch }));
  const chosenAlbumSet = chosenAlbums(list);
  const chosenPeople = idSet(list.people);
  if (list.person_id !== undefined) chosenPeople.add(list.person_id);
  const without = (set: Set<number>, id: number) => idText(new Set([...set].filter((x) => x !== id)));
  const chips: { key: string; label: string; off: () => void }[] = [];
  for (const id of chosenAlbumSet) {
    const a = albums.data?.find((x) => x.id === id);
    chips.push({ key: `a${id}`, label: a ? albumLabel(a) : "Album", off: () => go({ albums: without(chosenAlbumSet, id), album_id: undefined }) });
  }
  for (const y of idSet(list.years)) chips.push({ key: `y${y}`, label: String(y), off: () => go({ years: without(idSet(list.years), y) }) });
  for (const id of chosenPeople) {
    const name = people.data?.find((p) => p.id === id)?.name ?? "Person";
    chips.push({ key: `p${id}`, label: name, off: () => go({ people: without(chosenPeople, id), person_id: undefined }) });
  }
  for (const id of idSet(list.events)) {
    const label = events.data?.find((e) => e.id === id)?.label ?? "Event";
    chips.push({ key: `e${id}`, label, off: () => go({ events: without(idSet(list.events), id) }) });
  }
  if (list.status) chips.push({ key: "s", label: STATUS_NAMES[list.status] ?? list.status, off: () => go({ status: undefined }) });
  if (list.duplicates) chips.push({ key: "d", label: "Possible duplicates", off: () => go({ duplicates: undefined }) });
  if (list.ids) chips.push({ key: "i", label: `${list.ids.split(",").length} chosen photos`, off: () => go({ ids: undefined }) });
  // Backs belong to one album: offered when exactly one album is ticked.
  const one = chosenAlbumSet.size === 1 ? albums.data?.find((x) => chosenAlbumSet.has(x.id)) : undefined;
  const backs = one && one.loose_backs + one.paired_backs > 0 ? one : undefined;
  if (!chips.length && !backs) return null;
  return (
    <div className="active-filters">
      {chips.map((c) => (
        <button key={c.key} className="chip is-on active-filters__chip" onClick={c.off} title={`Stop filtering by ${c.label}`}>
          {c.label}
          <span aria-hidden>✕</span>
        </button>
      ))}
      {chosenPeople.size > 1 && <span className="muted small">{list.people_mode === "any" ? "any of these people" : "all of these people together"}</span>}
      {chips.length > 1 && (
        <button className="linkish" onClick={() => go(clearedFilters(list))}>
          Clear all
        </button>
      )}
      {backs && backs.paired_backs > 0 && (
        <a className="linkish" href={backsHref(backs.id, false, "paired")}>
          {backs.paired_backs} backs with their front{backs.paired_backs_undated > 0 && ` (${backs.paired_backs_undated} to do)`}
        </a>
      )}
      {backs && backs.loose_backs > 0 && (
        <a className="linkish" href={backsHref(backs.id)}>
          {backs.loose_backs} loose backs{backs.loose_backs_undated > 0 && ` (${backs.loose_backs_undated} to do)`}
        </a>
      )}
    </div>
  );
}

function Progress({ done, total }: { done: number; total: number }) {
  return (
    <span className="progress-mini" title={`${done} of ${total} dated`}>
      <span style={{ width: total ? `${(done / total) * 100}%` : 0 }} />
      <em>{total}</em>
    </span>
  );
}

function Tile({
  p,
  selected,
  big,
  onClick,
  onOpen,
  onTap,
  onLongPress,
}: {
  p: PhotoSummary;
  selected: boolean;
  /** Shown large (pinched to one or two columns): use the sharper preview image. */
  big: boolean;
  /** Mouse click: select (with shift / ⌘ for ranges and adding). */
  onClick: (e: React.MouseEvent) => void;
  /** Mouse double-click. */
  onOpen: () => void;
  /** Finger tap. */
  onTap: () => void;
  /** Finger press and hold. */
  onLongPress: () => void;
}) {
  const press = useLongPress(onLongPress);
  return (
    <button
      className={`tile ${selected ? "is-selected" : ""} tile--${p.status}`}
      {...press.handlers}
      onClick={(e) => {
        if (press.consumeLongPress()) return;
        if (press.wasTouch()) onTap();
        else onClick(e);
      }}
      onDoubleClick={onOpen}
      data-id={p.id}
    >
      <img src={imgUrl(p.sha256, big ? "preview" : "thumb", p.rotation)} loading="lazy" alt={p.rel_path} draggable={false} />
      <span className="tile__meta">
        <span
          className="tile__date"
          title={
            p.status === "reviewed" ? "Date you set" : p.status === "inferred"
              ? `Estimated date (≈), ${Math.round((p.confidence ?? 0) * 100)}% sure` : "No date yet"
          }
        >
          {p.date_label || "—"}
          {p.confidence != null && <span className="tile__conf"> {Math.round(p.confidence * 100)}%</span>}
        </span>
        {p.event_label && <span className="tile__event">{p.event_label}</span>}
      </span>
      <span className="tile__badges">
        {p.back_sha256 && <span className="badge" title="Has back scan">B</span>}
        {p.dup_group_id !== null && (
          <span className={`badge ${p.is_dup_keeper ? "badge--ok" : "badge--warn"}`} title="Possible duplicate">
            D
          </span>
        )}
        {p.rotation_source === "auto" && <span className="badge" title="Auto-rotated">↻</span>}
      </span>
    </button>
  );
}

// Scroll position of each grid list (by its URL query), so coming back from a photo returns to it.
const scrollMemory = new Map<string, number>();

export function Grid({ list }: { list: ListParams }) {
  const photos = usePhotos(list);
  const items = photos.data?.items ?? [];
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const anchor = useRef<number | null>(null);
  const update = useUpdatePhotos();
  const listKey = JSON.stringify(list);
  const [drawerOpen, setDrawerOpen] = useDrawer();
  const touch = useTouch();
  // Touch "Select mode": entered by a long press (or the Select button); taps then toggle tiles.
  const [selectMode, setSelectMode] = useState(false);
  // The phone bottom sheet with the date / event editor can be folded away to see the grid.
  const [sheetOpen, setSheetOpen] = useState(true);

  useEffect(() => {
    setSelected(new Set());
    setSelectMode(false);
    anchor.current = null;
  }, [listKey]);

  // Back from a photo: restore this list's scroll position once its tiles are rendered, then make
  // sure the photo last viewed (possibly several swipes on) is on screen.
  const gridEl = useRef<HTMLDivElement>(null);
  // Pinch the grid (phones and tablets) for fewer, bigger photos or more, smaller ones.
  const look = useNewGrid(); // the new grid (a preview): its own photo-size slider instead of pinch
  const pinch = useGridPinch(gridEl, touch && !look.on, usePhone());
  const scrollKey = listQuery(list);
  // Once per visit to the grid: switching filters inside the grid keeps today's behaviour.
  const restored = useRef(false);
  useLayoutEffect(() => {
    const el = gridEl.current;
    // Wait for this list's own data (not the previous list's placeholder) before restoring.
    if (!el || restored.current || !photos.data || photos.isPlaceholderData) return;
    restored.current = true;
    const saved = scrollMemory.get(scrollKey);
    if (saved !== undefined) el.scrollTop = saved;
    const last = takeLastPhoto(list);
    const tile = last !== null ? el.querySelector<HTMLElement>(`[data-id="${last}"]`) : null;
    if (tile) {
      const t = tile.getBoundingClientRect();
      const g = el.getBoundingClientRect();
      if (t.top < g.top || t.bottom > g.bottom) tile.scrollIntoView({ block: "center" });
    }
  }, [scrollKey, list, photos.data, photos.isPlaceholderData]);

  const indexOf = useMemo(() => new Map(items.map((p, i) => [p.id, i])), [items]);
  const selectedIds = useMemo(() => items.filter((p) => selected.has(p.id)).map((p) => p.id), [items, selected]);

  const onTileClick = (p: PhotoSummary, e: React.MouseEvent) => {
    const i = indexOf.get(p.id)!;
    if (e.shiftKey && anchor.current !== null) {
      const [a, b] = [anchor.current, i].sort((x, y) => x - y);
      const next = new Set(e.metaKey || e.ctrlKey ? selected : []);
      for (let k = a; k <= b; k++) next.add(items[k].id);
      setSelected(next);
      return;
    }
    if (e.metaKey || e.ctrlKey) {
      const next = new Set(selected);
      next.has(p.id) ? next.delete(p.id) : next.add(p.id);
      setSelected(next);
    } else {
      setSelected(new Set([p.id]));
    }
    anchor.current = i;
  };

  const toggle = (p: PhotoSummary) => {
    const next = new Set(selected);
    next.has(p.id) ? next.delete(p.id) : next.add(p.id);
    setSelected(next);
    anchor.current = indexOf.get(p.id)!;
  };

  const onTileTap = (p: PhotoSummary) => {
    if (pinch.suppressed()) return;
    if (selectMode) toggle(p);
    else navigate(photoHref(p.id, list));
  };

  const onTileLongPress = (p: PhotoSummary) => {
    if (pinch.suppressed()) return;
    const i = indexOf.get(p.id)!;
    if (!selectMode) {
      setSelectMode(true);
      setSheetOpen(true);
      setSelected(new Set([p.id]));
      anchor.current = i;
      return;
    }
    if (anchor.current === null) return toggle(p);
    // A second long press selects everything from the last tile touched to this one.
    const [a, b] = [anchor.current, i].sort((x, y) => x - y);
    const next = new Set(selected);
    for (let k = a; k <= b; k++) next.add(items[k].id);
    setSelected(next);
    anchor.current = i;
  };

  const exitSelectMode = () => {
    setSelectMode(false);
    setSelected(new Set());
    anchor.current = null;
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e)) return;
      if ((e.metaKey || e.ctrlKey) && e.key === "a") {
        e.preventDefault();
        setSelected(new Set(items.map((p) => p.id)));
      } else if (e.key === "Escape") {
        setSelected(new Set());
      } else if (e.key === "Enter" && selectedIds.length) {
        navigate(photoHref(selectedIds[0], list));
      } else if ((e.key === "r" || e.key === "R") && selectedIds.length && !e.metaKey && !e.ctrlKey && !e.repeat) {
        update.mutate({ ids: selectedIds, rotate_by: e.shiftKey ? 270 : 90 });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, selectedIds, list, update]);

  return (
    <div className="layout">
      <Sidebar list={list} open={drawerOpen} onClose={() => setDrawerOpen(false)} />
      <main className="main">
        <div className="main__head">
          <FiltersButton onClick={() => setDrawerOpen(true)} />
          <span className="muted">
            {photos.isLoading ? "Loading…" : `${photos.data?.total ?? 0} photos`}
            {selectedIds.length > 0 && ` · ${selectedIds.length} selected`}
          </span>
          <label className="lookswitch" title="Preview of a new, photo-first grid">
            <input type="checkbox" checked={look.on} onChange={look.toggle} /> New grid
          </label>
          {look.on && (
            <input className="rowsize" type="range" min={110} max={360} step={10} value={look.row}
              onChange={(e) => look.setRow(Number(e.target.value))} aria-label="Photo size" title="Photo size" />
          )}
          {selectMode ? (
            <>
              <span className="spacer" />
              <button className="btn btn--ghost" onClick={() => setSelected(new Set(items.map((p) => p.id)))}>
                Select all
              </button>
              <button className="btn btn--ghost" onClick={() => setSelected(new Set())} disabled={!selectedIds.length}>
                Clear
              </button>
              <button className="btn btn--primary" onClick={exitSelectMode}>
                Done
              </button>
            </>
          ) : (
            <>
              {selectedIds.length > 0 && (
                <button className="btn btn--ghost" onClick={() => setSelected(new Set())}>
                  Clear selection
                </button>
              )}
              {touch && items.length > 0 && (
                <>
                  <span className="spacer" />
                  <button
                    className="btn btn--ghost"
                    onClick={() => {
                      setSelectMode(true);
                      setSheetOpen(true);
                    }}
                  >
                    Select
                  </button>
                </>
              )}
            </>
          )}
        </div>
        <ActiveFilters list={list} />
        {photos.isError && <div className="error">{String(photos.error)}</div>}
        {!photos.isLoading && items.length === 0 && (
          <div className="empty">
            {filterCount(list) > 0 ? (
              <>
                No photos match these filters.{" "}
                <button className="linkish" onClick={() => navigate(gridHref(clearedFilters(list)))}>Clear filters</button>
              </>
            ) : (
              <>
                No photos here yet. Run <a href="#/jobs">Import</a> to scan the photo folder.
              </>
            )}
          </div>
        )}
        <div
          ref={gridEl}
          className={`grid ${look.on ? "grid--new" : pinch.className} ${selectMode ? "is-selecting" : ""} ${selectedIds.length ? "has-sheet" : ""}`}
          style={look.on ? undefined : pinch.style}
          onScroll={(e) => scrollMemory.set(scrollKey, e.currentTarget.scrollTop)}
        >
          {look.on ? (
            <NewGrid items={items} list={list} row={look.row} selected={selected} selecting={selectMode}
              onClick={onTileClick} onOpen={(p) => navigate(photoHref(p.id, list))} onTap={onTileTap} onLongPress={onTileLongPress} />
          ) : items.map((p) => (
            <Tile
              key={p.id}
              p={p}
              selected={selected.has(p.id)}
              big={pinch.big}
              onClick={(e) => onTileClick(p, e)}
              onOpen={() => navigate(photoHref(p.id, list))}
              onTap={() => onTileTap(p)}
              onLongPress={() => onTileLongPress(p)}
            />
          ))}
        </div>
        {selectedIds.length > 0 && (
          <div className={`bulkbar bulkbar--sheet ${sheetOpen ? "" : "is-collapsed"}`}>
            <button className="bulkbar__handle" onClick={() => setSheetOpen((o) => !o)} aria-expanded={sheetOpen}>
              {sheetOpen ? "▾ Hide" : `▴ Date & event · ${selectedIds.length} selected`}
            </button>
            <div className="bulkbar__body">
              <DateEventEditor ids={selectedIds} compact />
              <PlaceEditor ids={selectedIds} compact />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
