import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type BulkUpdate, type ParsedDate, type PhotoDetail } from "../src/api";
import { useEventInstances, useEventKinds, useUpdatePhotos } from "../src/hooks";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Where a photo's date came from, in words. */
export const DATE_SOURCE: Record<string, string> = {
  manual: "set by you",
  accepted: "accepted estimate",
  back: "from the back",
  event: "from the event",
  inferred: "estimated",
};

function useApply(ids: number[], onDone?: () => void) {
  const update = useUpdatePhotos();
  const [error, setError] = useState("");
  const apply = async (body: Omit<BulkUpdate, "ids">) => {
    setError("");
    try {
      await update.mutateAsync({ ids, ...body });
      onDone?.();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  return { apply, busy: update.isPending, error };
}

/** Dating with big buttons: the estimate, nearby photos' dates, the previous photo's date, a year
 *  strip and months, an exact day, or type anything ("1987", "1980s", "~1987", "1985-1988"). */
export function DatePanel({ ids, photo, previous, onDone, showNow = true }: {
  ids: number[];
  /** Repeat the photo's current date at the top (off where it's already on screen). */
  showNow?: boolean;
  /** One photo: its date, estimate and nearby dates. Absent for a selection of several. */
  photo?: PhotoDetail;
  /** The date of the photo before this one in the list, if it has one ("Same as previous"). */
  previous?: string | null;
  onDone?: () => void;
}) {
  const { apply, busy, error } = useApply(ids, onDone);
  const estimateYear = photo?.status === "inferred" ? (photo.year ?? null) : null;
  const center = photo?.year ?? photo?.nearby_dates[0]?.year ?? (previous ? Number(previous.slice(0, 4)) : null) ?? null;
  const [year, setYear] = useState<number | null>(null);
  const [typing, setTyping] = useState(false);
  useEffect(() => (setYear(null), setTyping(false)), [ids.join(",")]);
  const confirmed = photo?.status === "reviewed";

  return (
    <div className="t-date">
      {photo && showNow && (
        <p className="t-date__now">
          {photo.date_label ? (
            <>
              <b className={`t-status-${photo.status}`}>{photo.date_label}</b>{" "}
              <span className="t-muted">{DATE_SOURCE[photo.date_source ?? ""] ?? ""}</span>
            </>
          ) : (
            <span className="t-muted">No date yet</span>
          )}
        </p>
      )}
      {photo && photo.status === "inferred" && photo.inferred_label && (
        <button className="t-btn t-btn--primary t-btn--big" disabled={busy}
          onClick={() => apply({ date: photo.inferred_label, date_source: "accepted" })}>
          Use ≈{photo.inferred_label}
          <span className="t-btn__sub">{photo.inferred_source}</span>
        </button>
      )}
      {(photo?.nearby_dates.length || previous) && !confirmed ? (
        <div className="t-choices">
          {photo?.nearby_dates.map((n) => (
            <button key={n.photo_id} className="t-btn" disabled={busy} onClick={() => apply({ date: n.label, date_source: "accepted" })}>
              {n.label}
              <span className="t-btn__sub">{n.offset < 0 ? `photo ${-n.offset} before` : `photo ${n.offset} after`}</span>
            </button>
          ))}
          {previous && !photo?.nearby_dates.some((n) => n.label === previous) && (
            <button className="t-btn" disabled={busy} onClick={() => apply({ date: previous })}>
              {previous}
              <span className="t-btn__sub">same as previous</span>
            </button>
          )}
        </div>
      ) : null}
      <YearStrip center={center} estimate={estimateYear} chosen={year} onPick={(y) => setYear(y === year ? null : y)} />
      {year !== null && (
        <div className="t-months">
          <button className="t-btn t-btn--primary" disabled={busy} onClick={() => apply({ date: String(year) })}>
            Just {year}
          </button>
          {MONTHS.map((m, i) => (
            <button key={m} className="t-btn" disabled={busy} onClick={() => apply({ date: `${year}-${String(i + 1).padStart(2, "0")}` })}>
              {m}
            </button>
          ))}
        </div>
      )}
      <div className="t-date__more">
        <label className="t-btn t-daypick">
          Exact day…
          <input
            type="date"
            min="1850-01-01"
            max={new Date().toISOString().slice(0, 10)}
            onChange={(e) => e.target.value && apply({ date: e.target.value })}
          />
        </label>
        <button className="t-btn" onClick={() => setTyping((t) => !t)}>
          Type a date…
        </button>
        {confirmed && (
          <button className="t-btn t-btn--danger-text" disabled={busy} onClick={() => apply({ clear_date: true })}>
            Clear date
          </button>
        )}
      </div>
      {typing && <TypedDate busy={busy} onApply={(date) => apply({ date })} />}
      {error && <p className="t-error">{error}</p>}
    </div>
  );
}

/** Years around the likely one, scrollable; the estimate is marked. */
function YearStrip({ center, estimate, chosen, onPick }: {
  center: number | null;
  estimate: number | null;
  chosen: number | null;
  onPick: (y: number) => void;
}) {
  const now = new Date().getFullYear();
  const mid = center ?? 1985;
  const years: number[] = [];
  for (let y = Math.max(1850, mid - 15); y <= Math.min(now, mid + 15); y++) years.push(y);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current?.querySelector<HTMLElement>(`[data-year="${mid}"]`);
    el?.scrollIntoView({ inline: "center", block: "nearest" });
  }, [mid]);
  return (
    <div className="t-years" ref={ref} aria-label="Year">
      {years.map((y) => (
        <button
          key={y}
          data-year={y}
          className={`t-year ${chosen === y ? "is-on" : ""} ${estimate === y ? "is-estimate" : ""}`}
          onClick={() => onPick(y)}
        >
          {y}
        </button>
      ))}
    </div>
  );
}

function TypedDate({ busy, onApply }: { busy: boolean; onApply: (date: string) => void }) {
  const [text, setText] = useState("");
  const [parsed, setParsed] = useState<ParsedDate | null>(null);
  useEffect(() => {
    if (!text.trim()) return setParsed(null);
    const t = setTimeout(() => api.parseDate(text).then(setParsed).catch(() => setParsed(null)), 150);
    return () => clearTimeout(t);
  }, [text]);
  return (
    <form className="t-typed" onSubmit={(e) => (e.preventDefault(), parsed?.ok && onApply(text.trim()))}>
      <input
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="1987, 1987-06, 1980s, ~1987, 1985-1988"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint="done"
      />
      <button className="t-btn t-btn--primary" disabled={busy || !parsed?.ok}>
        Set
      </button>
      <span className={`t-typed__hint ${parsed?.ok === false ? "t-error" : "t-muted"}`}>
        {parsed ? (parsed.ok ? `→ ${parsed.label}` : parsed.error) : " "}
      </span>
    </form>
  );
}

/** Event chips (Christmas, Birthday, …); the photo's own event is marked. */
export function EventPanel({ ids, photo, onDone }: { ids: number[]; photo?: PhotoDetail; onDone?: () => void }) {
  const kinds = useEventKinds();
  const instances = useEventInstances();
  const qc = useQueryClient();
  const { apply, busy, error } = useApply(ids, onDone);
  const currentKind = instances.data?.find((i) => i.id === photo?.event_instance_id)?.kind_id;
  return (
    <div className="t-event">
      {photo && <p className="t-muted">{photo.event_label ? `Now: ${photo.event_label}` : "No event"}</p>}
      <div className="t-chips">
        {kinds.data?.map((k) => (
          <button key={k.id} className={`t-chip t-chip--big ${currentKind === k.id ? "is-on" : ""}`} disabled={busy}
            onClick={() => apply({ event_kind_id: k.id })}>
            {k.name}
          </button>
        ))}
        <button
          className="t-chip t-chip--big"
          onClick={async () => {
            const name = window.prompt("New event type (e.g. Family reunion)")?.trim();
            if (!name) return;
            const k = await api.addEventKind(name);
            await qc.invalidateQueries({ queryKey: ["eventKinds"] });
            apply({ event_kind_id: k.id });
          }}
        >
          ＋ New
        </button>
      </div>
      {photo?.event_label && (
        <button className="t-btn t-btn--danger-text" disabled={busy} onClick={() => apply({ clear_event: true })}>
          Clear event
        </button>
      )}
      {error && <p className="t-error">{error}</p>}
    </div>
  );
}
