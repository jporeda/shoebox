import { useEffect, useState } from "react";

const CHANGED = "shoebox:setting";

/** An on/off switch from a photo's Advanced tab: off unless turned on, remembered on this device
 *  (every screen showing it follows a change at once). */
export function useSwitch(key: string): [boolean, (on: boolean) => void] {
  const read = () => {
    try {
      return localStorage.getItem(key) === "on";
    } catch {
      return false;
    }
  };
  const [on, setOn] = useState(read);
  useEffect(() => {
    const sync = () => setOn(read());
    window.addEventListener(CHANGED, sync);
    return () => window.removeEventListener(CHANGED, sync);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (next: boolean) => {
    try {
      localStorage.setItem(key, next ? "on" : "off");
    } catch {
      // private window: just this visit
    }
    setOn(next);
    window.dispatchEvent(new Event(CHANGED));
  };
  return [on, set];
}

/** Zoom in on a face from its name box (People tab). */
export const FACE_ZOOM = "shoebox.touch.faceZoom";
/** The Date tab's Estimate and Exact boxes start open. */
export const OPEN_ESTIMATE = "shoebox.touch.openEstimate";
export const OPEN_EXACT = "shoebox.touch.openExact";
/** A button over the photo that accepts every name guess on it at once. */
export const ACCEPT_ALL = "shoebox.touch.acceptAll";
/** Beside the face boxes on the photo: the names people confirmed (green boxes), and the app's
 *  guesses with a "?" (yellow boxes). The first was one "names next to boxes" switch: same key. */
export const CONFIRMED_NAMES = "shoebox.touch.boxNames";
export const ESTIMATED_NAMES = "shoebox.touch.guessNames";
/** Those names as first names only ("Ana" for "Ana Smith", "Ana or Ben" for twins). */
export const FIRST_NAMES = "shoebox.touch.firstNames";
