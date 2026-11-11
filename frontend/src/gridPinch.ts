import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

/** Column counts a pinch snaps between, like the Photos app: pinch out for fewer, bigger photos,
 *  pinch in for more, smaller ones. The default layout (auto-fill) is wherever it falls among them. */
const PHONE_STEPS = [1, 2, 3, 5, 7];
const TABLET_STEPS = [2, 3, 4, 5, 6, 8, 10];
/** How far the fingers must spread or close (as a ratio) before a release changes the columns. */
const THRESHOLD = 0.12;
/** Below this tile width the date line is hidden and the gaps shrink, so the photos fill the screen. */
const DENSE_BELOW = 100;
const DENSE_GAP = 3;

const storageKey = (phone: boolean) => `shoebox.gridColumns.${phone ? "phone" : "tablet"}`;
function load(phone: boolean): number | null {
  try {
    const n = Number(localStorage.getItem(storageKey(phone)));
    return Number.isInteger(n) && n > 0 && n <= 12 ? n : null;
  } catch {
    return null;
  }
}
function save(phone: boolean, cols: number) {
  try {
    localStorage.setItem(storageKey(phone), String(cols));
  } catch {
    /* private mode or storage blocked: the choice just isn't remembered */
  }
}

/** The step to land on after a pinch from `cols` columns that scaled the grid by `scale`: the next
 *  step that way, or further only if the fingers went past it. */
function snap(steps: number[], cols: number, scale: number): number {
  const desired = cols / scale;
  if (scale > 1 + THRESHOLD) {
    const fewer = steps.filter((x) => x < cols);
    if (!fewer.length) return cols;
    return fewer.find((x) => x >= desired) ?? fewer[fewer.length - 1];
  }
  if (scale < 1 - THRESHOLD) {
    const more = steps.filter((x) => x > cols);
    if (!more.length) return cols;
    return [...more].reverse().find((x) => x <= desired) ?? more[0];
  }
  return cols;
}

type Gesture = {
  d0: number;
  cx: number;
  cy: number;
  cols: number;
  steps: number[];
  w0: number;
  scale: number;
  anchor: HTMLElement | null;
  fx: number;
  fy: number;
};
type Settle = { anchor: HTMLElement | null; fx: number; fy: number; cx: number; cy: number; visualW: number };

const distance = (t: TouchList) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

/** Two-finger pinch on the photo grid changes its column count instead of zooming the page.
 *  Touch devices only (`enabled`); with a mouse the grid keeps its automatic layout.
 *  During the pinch the grid scales live around the fingers; on release it snaps to a column step,
 *  keeping the photo under the fingers in place, and the choice is remembered per phone / tablet. */
export function useGridPinch(ref: RefObject<HTMLDivElement | null>, enabled: boolean, phone: boolean) {
  const [cols, setCols] = useState<number | null>(() => (enabled ? load(phone) : null));
  const [width, setWidth] = useState(0);
  const suppressUntil = useRef(0);
  const active = useRef(false);
  const settle = useRef<Settle | null>(null);
  const steps = phone ? PHONE_STEPS : TABLET_STEPS;

  useEffect(() => setCols(enabled ? load(phone) : null), [enabled, phone]);

  // Width of the grid's content box, to size tiles (and their off-screen placeholders) exactly.
  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    const measure = () => {
      const cs = getComputedStyle(el);
      setWidth(el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref, enabled]);

  let gap = phone ? 8 : 10;
  let tileW = cols && width ? (width - gap * (cols - 1)) / cols : 0;
  const dense = !!cols && tileW > 0 && tileW < DENSE_BELOW;
  if (dense) {
    gap = DENSE_GAP;
    tileW = (width - gap * (cols! - 1)) / cols!;
  }

  useEffect(() => {
    const el = ref.current;
    if (!el || !enabled) return;
    let g: Gesture | null = null;

    const currentCols = () => cols ?? getComputedStyle(el).gridTemplateColumns.split(" ").filter(Boolean).length;

    const begin = (e: TouchEvent) => {
      const t = e.touches;
      const cx = (t[0].clientX + t[1].clientX) / 2;
      const cy = (t[0].clientY + t[1].clientY) / 2;
      // A second finger means this isn't a tap or a press-and-hold: cancel any pending long press.
      for (const touch of Array.from(t)) {
        const tile = (touch.target as Element | null)?.closest?.(".tile");
        tile?.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerType: "touch", isPrimary: true }));
      }
      // The photo between the fingers (or, if that's a gap, the nearest one) stays put.
      let hit: HTMLElement | null | undefined = null;
      for (const [dx, dy] of [[0, 0], [-12, 0], [12, 0], [0, -12], [0, 12]]) {
        hit = document.elementFromPoint(cx + dx, cy + dy)?.closest<HTMLElement>(".tile");
        if (hit) break;
      }
      const anchor = hit ?? (t[0].target as Element | null)?.closest?.<HTMLElement>(".tile") ?? null;
      const r = anchor?.getBoundingClientRect();
      const first = el.querySelector<HTMLElement>(".tile");
      const c = currentCols();
      g = {
        d0: Math.max(distance(t), 1),
        cx,
        cy,
        cols: c,
        steps,
        w0: first?.getBoundingClientRect().width ?? 1,
        scale: 1,
        anchor,
        fx: r ? Math.min(1, Math.max(0, (cx - r.left) / r.width)) : 0.5,
        fy: r ? Math.min(1, Math.max(0, (cy - r.top) / r.height)) : 0.5,
      };
      active.current = true;
      const box = el.getBoundingClientRect();
      el.style.transition = "none";
      el.style.transformOrigin = `${cx - box.left}px ${cy - box.top}px`;
      el.classList.add("is-pinching");
    };

    const finish = () => {
      if (!g) return;
      const { cols: from, scale, anchor, fx, fy, cx, cy, w0 } = g;
      g = null;
      active.current = false;
      suppressUntil.current = performance.now() + 400;
      const to = snap(steps, from, scale);
      if (to === from) {
        el.style.transition = "transform 0.18s ease-out";
        el.style.transform = "";
        const done = () => {
          el.style.transition = "";
          el.classList.remove("is-pinching");
        };
        el.addEventListener("transitionend", done, { once: true });
        window.setTimeout(done, 250);
        return;
      }
      settle.current = { anchor, fx, fy, cx, cy, visualW: w0 * scale };
      save(phone, to);
      setCols(to);
    };

    const onStart = (e: TouchEvent) => {
      if (e.touches.length === 2 && !g) begin(e);
    };
    const onMove = (e: TouchEvent) => {
      if (!g || e.touches.length < 2) return;
      if (e.cancelable) e.preventDefault();
      let s = distance(e.touches) / g.d0;
      // At the first or last step the grid only gives a little: there's nowhere further to go.
      const canFewer = g.steps.some((x) => x < g!.cols);
      const canMore = g.steps.some((x) => x > g!.cols);
      if (s > 1 && !canFewer) s = 1 + (s - 1) * 0.15;
      if (s < 1 && !canMore) s = 1 - (1 - s) * 0.15;
      g.scale = Math.min(3, Math.max(0.33, s));
      el.style.transform = `scale(${g.scale})`;
    };
    const onEnd = (e: TouchEvent) => {
      if (g && e.touches.length < 2) finish();
    };
    // iOS Safari zooms the page from its own gesture events, whatever touch-action says.
    const noGesture = (e: Event) => e.preventDefault();

    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    el.addEventListener("gesturestart", noGesture);
    el.addEventListener("gesturechange", noGesture);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
      el.removeEventListener("gesturestart", noGesture);
      el.removeEventListener("gesturechange", noGesture);
    };
  }, [ref, enabled, phone, cols, steps]);

  // After the new column count is laid out: put the photo that was under the fingers back under
  // them, then shrink or grow from the size it had at release to its new size.
  useLayoutEffect(() => {
    const el = ref.current;
    const s = settle.current;
    if (!el || !s) return;
    settle.current = null;
    let w1 = s.visualW;
    // Measure without the pinch's scale still applied.
    el.style.transition = "none";
    el.style.transform = "";
    if (s.anchor?.isConnected) {
      const r = s.anchor.getBoundingClientRect();
      const dy = r.top + s.fy * r.height - s.cy;
      // Tablets scroll the grid itself; on a phone the grid is in the page, which scrolls.
      if (el.scrollHeight > el.clientHeight + 1) el.scrollTop += dy;
      else window.scrollBy(0, dy);
      w1 = r.width || w1;
    }
    const box = el.getBoundingClientRect();
    el.style.transition = "none";
    el.style.transformOrigin = `${s.cx - box.left}px ${s.cy - box.top}px`;
    el.style.transform = `scale(${s.visualW / w1})`;
    void el.offsetWidth; // apply that before transitioning away from it
    el.style.transition = "transform 0.22s ease-out";
    el.style.transform = "";
    const done = () => {
      el.style.transition = "";
      el.classList.remove("is-pinching");
    };
    el.addEventListener("transitionend", done, { once: true });
    window.setTimeout(done, 300);
  }, [ref, cols]);

  const style = cols
    ? ({ "--cols": cols, "--gap": `${gap}px`, ...(tileW ? { "--tile": `${tileW}px` } : {}) } as CSSProperties)
    : undefined;
  const className = cols ? `grid--cols${dense ? " grid--dense" : ""}` : "";
  const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
  return {
    style,
    className,
    /** Tiles big enough that the 400px thumbnail would look soft: show the preview image instead. */
    big: tileW * dpr > 700,
    /** True during a pinch and just after: the fingers lifting shouldn't count as a tap. */
    suppressed: () => active.current || performance.now() < suppressUntil.current,
  };
}
