import type { components } from "./schema";

type S = components["schemas"];
export type Album = S["AlbumOut"];
export type PhotoSummary = S["PhotoSummary"];
export type PhotoDetail = S["PhotoDetail"];
export type PhotoPage = S["PhotoPage"];
export type BulkUpdate = S["BulkUpdate"];
export type BulkResult = S["BulkResult"];
export type UndoResult = S["UndoResult"];
export type EditBatch = S["EditBatchOut"];
export type EventKind = S["EventKindOut"];
export type EventInstance = S["EventInstanceOut"];
export type ParsedDate = S["ParsedDate"];
export type Job = S["JobOut"];
export type JobType = S["JobIn"]["type"];
export type Stats = S["Stats"];
export type Back = S["BackOut"];
export type BackKind = Back["kind"];
export type ClaudeEstimate = S["ClaudeEstimate"];
export type Face = S["FaceOut"];
export type Cluster = S["ClusterOut"];
export type Person = S["PersonOut"];
export type BatchOut = S["BatchOut"];
export type ReviewGroup = S["ReviewGroup"];

export type ListParams = {
  album_id?: number;
  person_id?: number;
  status?: "untagged" | "inferred" | "reviewed";
  duplicates?: boolean;
  order?: "album" | "chrono" | "confidence";
};

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    // The server refuses changes without X-Shoebox, so other sites can't make them.
    headers: { "X-Shoebox": "1", ...(init?.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail);
  }
  return res.json() as Promise<T>;
}

function qs(params: Record<string, unknown>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== false && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  albums: () => call<Album[]>("/albums"),
  photos: (p: ListParams) => call<PhotoPage>(`/photos${qs(p)}`),
  photo: (id: number) => call<PhotoDetail>(`/photos/${id}`),
  update: (body: BulkUpdate) => call<BulkResult>("/photos", { method: "PATCH", body: JSON.stringify(body) }),
  keeper: (id: number) => call<PhotoDetail>(`/photos/${id}/keeper`, { method: "POST" }),
  parseDate: (text: string) => call<ParsedDate>(`/parse-date${qs({ text })}`),
  undo: (batch_id?: string, skip = false) =>
    call<UndoResult>("/undo", { method: "POST", body: JSON.stringify({ batch_id, skip }) }),
  edits: () => call<EditBatch[]>("/edits"),
  eventKinds: () => call<EventKind[]>("/event-kinds"),
  addEventKind: (name: string) =>
    call<EventKind>("/event-kinds", { method: "POST", body: JSON.stringify({ name }) }),
  eventInstances: () => call<EventInstance[]>("/event-instances"),
  jobs: () => call<Job[]>("/jobs"),
  startJob: (type: JobType, params: Record<string, unknown> = {}) =>
    call<Job>("/jobs", { method: "POST", body: JSON.stringify({ type, params }) }),
  cancelJob: (id: number) => call<{ ok: boolean }>(`/jobs/${id}/cancel`, { method: "POST" }),
  stats: () => call<Stats>("/stats"),
  people: () => call<Person[]>("/people"),
  createPerson: (name: string) => call<Person>("/people", { method: "POST", body: JSON.stringify({ name }) }),
  updatePerson: (id: number, body: { name?: string; birth_year?: number | null; notes?: string | null }) =>
    call<Person>(`/people/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  mergePerson: (id: number, into_id: number) =>
    call<BatchOut>(`/people/${id}/merge`, { method: "POST", body: JSON.stringify({ into_id }) }),
  personFaces: (id: number) => call<Face[]>(`/people/${id}/faces`),
  clusters: (singles = false) => call<Cluster[]>(`/clusters${qs({ singles })}`),
  clusterFaces: (id: number) => call<Face[]>(`/clusters/${id}/faces`),
  photoFaces: (id: number) => call<Face[]>(`/photos/${id}/faces`),
  assignFaces: (face_ids: number[], who: { person_id?: number; name?: string }) =>
    call<BatchOut>("/faces/assign", { method: "POST", body: JSON.stringify({ face_ids, ...who }) }),
  faceReview: () => call<ReviewGroup[]>("/faces/review"),
  acceptFaces: (face_ids: number[]) => call<BatchOut>("/faces/accept", { method: "POST", body: JSON.stringify({ face_ids }) }),
  rejectFaces: (face_ids: number[]) => call<BatchOut>("/faces/reject", { method: "POST", body: JSON.stringify({ face_ids }) }),
  revertRun: (runId: string) => call<BatchOut>(`/runs/${runId}/revert`, { method: "POST" }),
  ignoreFaces: (face_ids: number[], ignored = true) =>
    call<BatchOut>("/faces/ignore", { method: "POST", body: JSON.stringify({ face_ids, ignored }) }),
  drawFace: (photoId: number, box: { x: number; y: number; w: number; h: number; name?: string }) =>
    call<Face>(`/photos/${photoId}/faces`, { method: "POST", body: JSON.stringify(box) }),
  deleteFace: (id: number) => call<{ deleted: boolean }>(`/faces/${id}`, { method: "DELETE" }),
  faceStats: () =>
    call<{ faces: number; named: number; ignored: number; unnamed_in_clusters: number; unnamed_single: number; people: number; to_review: number }>(
      "/faces/stats",
    ),
  claudeEstimate: (scope: "unread" | "all") => call<ClaudeEstimate>(`/claude/estimate${qs({ scope })}`),
  backs: (kind: BackKind, p: { album_id?: number; undated?: boolean }) =>
    call<Back[]>(`/${kind}-backs${qs(p)}`),
  setBackDate: (kind: BackKind, id: number, date: string | null) =>
    call<Back>(`/${kind}-backs/${id}`, { method: "PATCH", body: JSON.stringify({ date }) }),
};

export const faceUrl = (id: number) => `/api/face/${id}.jpg`;

export const imgUrl = (sha: string, kind: "thumb" | "preview", rotation = 0) =>
  `/api/img/${sha}/${kind}/${rotation}.jpg`;
