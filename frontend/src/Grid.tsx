import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { imgUrl, type ListParams, type PhotoSummary } from "./api";
import { FiltersButton, SidebarDrawer } from "./Drawer";
import { DateEventEditor, PlaceEditor } from "./Editor";
import { useAlbums, useDrawer, useLongPress, usePhotos, useTouch, useUpdatePhotos } from "./hooks";
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
  const albums = useAlbums();
  const people = usePeople();
  const touch = useTouch();
  const person = people.data?.find((p) => p.id === list.person_id);
  const go = (patch: Partial<ListParams>) => navigate(gridHref({ ...list, ...patch }));
  return (
    <SidebarDrawer open={open} onClose={onClose}>
      {person && (
        <section>
          <h3 className="sidebar__heading">Person</h3>
          <div className="filter-chip">
            <a href={`#/people/${person.id}`}>{person.name}</a>
            <a href={gridHref({ ...list, person_id: undefined })} title="Show everyone">
              ×
            </a>
          </div>
        </section>
      )}
      <section>
        <h3 className="sidebar__heading">Albums</h3>
        <ul className="nav-list">
          <li>
            <a className={list.album_id === undefined ? "is-active" : ""} href={gridHref({ ...list, album_id: undefined })}>
              All photos
            </a>
          </li>
          {albums.data?.map((a) => (
            <li key={a.id}>
              <a
                className={`${list.album_id === a.id ? "is-active" : ""} ${a.missing ? "is-missing" : ""}`}
                href={gridHref({ ...list, album_id: a.id })}
                title={a.rel_path}
              >
                <span className="nav-list__name">{a.name}</span>
                <span className="nav-list__count">
                  <Progress done={a.dated} total={a.count} />
                </span>
              </a>
            </li>
          ))}
        </ul>
      </section>
      {(() => {
        const a = albums.data?.find((x) => x.id === list.album_id);
        return a && a.loose_backs + a.paired_backs > 0 ? (
          <section className="stack">
            {a.paired_backs > 0 && (
              <a className="btn btn--ghost btn--block" href={backsHref(a.id, false, "paired")}>
                {a.paired_backs} backs with their front
                {a.paired_backs_undated > 0 && <span className="muted"> · {a.paired_backs_undated} to do</span>}
              </a>
            )}
            {a.loose_backs > 0 && (
              <a className="btn btn--ghost btn--block" href={backsHref(a.id)}>
                {a.loose_backs} loose backs
                {a.loose_backs_undated > 0 && <span className="muted"> · {a.loose_backs_undated} to do</span>}
              </a>
            )}
          </section>
        ) : null;
      })()}
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
          everything between
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
  onClick,
  onOpen,
  onTap,
  onLongPress,
}: {
  p: PhotoSummary;
  selected: boolean;
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
      <img src={imgUrl(p.sha256, "thumb", p.rotation)} loading="lazy" alt={p.rel_path} draggable={false} />
      <span className="tile__meta">
        <span className="tile__date">
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
    const tile = last !== null ? el.querySelector<HTMLElement>(`.tile[data-id="${last}"]`) : null;
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
    if (selectMode) toggle(p);
    else navigate(photoHref(p.id, list));
  };

  const onTileLongPress = (p: PhotoSummary) => {
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
        {photos.isError && <div className="error">{String(photos.error)}</div>}
        {!photos.isLoading && items.length === 0 && (
          <div className="empty">
            No photos here yet. Run <a href="#/jobs">Import</a> to scan the photo folder.
          </div>
        )}
        <div
          ref={gridEl}
          className={`grid ${selectMode ? "is-selecting" : ""} ${selectedIds.length ? "has-sheet" : ""}`}
          onScroll={(e) => scrollMemory.set(scrollKey, e.currentTarget.scrollTop)}
        >
          {items.map((p) => (
            <Tile
              key={p.id}
              p={p}
              selected={selected.has(p.id)}
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
