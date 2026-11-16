import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, type SettingsInfo } from "./api";

/** Settings stored with the collection (each collection has its own). */
export function Settings() {
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  if (!settings.data) return <p className="pad muted">Loading…</p>;
  return (
    <div className="settings pad">
      <h2>Settings</h2>
      <p className="muted small">Each collection has its own settings.</p>
      <NumberSetting
        field="twin_margin_percent"
        defaultField="twin_margin_default"
        data={settings.data}
        title="Twins: when to suggest “Ana or Ben?”"
        unit="points"
        min={0}
        max={50}
        slider={30}
        saved="Saved. Name suggestions are being worked out again with it."
      >
        For people you've paired as twins on a face. Each name guess has a match score (the % shown on guesses).
        When the face's best match is one twin and the other twin's score is within this many points, the app
        suggests “Ana or Ben?” instead of picking one. Higher: more faces get the pair. 0: never, except in photos
        where both faces look like the same twin.
      </NumberSetting>
      <NumberSetting
        field="snapshot_gap_minutes"
        defaultField="snapshot_gap_default"
        data={settings.data}
        title="Backups: database snapshots after jobs"
        unit="minutes"
        prefix="At most one every"
        min={0}
        max={1440}
        slider={120}
        saved="Saved."
      >
        After each job (and naming or dating a photo starts one), Shoebox can save a full copy of the database in
        the backup folder. Taking at most one every so often saves a lot of writing, which matters on an SD card or a
        small disk. <b>truth.json</b> (everything you've entered) is still written after every job, and a manual
        Backup on the Jobs page always takes a snapshot. 0: after every job.
      </NumberSetting>
    </div>
  );
}

type Field = "twin_margin_percent" | "snapshot_gap_minutes";

function NumberSetting({ field, defaultField, data, title, unit, prefix = "Within", min, max, slider, saved, children }: {
  field: Field;
  defaultField: "twin_margin_default" | "snapshot_gap_default";
  data: SettingsInfo;
  title: string;
  unit: string;
  prefix?: string;
  min: number;
  max: number;
  slider: number;
  saved: string;
  children: React.ReactNode;
}) {
  const qc = useQueryClient();
  const [value, setValue] = useState<number>(data[field]);
  const [note, setNote] = useState("");
  useEffect(() => setValue(data[field]), [data, field]);
  const save = useMutation({
    mutationFn: (v: number) => api.saveSettings({ [field]: v }),
    onSuccess: (s) => {
      qc.setQueryData(["settings"], s);
      qc.invalidateQueries({ queryKey: ["jobs"] }); // a re-run it may have started
      setNote(saved);
    },
    onError: (e) => setNote(e instanceof ApiError ? e.message : String(e)),
  });
  const dflt = data[defaultField];
  const set = (v: number) => {
    setNote("");
    setValue(Math.max(min, Math.min(max, v)));
  };
  return (
    <section className="settings__item">
      <h3>{title}</h3>
      <p className="muted small">{children}</p>
      <label className="settings__field">
        {prefix}
        <input type="number" min={min} max={max} value={value} onChange={(e) => set(Number(e.target.value) || 0)} />
        {unit}
        <input type="range" min={min} max={slider} value={Math.min(value, slider)} onChange={(e) => set(Number(e.target.value))}
          aria-label={title} />
      </label>
      <div className="settings__actions">
        <button className="btn btn--primary" disabled={save.isPending || value === data[field]} onClick={() => save.mutate(value)}>
          Save
        </button>
        {value !== dflt && (
          <button className="btn btn--ghost" onClick={() => set(dflt)}>
            Back to the default ({dflt})
          </button>
        )}
        {note && <span className="muted small" aria-live="polite">{note}</span>}
      </div>
    </section>
  );
}
