/** Changes that couldn't be saved because the photos couldn't be reached (the Mac asleep, other
 *  Wi-Fi). They're kept on this device, so even a reload doesn't lose them, and saved as soon as the
 *  photos can be reached again. Only for edits saved without anyone watching: a caption or place
 *  saved on the way to the next photo. Keyed by photo and field, so a newer edit replaces an older one. */
import { api, ApiError, type BulkUpdate } from "../src/api";

const KEY = "shoebox.unsaved";
/** Sent when a save fails for want of a connection: the app says so at once (not on its next poll). */
export const WRITE_FAILED = "shoebox:writefailed";
/** Sent when the list of waiting changes changes. */
export const PENDING_CHANGED = "shoebox:pending";

type Waiting = Record<string, BulkUpdate>;

function load(): Waiting {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}") as Waiting;
  } catch {
    return {};
  }
}
function store(w: Waiting) {
  try {
    if (Object.keys(w).length) localStorage.setItem(KEY, JSON.stringify(w));
    else localStorage.removeItem(KEY);
  } catch {
    // private window: kept in memory below for this visit
  }
  memory = w;
  window.dispatchEvent(new Event(PENDING_CHANGED));
}
let memory: Waiting = load();

export const waitingCount = () => Object.keys(memory).length;

/** Save `body` now; if the photos can't be reached, keep it under `key` and save it later. Errors the
 *  server reports (a bad value) aren't kept: retrying wouldn't help, and the caller shows them. */
export async function saveOrKeep(key: string, body: BulkUpdate, save: (b: BulkUpdate) => Promise<unknown>) {
  try {
    await save(body);
    if (key in memory) {
      const { [key]: _, ...rest } = memory;
      store(rest);
    }
  } catch (e) {
    if (e instanceof ApiError) throw e;
    store({ ...memory, [key]: body });
    window.dispatchEvent(new Event(WRITE_FAILED));
    throw e;
  }
}

/** Try every waiting change again; returns how many still wait. */
export async function flushWaiting(): Promise<number> {
  let left = { ...memory };
  for (const [key, body] of Object.entries(memory)) {
    try {
      await api.update(body);
      const { [key]: _, ...rest } = left;
      left = rest;
    } catch (e) {
      if (e instanceof ApiError) {
        const { [key]: _, ...rest } = left; // the server refused it: retrying won't help
        left = rest;
      } else break; // still can't reach the photos
    }
  }
  store(left);
  return Object.keys(left).length;
}
