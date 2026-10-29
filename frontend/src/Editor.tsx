import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type ParsedDate } from "./api";
import { useEventKinds, useUpdatePhotos } from "./hooks";

function useParsedDate(text: string): ParsedDate | null {
  const [parsed, setParsed] = useState<ParsedDate | null>(null);
  useEffect(() => {
    if (!text.trim()) {
      setParsed(null);
      return;
    }
    const t = setTimeout(() => api.parseDate(text).then(setParsed).catch(() => setParsed(null)), 150);
    return () => clearTimeout(t);
  }, [text]);
  return parsed;
}

type Props = {
  ids: number[];
  /** Shown as a hint for the current value in single-photo mode. */
  currentDate?: string;
  currentEvent?: string | null;
  autoFocus?: boolean;
  compact?: boolean;
};

/** Date + event entry used by both the bulk bar and photo detail. */
export function DateEventEditor({ ids, currentDate, currentEvent, autoFocus, compact }: Props) {
  const [date, setDate] = useState("");
  const [kindId, setKindId] = useState<string>("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState("");
  const parsed = useParsedDate(date);
  const kinds = useEventKinds();
  const update = useUpdatePhotos();
  const qc = useQueryClient();
  const dateRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDate("");
    setKindId("");
    setLabel("");
    setError("");
  }, [ids.join(",")]);

  useEffect(() => {
    const focusDate = (e: KeyboardEvent) => {
      if (e.key === "y" && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLSelectElement)) {
        e.preventDefault();
        dateRef.current?.focus();
      }
    };
    window.addEventListener("keydown", focusDate);
    return () => window.removeEventListener("keydown", focusDate);
  }, []);

  const run = async (body: Parameters<typeof update.mutateAsync>[0]) => {
    setError("");
    try {
      await update.mutateAsync(body);
      setDate("");
      setLabel("");
      setKindId("");
      dateRef.current?.blur();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  const apply = () => {
    if (!date.trim() && !kindId) return;
    run({
      ids,
      date: date.trim() || undefined,
      event_kind_id: kindId ? Number(kindId) : undefined,
      event_label: label.trim() || undefined,
    });
  };

  const addKind = async () => {
    const name = window.prompt("New event type (e.g. Family reunion)")?.trim();
    if (!name) return;
    try {
      const k = await api.addEventKind(name);
      await qc.invalidateQueries({ queryKey: ["eventKinds"] });
      setKindId(String(k.id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  const canApply = (date.trim() !== "" && parsed?.ok) || (date.trim() === "" && kindId !== "");

  return (
    <div className={`editor ${compact ? "editor--compact" : ""}`}>
      <form
        className="editor__row"
        onSubmit={(e) => {
          e.preventDefault();
          if (canApply) apply();
        }}
      >
        <label className="field">
          <span className="field__label">
            Date {currentDate ? <em className="muted">now {currentDate}</em> : null}
          </span>
          <input
            ref={dateRef}
            autoFocus={autoFocus}
            value={date}
            onChange={(e) => setDate(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && (e.currentTarget.blur(), setDate(""))}
            placeholder="1987 · 1987-06 · 1980s · 1985-1988 · ~1987"
            aria-invalid={parsed?.ok === false}
          />
          <span className={`field__hint ${parsed?.ok === false ? "is-error" : ""}`}>
            {parsed ? (parsed.ok ? `→ ${parsed.label}` : parsed.error) : " "}
          </span>
        </label>
        <label className="field">
          <span className="field__label">
            Event {currentEvent ? <em className="muted">now {currentEvent}</em> : null}
          </span>
          <div className="field__inline">
            <select value={kindId} onChange={(e) => setKindId(e.target.value)}>
              <option value="">—</option>
              {kinds.data?.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.name}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn--ghost" onClick={addKind} title="Add an event type">
              +
            </button>
          </div>
          <span className="field__hint">&nbsp;</span>
        </label>
        {kindId && (
          <label className="field">
            <span className="field__label">Label (optional)</span>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Sam's 5th birthday" />
            <span className="field__hint">&nbsp;</span>
          </label>
        )}
        <div className="field field--actions">
          <span className="field__label">&nbsp;</span>
          <button className="btn btn--primary" disabled={!canApply || update.isPending} type="submit">
            Apply{ids.length > 1 ? ` to ${ids.length}` : ""}
          </button>
          <span className="field__hint">&nbsp;</span>
        </div>
      </form>
      <div className="editor__row editor__row--secondary">
        <button className="btn btn--ghost" onClick={() => run({ ids, clear_date: true })} disabled={update.isPending}>
          Clear date
        </button>
        <button className="btn btn--ghost" onClick={() => run({ ids, clear_event: true })} disabled={update.isPending}>
          Clear event
        </button>
        <button className="btn btn--ghost" onClick={() => run({ ids, rotate_by: 270 })} title="Rotate left (Shift+R)">
          ⟲ Rotate
        </button>
        <button className="btn btn--ghost" onClick={() => run({ ids, rotate_by: 90 })} title="Rotate right (R)">
          ⟳ Rotate
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </div>
  );
}
