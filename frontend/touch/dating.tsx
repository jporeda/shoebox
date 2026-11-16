import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type BulkUpdate, type PhotoDetail } from "../src/api";
import { useEventInstances, useEventKinds, useUpdatePhotos } from "../src/hooks";
import { OPEN_ESTIMATE, OPEN_EXACT, useSwitch } from "./settings";

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

/** Dating with big buttons: the estimate, nearby photos' dates and the previous photo's date, then
 *  two boxes: Estimate (earliest and latest year, the months it could be) and Exact (year, month,
 *  day, or pick a day on the calendar). Each box starts open if the Advanced tab says so. */
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
  const confirmed = photo?.status === "reviewed";
  const [openEstimate] = useSwitch(OPEN_ESTIMATE);
  const [openExact] = useSwitch(OPEN_EXACT);

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
      {/* Keyed by the photos: the next photo starts from its own date, with the boxes as set to open. */}
      <EstimateBox key={`e${ids.join(",")}:${openEstimate}`} trusted={photo?.trusted ?? null} open={openEstimate} busy={busy} apply={apply} />
      <ExactBox key={`x${ids.join(",")}:${openExact}`} trusted={photo?.trusted ?? null} open={openExact} busy={busy} apply={apply} />
      {confirmed && (
        <button className="t-btn t-btn--danger-text t-date__clear" disabled={busy} onClick={() => apply({ clear_date: true })}>
          Clear date
        </button>
      )}
      {error && <p className="t-error">{error}</p>}
    </div>
  );
}

type Trusted = NonNullable<PhotoDetail["trusted"]>;
type Apply = (body: Omit<BulkUpdate, "ids">) => Promise<void>;
const yearOk = (t: string) => /^\d{4}$/.test(t.trim()) && +t >= 1850 && +t <= new Date().getFullYear();

/** A box that folds open and shut: the heading shows what it's for. */
function Fold({ title, hint, open, children }: { title: string; hint: string; open: boolean; children: React.ReactNode }) {
  return (
    <details className="t-fold" open={open}>
      <summary>
        <span className="t-fold__title">{title}</span> <span className="t-muted t-small">{hint}</span>
      </summary>
      <div className="t-fold__body">{children}</div>
    </details>
  );
}

/** A four-digit year, with the number keypad. */
function YearInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="t-field">
      <span className="t-field__label">{label}</span>
      <input inputMode="numeric" pattern="[0-9]*" maxLength={4} placeholder="e.g. 1985" autoComplete="off" enterKeyHint="done"
        value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
        aria-invalid={value !== "" && !yearOk(value)} />
    </label>
  );
}

/** Not sure of the date: earliest and latest year, and the months it could be. All optional, but a
 *  year is needed to save (months alone aren't a date). */
function EstimateBox({ trusted: t, open, busy, apply }: { trusted: Trusted | null; open: boolean; busy: boolean; apply: Apply }) {
  // From the photo's own date when it's a year, a range or a decade (with or without months).
  const rough = t && (t.precision === "year" || t.precision === "range" || t.precision === "decade" || t.precision === "month");
  const [from, setFrom] = useState(rough ? String(t.min ?? t.year) : "");
  const [to, setTo] = useState(rough && t.max !== t.min ? String(t.max ?? "") : "");
  const [months, setMonths] = useState<number[]>(rough ? (t.months ?? (t.month ? [t.month] : [])) : []);
  const years = [from, to].filter((y) => y.trim() !== "");
  const valid = years.length > 0 && years.every(yearOk);
  const [a, b] = years.map(Number).sort((x, y) => x - y);
  const date = !valid ? "" : b === undefined || a === b ? String(a) : `${a}-${b}`;
  const toggle = (m: number) => setMonths((ms) => (ms.includes(m) ? ms.filter((x) => x !== m) : [...ms, m].sort((x, y) => x - y)));
  return (
    <Fold title="Estimate" hint="not sure of the date" open={open}>
      <div className="t-fold__years">
        <YearInput label="Earliest year" value={from} onChange={setFrom} />
        <YearInput label="Latest year" value={to} onChange={setTo} />
      </div>
      <span className="t-field__label">Months it could be</span>
      <div className="t-monthpick" role="group" aria-label="Months it could be">
        {MONTHS.map((m, i) => (
          <button key={m} type="button" className={`t-chip ${months.includes(i + 1) ? "is-on" : ""}`} aria-pressed={months.includes(i + 1)}
            onClick={() => toggle(i + 1)}>
            {m}
          </button>
        ))}
      </div>
      <button className="t-btn t-btn--primary t-btn--block" disabled={busy || !valid}
        onClick={() => apply({ date, months: months.length ? months : null })}>
        {valid ? `Set ${date}${months.length ? `, ${monthsText(months)}` : ""}` : years.length ? "Years are 4 digits, 1850 to now" : "Type a year to set the date"}
      </button>
    </Fold>
  );
}

/** "Jun–Aug, Dec", as the server writes it in date labels. */
function monthsText(ms: number[]): string {
  const runs: number[][] = [];
  for (const m of ms) runs.length && m === runs[runs.length - 1].at(-1)! + 1 ? runs[runs.length - 1].push(m) : runs.push([m]);
  return runs.map((r) => MONTHS[r[0] - 1] + (r.length > 1 ? `–${MONTHS[r[r.length - 1] - 1]}` : "")).join(", ");
}

/** Sure of the date: year, and the month and day if known. "Pick a date" fills all three. */
function ExactBox({ trusted: t, open, busy, apply }: { trusted: Trusted | null; open: boolean; busy: boolean; apply: Apply }) {
  const exact = t && !t.months && (t.precision === "day" || t.precision === "month" || t.precision === "year");
  const [year, setYear] = useState(exact ? String(t.year) : "");
  const [month, setMonth] = useState(exact && t.month ? String(t.month) : "");
  const [day, setDay] = useState(exact && t.day ? String(t.day) : "");
  const days = month && yearOk(year) ? new Date(+year, +month, 0).getDate() : 31;
  useEffect(() => {
    if (day && +day > days) setDay(String(days)); // 31 Jan → February: the last day of February
  }, [days]); // eslint-disable-line react-hooks/exhaustive-deps
  const pad = (n: string) => n.padStart(2, "0");
  const date = !yearOk(year) ? "" : !month ? year : !day ? `${year}-${pad(month)}` : `${year}-${pad(month)}-${pad(day)}`;
  return (
    <Fold title="Exact" hint="sure of the year (and maybe the day)" open={open}>
      <label className="t-btn t-daypick t-btn--block">
        📅 Pick a date…
        <input
          type="date"
          min="1850-01-01"
          max={new Date().toISOString().slice(0, 10)}
          value={date.length === 10 ? date : ""}
          onChange={(e) => {
            const [y, m, d] = e.target.value.split("-");
            if (!y) return;
            setYear(y);
            setMonth(String(+m));
            setDay(String(+d));
          }}
        />
      </label>
      <div className="t-fold__exact">
        <YearInput label="Year" value={year} onChange={setYear} />
        <label className="t-field">
          <span className="t-field__label">Month</span>
          <select value={month} onChange={(e) => (setMonth(e.target.value), e.target.value || setDay(""))}>
            <option value="">—</option>
            {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
          </select>
        </label>
        <label className="t-field">
          <span className="t-field__label">Day</span>
          <select value={day} disabled={!month} onChange={(e) => setDay(e.target.value)}>
            <option value="">—</option>
            {Array.from({ length: days }, (_, i) => <option key={i} value={i + 1}>{i + 1}</option>)}
          </select>
        </label>
      </div>
      <button className="t-btn t-btn--primary t-btn--block" disabled={busy || !date} onClick={() => apply({ date })}>
        {date ? `Set ${date}` : year ? "Years are 4 digits, 1850 to now" : "Type a year or pick a date"}
      </button>
    </Fold>
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
