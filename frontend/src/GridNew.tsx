/** The new photo grid (a preview, switched on in the grid's header): photos fill justified rows of
 *  equal height with no cards around them, in sticky sections by album or year, with the date as a
 *  small pill on the photo and line icons for its back, duplicates and auto-rotation. */
import { useEffect, useState } from "react";
import "./gridNew.css";
import { albumLabel, imgUrl, type ListParams, type PhotoSummary } from "./api";
import { useAlbums, useLongPress } from "./hooks";

const LOOK_KEY = "shoebox.gridLook";
const ROW_KEY = "shoebox.gridRow";
const read = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const write = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    // private window: just this visit
  }
};

const CHANGED = "shoebox:gridlook"; // the switch is in one place (the touch app's menu), the grid in another

/** Whether this browser shows the new grid, and the row height (photo size) it uses on the desktop. */
export function useNewGrid() {
  const [on, setOn] = useState(() => read(LOOK_KEY) === "new");
  const [row, setRow] = useState(() => Number(read(ROW_KEY)) || 190);
  useEffect(() => {
    const sync = () => setOn(read(LOOK_KEY) === "new");
    window.addEventListener(CHANGED, sync);
    return () => window.removeEventListener(CHANGED, sync);
  }, []);
  return {
    on,
    toggle: () => {
      write(LOOK_KEY, on ? "classic" : "new");
      window.dispatchEvent(new Event(CHANGED));
    },
    row,
    setRow: (px: number) => (write(ROW_KEY, String(px)), setRow(px)),
  };
}

/** Line icons (Lucide, ISC licence), drawn with the text colour. */
const ICONS = {
  check: <path d="M20 6 9 17l-5-5" />,
  copy: (
    <>
      <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </>
  ),
  rotate: (
    <>
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </>
  ),
  back: (
    <>
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
      <path d="M16 13H8" />
      <path d="M16 17H8" />
    </>
  ),
};
export function Icon({ name, size = 14 }: { name: keyof typeof ICONS; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {ICONS[name]}
    </svg>
  );
}

type Section = { key: string; group: string; title: string; items: PhotoSummary[] };

/** Consecutive photos grouped the way the list is ordered: by album, by year, or (least sure
 *  first) not at all. */
function useSections(items: PhotoSummary[], list: ListParams): Section[] {
  const albums = useAlbums();
  const names = new Map((albums.data ?? []).map((a) => [a.id, albumLabel(a)]));
  const keyOf = (p: PhotoSummary): [string, string] =>
    list.order === "chrono"
      ? [String(p.year ?? "none"), p.year != null ? String(p.year) : "No date"]
      : list.order === "confidence"
        ? ["all", ""]
        : [String(p.album_id), names.get(p.album_id) ?? "Album"];
  const out: Section[] = [];
  for (const p of items) {
    const [group, title] = keyOf(p);
    const last = out[out.length - 1];
    if (last && last.group === group) last.items.push(p);
    else out.push({ key: `${group}:${out.length}`, group, title, items: [p] });
  }
  return out;
}

export function NewGrid({ items, list, row, selected, selecting, onClick, onOpen, onTap, onLongPress, touchApp }: {
  items: PhotoSummary[];
  list: ListParams;
  row: number;
  selected: Set<number>;
  selecting: boolean;
  /** Mouse click (desktop): select. Not used in the touch app, where every click is a tap. */
  onClick?: (p: PhotoSummary, e: React.MouseEvent) => void;
  onOpen: (p: PhotoSummary) => void;
  onTap: (p: PhotoSummary) => void;
  onLongPress: (p: PhotoSummary) => void;
  /** The touch app: a click or tap always taps, a press with any pointer selects, and tiles carry the
   *  "tile" class the pinch looks for. */
  touchApp?: boolean;
}) {
  const sections = useSections(items, list);
  const size = row < 70 ? "is-tiny is-small" : row < 170 ? "is-small" : "";
  return (
    <div className={`jgrid ${size} ${selecting || selected.size ? "has-selection" : ""}`} style={{ "--row": `${row}px` } as React.CSSProperties}>
      {sections.map((s) => (
        <section key={s.key} className="jsec">
          {s.title && (
            <h3 className="jsec__head">
              {s.title} <span className="jsec__count">{s.items.length}</span>
            </h3>
          )}
          <div className="jrow">
            {s.items.map((p) => (
              <NewTile key={p.id} p={p} big={row * devicePixelRatio > 700} selected={selected.has(p.id)} touchApp={touchApp}
                onClick={(e) => (onClick ? onClick(p, e) : onTap(p))} onOpen={() => onOpen(p)} onTap={() => onTap(p)}
                onLongPress={() => onLongPress(p)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function NewTile({ p, big, selected, touchApp, onClick, onOpen, onTap, onLongPress }: {
  p: PhotoSummary;
  big: boolean;
  selected: boolean;
  touchApp?: boolean;
  onClick: (e: React.MouseEvent) => void;
  onOpen: () => void;
  onTap: () => void;
  onLongPress: () => void;
}) {
  const press = useLongPress(onLongPress, undefined, touchApp ? { anyPointer: true } : undefined);
  const turned = p.rotation === 90 || p.rotation === 270;
  const ar = p.width && p.height ? (turned ? p.height / p.width : p.width / p.height) : 4 / 3;
  const conf = p.confidence != null ? `, ${Math.round(p.confidence * 100)}% sure` : "";
  const label = p.date_label || "No date";
  return (
    <button
      className={`jtile jtile--${p.status} ${touchApp ? "tile" : ""} ${selected ? "is-selected" : ""}`}
      style={{ "--ar": ar } as React.CSSProperties}
      {...press.handlers}
      onClick={(e) => {
        if (press.consumeLongPress()) return;
        if (touchApp || press.wasTouch()) onTap();
        else onClick(e);
      }}
      onDoubleClick={touchApp ? undefined : onOpen}
      data-id={p.id}
      aria-pressed={selected}
      aria-label={`${p.rel_path.split("/").pop()}, ${p.status === "inferred" ? `estimated ${label}${conf}` : label}`}
    >
      <img src={imgUrl(p.sha256, big ? "preview" : "thumb", p.rotation)} loading="lazy" alt="" draggable={false} />
      <span className="jtile__check" aria-hidden>
        {selected && <Icon name="check" size={13} />}
      </span>
      <span className="jtile__icons" aria-hidden>
        {p.back_sha256 && <span title="Has a back scan"><Icon name="back" /></span>}
        {p.dup_group_id !== null && <span title="Possible duplicate" className={p.is_dup_keeper ? "" : "is-warn"}><Icon name="copy" /></span>}
        {p.rotation_source === "auto" && <span title="Auto-rotated"><Icon name="rotate" /></span>}
      </span>
      <span className="jtile__date" title={p.status === "reviewed" ? "Date you set" : p.status === "inferred" ? `Estimated${conf}` : "No date yet"}>
        <i className="jtile__dot" />
        {label}
        {p.event_label && <span className="jtile__event"> · {p.event_label}</span>}
      </span>
    </button>
  );
}
