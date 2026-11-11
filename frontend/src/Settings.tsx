import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";

/** Settings stored with the collection (each collection has its own). */
export function Settings() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [margin, setMargin] = useState<number | null>(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (settings.data && margin === null) setMargin(settings.data.twin_margin_percent);
  }, [settings.data, margin]);
  const save = useMutation({
    mutationFn: (twin_margin_percent: number) => api.saveSettings({ twin_margin_percent }),
    onSuccess: (s) => {
      qc.setQueryData(["settings"], s);
      qc.invalidateQueries({ queryKey: ["jobs"] }); // the re-run of name suggestions it started
      setNote("Saved. Name suggestions are being worked out again with it.");
    },
    onError: (e) => setNote(e instanceof ApiError ? e.message : String(e)),
  });
  if (!settings.data || margin === null) return <p className="pad muted">Loading…</p>;
  const { twin_margin_default: dflt, twin_margin_percent: saved } = settings.data;
  return (
    <div className="settings pad">
      <h2>Settings</h2>
      <section className="settings__item">
        <h3>Twins: when to suggest “Ana or Ben?”</h3>
        <p className="muted small">
          For people you've paired as twins on a face. Each name guess has a match score (the % shown on guesses).
          When the face's best match is one twin and the other twin's score is within this many points, the app
          suggests “Ana or Ben?” instead of picking one. Higher: more faces get the pair. 0: never, except in
          photos where both faces look like the same twin.
        </p>
        <label className="settings__field">
          Within
          <input
            type="number"
            min={0}
            max={50}
            value={margin}
            onChange={(e) => {
              setNote("");
              setMargin(Math.max(0, Math.min(50, Number(e.target.value) || 0)));
            }}
          />
          points
          <input type="range" min={0} max={30} value={Math.min(margin, 30)} onChange={(e) => { setNote(""); setMargin(Number(e.target.value)); }}
            aria-label="Twin match margin" />
        </label>
        <div className="settings__actions">
          <button className="btn btn--primary" disabled={save.isPending || margin === saved} onClick={() => save.mutate(margin)}>
            Save
          </button>
          {margin !== dflt && (
            <button className="btn btn--ghost" onClick={() => { setNote(""); setMargin(dflt); }}>
              Back to the default ({dflt})
            </button>
          )}
          {note && <span className="muted small" aria-live="polite">{note}</span>}
        </div>
      </section>
    </div>
  );
}
