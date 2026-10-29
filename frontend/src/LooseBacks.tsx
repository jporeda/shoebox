import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, imgUrl, type Back, type BackKind } from "./api";
import { useAlbums } from "./hooks";
import { backsHref, navigate, photoHref } from "./route";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const fmt = (y: number, m?: number | null) => (m ? `${y}-${String(m).padStart(2, "0")}` : String(y));
const pretty = (label: string) => {
  const [y, m] = label.split("-");
  return m ? `${MONTHS[Number(m) - 1]} ${y}` : y;
};

/** Review page: each back next to the front(s) it dates. */
export function LooseBacks({ kind, albumId, undated }: { kind: BackKind; albumId?: number; undated: boolean }) {
  const albums = useAlbums();
  const backs = useQuery({
    queryKey: ["backs", kind, albumId, undated],
    queryFn: () => api.backs(kind, { album_id: albumId, undated }),
    placeholderData: (prev) => prev,
  });
  const count = (a: { loose_backs: number; paired_backs: number }) => (kind === "loose" ? a.loose_backs : a.paired_backs);
  const openOf = (a: { loose_backs_undated: number; paired_backs_undated: number }) =>
    kind === "loose" ? a.loose_backs_undated : a.paired_backs_undated;
  const withBacks = albums.data?.filter((a) => count(a) > 0) ?? [];
  const total = withBacks.reduce((n, a) => n + count(a), 0);
  const open = withBacks.reduce((n, a) => n + openOf(a), 0);
  const all = albums.data ?? [];
  const tabTotal = (k: BackKind) =>
    all.reduce((n, a) => n + (k === "loose" ? a.loose_backs_undated : a.paired_backs_undated), 0);

  return (
    <div className="layout">
      <aside className="sidebar">
        <section>
          <div className="segmented">
            <button className={kind === "loose" ? "is-active" : ""} onClick={() => navigate(backsHref(albumId, undated, "loose"))}>
              Loose ({tabTotal("loose")})
            </button>
            <button className={kind === "paired" ? "is-active" : ""} onClick={() => navigate(backsHref(albumId, undated, "paired"))}>
              Paired ({tabTotal("paired")})
            </button>
          </div>
          <p className="muted small">
            {kind === "loose"
              ? "Backs with no front of the same name. Each one dates the undated fronts within a few files of it (shown on the right)."
              : "Backs scanned with their own front. The date you enter here becomes that photo's estimate."}{" "}
            Type the date on the back and press Enter to save and move on; “No date” when there isn't one.
          </p>
          <label className="check">
            <input type="checkbox" checked={undated} onChange={(e) => navigate(backsHref(albumId, e.target.checked, kind))} />
            Only backs still to do
          </label>
        </section>
        <section>
          <ul className="nav-list">
            <li>
              <a className={albumId === undefined ? "is-active" : ""} href={backsHref(undefined, undated, kind)}>
                <span className="nav-list__name">All albums</span>
                <span className="muted small">{open}/{total}</span>
              </a>
            </li>
            {withBacks.map((a) => (
              <li key={a.id}>
                <a className={albumId === a.id ? "is-active" : ""} href={backsHref(a.id, undated, kind)}>
                  <span className="nav-list__name">{a.name}</span>
                  <span className="muted small" title="still to do / total">
                    {openOf(a)}/{count(a)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      </aside>
      <main className="main backs">
        <div className="main__head muted">
          {backs.isLoading ? "Loading…" : `${backs.data?.length ?? 0} ${kind} backs`}
        </div>
        {!backs.isLoading && backs.data?.length === 0 && (
          <div className="empty">{undated ? "Nothing left to do here." : `No ${kind} backs.`}</div>
        )}
        <div className="backs__list">
          {backs.data?.map((b, i) => <BackRow key={b.id} back={b} index={i} />)}
        </div>
      </main>
    </div>
  );
}

function BackRow({ back, index }: { back: Back; index: number }) {
  const qc = useQueryClient();
  const [value, setValue] = useState(back.date_label);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setValue(back.date_label), [back.date_label]);

  const save = async (date: string | null) => {
    setSaving(true);
    setError("");
    try {
      await api.setBackDate(back.kind, back.id, date);
      await qc.invalidateQueries({ queryKey: ["backs"] });
      qc.invalidateQueries({ queryKey: ["albums"] });
      qc.invalidateQueries({ queryKey: ["photos"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["edits"] });
      const next = document.querySelector<HTMLInputElement>(`[data-back-input="${index + 1}"]`);
      next?.focus();
      next?.scrollIntoView({ block: "center", behavior: "smooth" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const status =
    back.source === "manual"
      ? back.date_label
        ? "confirmed"
        : "marked: no date"
      : back.source === "ocr" || back.source === "claude"
        ? `read by ${back.source === "ocr" ? "OCR" : "Claude"} (${Math.round((back.confidence ?? 0) * 100)}%)`
        : "no date found";
  const fresh = back.candidates.filter((c) => fmt(c.year as number, c.month as number) !== back.date_label);

  return (
    <section className={`back-row ${back.source === "manual" ? "is-confirmed" : ""}`}>
      <figure className="back-row__img">
        <img src={imgUrl(back.sha256, "preview", 0)} alt={back.rel_path} loading="lazy" />
        <figcaption>{back.rel_path}</figcaption>
      </figure>
      <div className="back-row__body">
        <form
          className="back-row__form"
          onSubmit={(e) => {
            e.preventDefault();
            save(value.trim() || null);
          }}
        >
          <label className="field">
            <span className="field__label">
              Date on this back <em className={`muted source-${back.source ?? "none"}`}>{status}</em>
            </span>
            <div className="field__inline">
              <input
                ref={input}
                data-back-input={index}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder="1978-10 or 1978"
              />
              <button className="btn btn--primary" disabled={saving} type="submit">
                {(back.source === "ocr" || back.source === "claude") && value === back.date_label ? "Confirm" : "Save"}
              </button>
              <button className="btn btn--ghost" type="button" disabled={saving} onClick={() => save(null)} title="This back has no date">
                No date
              </button>
            </div>
          </label>
          {fresh.length > 0 && (
            <div className="chips">
              <span className="muted small">Also seen:</span>
              {fresh.map((c) => {
                const label = fmt(c.year as number, c.month as number);
                return (
                  <button key={label} type="button" className="chip" onClick={() => save(label)}>
                    {pretty(label)}{" "}
                    <span className="muted">{c.source === "claude" ? `${c.kind === "handwritten" ? "handwritten" : "Claude"}` : `×${c.votes as number}`}</span>
                  </button>
                );
              })}
            </div>
          )}
          {error && <span className="error">{error}</span>}
          {back.transcription && <p className="transcription">“{back.transcription}”</p>}
        </form>
        <div className="neighbours">
          {back.neighbours.map((n) => (
            <a key={n.id} className={`neighbour tile--${n.status}`} href={photoHref(n.id, { album_id: back.album_id })} title={n.inferred_source ?? ""}>
              <img src={imgUrl(n.sha256, "thumb", n.rotation)} alt={n.rel_path} loading="lazy" />
              <span className="neighbour__meta">
                <span className="neighbour__dist">
                  {back.kind === "paired" ? "this photo" : `${n.distance > 0 ? `+${n.distance}` : n.distance} files`}
                </span>
                <span className="tile__date">{n.date_label || "—"}</span>
              </span>
            </a>
          ))}
          {back.neighbours.length === 0 && <span className="muted small">No fronts within reach of this back.</span>}
        </div>
      </div>
    </section>
  );
}
