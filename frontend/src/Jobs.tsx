import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Job, type JobType } from "./api";

const ACTIONS: { type: JobType; label: string; help: string; confirm?: string }[] = [
  { type: "ingest", label: "Import", help: "Scan the photo folder: new albums, photos, back scans, duplicates, thumbnails. Then auto-rotates." },
  {
    type: "read_backs",
    label: "Read backs",
    help: "OCR the printed lab dates on back scans (local, nothing leaves your Mac). Runs after Import automatically.",
  },
  {
    type: "faces",
    label: "Find faces",
    help: "Detect faces in photos not scanned yet (and embed boxes you drew). Runs after auto-rotate.",
  },
  {
    type: "cluster",
    label: "Group faces",
    help: "Regroup unnamed faces that look alike, for naming on the People page.",
  },
  {
    type: "estimate",
    label: "Infer dates & names",
    help: "Re-estimate undated photos (backs, album neighbours, ages, file names) and suggest names for faces. Runs by itself after your edits.",
  },
  {
    type: "evaluate",
    label: "Measure accuracy",
    help: "Hide 20% of your confirmed dates and names, predict them, and report how close the app gets. Changes nothing.",
  },
  { type: "export", label: "Export", help: "Write dated copies with embedded metadata to backup/export, plus XMP sidecars." },
  { type: "backup", label: "Back up now", help: "Database snapshot, truth.json and checksum manifest to the backup folder." },
  { type: "fixity", label: "Verify originals", help: "Re-hash every original and back scan and report anything changed or missing." },
  {
    type: "restore",
    label: "Restore from truth.json",
    help: "Re-apply backed-up dates, events, captions and rotations onto imported photos.",
    confirm: "Re-apply backup/truth/truth.json onto the current photos? Existing values are overwritten (undoable).",
  },
];

const LABEL: Record<string, string> = { faces: "Find faces", cluster: "Group faces", claude_backs: "Claude: read backs", ingest: "Import", orient: "Auto-rotate", read_backs: "Read backs", estimate: "Infer dates & names", evaluate: "Measure accuracy", export: "Export", backup: "Backup", fixity: "Verify originals", restore: "Restore" };

export function Jobs() {
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    refetchInterval: (q) => (q.state.data?.some((j) => ["queued", "running", "cancelling"].includes(j.state)) ? 1000 : 5000),
  });
  const start = useMutation({
    mutationFn: ({ type, params }: { type: JobType; params?: Record<string, unknown> }) => api.startJob(type, params),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["jobs"] }),
  });


  const active = new Set(jobs.data?.filter((j) => ["queued", "running"].includes(j.state)).map((j) => j.type));

  // When a job finishes, photos/albums/stats may have changed: refresh everything.
  const running = useRef<Set<number>>(new Set());
  useEffect(() => {
    const now = new Set(jobs.data?.filter((j) => ["queued", "running", "cancelling"].includes(j.state)).map((j) => j.id));
    const finished = [...running.current].some((id) => !now.has(id));
    running.current = now;
    if (finished) qc.invalidateQueries({ predicate: (q) => q.queryKey[0] !== "jobs" });
  }, [jobs.data, qc]);

  return (
    <div className="jobs pad">
      <div className="actions">
        {ACTIONS.map((a) => (
          <div className="action" key={a.type}>
            <button
              className="btn btn--primary"
              disabled={active.has(a.type) || start.isPending}
              onClick={() =>
                (!a.confirm || window.confirm(a.confirm)) && start.mutate({ type: a.type })
              }
            >
              {a.label}
            </button>
            <p className="muted">{a.help}</p>
          </div>
        ))}
      </div>
      <h2>Recent jobs</h2>
      <div className="job-list">
        {jobs.data?.map((j) => <JobRow key={j.id} job={j} />)}
        {jobs.data?.length === 0 && <p className="muted">No jobs yet.</p>}
      </div>
    </div>
  );
}

function JobRow({ job }: { job: Job }) {
  const qc = useQueryClient();
  const pct = job.total ? Math.round((job.progress / job.total) * 100) : job.state === "done" ? 100 : 0;
  const running = job.state === "running" || job.state === "queued";
  return (
    <details className={`job job--${job.state}`}>
      <summary>
        <span className="job__name">{LABEL[job.type] ?? job.type}</span>
        <span className={`job__state state-${job.state}`}>{job.state}</span>
        <span className="job__bar">
          <span style={{ width: `${pct}%` }} />
        </span>
        <span className="job__msg muted">{job.state === "running" ? `${job.progress}/${job.total} ${job.message}` : summary(job)}</span>
        <span className="muted job__time">{new Date(job.created_at + (job.created_at.endsWith("Z") ? "" : "Z")).toLocaleString()}</span>
        {running && (
          <button
            className="btn btn--ghost"
            onClick={async (e) => {
              e.preventDefault();
              await api.cancelJob(job.id);
              qc.invalidateQueries({ queryKey: ["jobs"] });
            }}
          >
            Cancel
          </button>
        )}
      </summary>
      {job.error && <pre className="error">{job.error}</pre>}
      {job.type === "estimate" && job.state === "done" && <RevertButtons result={job.result as Record<string, any>} />}
      {Object.keys(job.result).length > 0 && <pre className="job__result">{JSON.stringify(job.result, null, 2)}</pre>}
    </details>
  );
}

function summary(job: Job): string {
  const r = job.result as Record<string, unknown>;
  const len = (k: string) => (Array.isArray(r[k]) ? (r[k] as unknown[]).length : 0);
  switch (job.type) {
    case "ingest":
      return r.fronts_seen !== undefined
        ? `${r.fronts_seen} photos, ${r.new} new, ${r.backs_paired} paired backs, ${r.loose_backs ?? len("orphan_backs")} loose backs, ${r.duplicate_groups} dup groups, ${len("unreadable")} unreadable`
        : "";
    case "export":
      return r.planned !== undefined ? `${r.written} written, ${r.unchanged} unchanged, ${len("failed")} failed` : "";
    case "fixity":
      return r.checked !== undefined ? `${r.ok}/${r.checked} ok, ${len("changed")} changed, ${len("missing")} missing, ${len("new_files")} new` : "";
    case "claude_backs":
      return r.sent !== undefined
        ? `${r.dates_accepted} dates from ${r.succeeded}/${r.sent} backs${r.failed ? `, ${r.failed} failed (will retry next run)` : ""}`
        : "";
    case "faces":
      return r.photos !== undefined
        ? `${r.faces_added} faces in ${r.photos} photos (${r.seconds_per_photo}s/photo)${r.failed ? `, ${r.failed} failed` : ""}`
        : "";
    case "cluster":
      return r.clusters !== undefined ? `${r.faces} unnamed faces → ${r.clusters} groups, ${r.singletons} single` : "";
    case "read_backs":
      return r.dates_accepted !== undefined
        ? `${r.dates_accepted} dates read from ${(r.loose_backs as number) + (r.paired_backs as number)} backs`
        : "";
    case "estimate": {
      if (r.estimated === undefined) return "";
      const n = r.names as Record<string, number> | undefined;
      return `${r.estimated} dates estimated (${r.own_back} back, ${r.loose_back} loose back, ${r.neighbours ?? 0} neighbours, ${r.age ?? 0} age, ${r.file_name} file name)${n ? ` · ${n.auto} names applied, ${n.suggested} suggested` : ""}`;
    }
    case "evaluate": {
      const d = (r.dates as Record<string, any>) ?? {};
      const f = (r.faces as Record<string, any>) ?? {};
      const dates = d.overall?.n ? `dates: ${Math.round(d.overall.within_1_year * 100)}% within 1 yr (median off ${d.overall.median_abs_error_years} yr, n=${d.overall.n})` : `dates: ${d.status ?? "—"}`;
      const faces = f.overall?.faces ? `names: auto ${f.overall.auto_precision ?? "—"} precision / ${f.overall.auto_recall ?? "—"} recall` : `names: ${f.status ?? "—"}`;
      return `${dates} · ${faces}`;
    }
    case "orient":
      return r.checked !== undefined ? `${r.rotated} of ${r.checked} rotated` : "";
    case "restore":
      return r.matched !== undefined ? `${r.matched} restored from ${r.file}, ${len("unmatched")} not found` : "";
    default:
      return job.message;
  }
}

/** Automatic names can be reverted; date estimates just follow their evidence. */
function RevertButtons({ result }: { result: Record<string, any> }) {
  const qc = useQueryClient();
  const runId = result.names?.run_id as string | undefined;
  if (!runId || !result.names?.auto) return null;
  return (
    <div className="job__revert">
      <button
        className="btn btn--ghost"
        onClick={async () => {
          if (!window.confirm("Clear the names this run gave automatically? Those faces will only get suggestions from now on. (Undoable.)")) return;
          await api.revertRun(runId);
          qc.invalidateQueries();
        }}
      >
        Revert this run's names
      </button>
    </div>
  );
}
