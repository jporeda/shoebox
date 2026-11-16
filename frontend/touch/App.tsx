import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../src/api";
import { useJobs, useJobWatcher, useStats } from "../src/hooks";
import { usePeople } from "../src/peopleData";
import { closePhoto, go, LAST_PHOTO_EVENT, useTouchRoute, viewerList, type Screen } from "./route";
import { Photos } from "./screens/Photos";
import { Viewer } from "./screens/Viewer";
import { DateQueue } from "./screens/DateQueue";
import { PeopleScreen } from "./screens/People";
import { PersonScreen } from "./screens/Person";
import { CheckScreen } from "./screens/Check";
import { CantReach, Menu, UndoToast, Welcome } from "./ui";
import { flushWaiting, PENDING_CHANGED, waitingCount, WRITE_FAILED } from "./pending";
import { Icon, useNewGrid, type IconName } from "../src/GridNew";

const TABS = [
  { view: "photos", href: "#/", label: "Photos", icon: "▦", line: "photos" },
  { view: "date", href: "#/date", label: "Date", icon: "◷", line: "calendar" },
  { view: "people", href: "#/people", label: "People", icon: "☺", line: "people" },
  { view: "check", href: "#/check", label: "Check", icon: "✓", line: "confirm" },
] as const satisfies readonly { view: string; href: string; label: string; icon: string; line: IconName }[];

/** Which tab a screen belongs to (a person's page is under People). */
const tabOf = (s: Screen) => (s.view === "person" ? "people" : s.view);

// Scroll position per screen, so coming back (Back, a tab) lands where you were.
const scrollMemory = new Map<string, number>();
// The last screen in each tab: a tab brings it back as you left it (filters, a person's page).
const tabMemory = new Map<string, string>();

export function App() {
  const route = useTouchRoute();
  // The new look ("Harvest Table", in the ⋯ menu): its colours and type come from src/tokens.css.
  const look = useNewGrid(true);
  useEffect(() => {
    if (look.on) document.documentElement.dataset.look = "harvest";
    else delete document.documentElement.dataset.look;
  }, [look.on]);
  const { screen, overlay, base } = route;
  useJobWatcher(); // refresh what a background job changed (e.g. new name suggestions)
  const stats = useStats();
  // The job list is asked for every few seconds, so it notices first when the Mac can't be reached.
  const jobs = useJobs();
  // A save that failed for want of a connection says so at once, not on the next poll; the changes it
  // kept are saved as soon as the photos answer again.
  const [waiting, setWaiting] = useState(waitingCount);
  const [failedAt, setFailedAt] = useState(0);
  useEffect(() => {
    const onWaiting = () => setWaiting(waitingCount());
    const onFailed = () => setFailedAt(Date.now());
    window.addEventListener(PENDING_CHANGED, onWaiting);
    window.addEventListener(WRITE_FAILED, onFailed);
    return () => {
      window.removeEventListener(PENDING_CHANGED, onWaiting);
      window.removeEventListener(WRITE_FAILED, onFailed);
    };
  }, []);
  const unreachable = stats.isError || jobs.isError || failedAt > jobs.dataUpdatedAt;
  const qc = useQueryClient();
  useEffect(() => {
    if (unreachable || !waiting) return;
    flushWaiting().then((left) => {
      if (left < waiting) void qc.invalidateQueries();
    });
  }, [unreachable, waiting, jobs.dataUpdatedAt, qc]);
  const [retrying, setRetrying] = useState(false);
  const retry = async () => {
    setRetrying(true);
    await qc.refetchQueries();
    if (waitingCount()) await flushWaiting();
    setRetrying(false);
  };
  const faceStats = useQuery({ queryKey: ["faceStats"], queryFn: api.faceStats });
  const people = usePeople();
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [base]);
  const main = useRef<HTMLElement>(null);
  // When a photo closes, by any way out (in-app Back, swipe down, the phone's or browser's Back), the
  // screen underneath brings the last photo viewed into view.
  const lastPhoto = useRef<number | null>(null);
  useEffect(() => {
    const was = lastPhoto.current;
    lastPhoto.current = overlay?.id ?? null;
    if (was !== null && !overlay) window.dispatchEvent(new CustomEvent(LAST_PHOTO_EVENT, { detail: was }));
  }, [overlay?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  tabMemory.set(tabOf(screen), base);
  useLayoutEffect(() => {
    if (main.current) main.current.scrollTop = scrollMemory.get(base) ?? 0;
  }, [base]);

  const counts: Record<string, number | undefined> = {
    date: stats.data ? stats.data.untagged + stats.data.inferred : undefined,
    check: faceStats.data?.to_review,
  };
  const personName = screen.view === "person" ? people.data?.find((p) => p.id === screen.id)?.name : undefined;
  const backLabel =
    screen.view === "person" ? (personName ?? "Back")
    : screen.view === "people" ? (screen.tab === "everyone" ? "People" : "To name")
    : screen.view === "check" ? "Check"
    : screen.view === "date" ? "Date"
    : "Photos";
  const title = screen.view === "person" ? null : TABS.find((t) => t.view === screen.view)?.label;

  return (
    <div className={`t-app ${overlay ? "is-viewer" : ""}`}>
      {/* The screen stays mounted under an open photo, so its scroll and half-done work survive. */}
      <div className="t-screen" aria-hidden={overlay ? true : undefined}>
        {title && (
          <header className="t-top">
            <span className="t-top__title">{title}</span>
            <button className="t-btn t-btn--plain t-top__menu" aria-label="Menu" onClick={() => setMenu(true)}>
              {look.on ? <Icon name="more" size={26} stroke={2.4} /> : "⋯"}
            </button>
          </header>
        )}
        <main className="t-main" ref={main} onScroll={(e) => scrollMemory.set(base, e.currentTarget.scrollTop)}>
          {screen.view === "photos" && <Photos list={screen.list} />}
          {screen.view === "person" && <PersonScreen id={screen.id} />}
          {screen.view === "date" && <DateQueue q={screen.q} />}
          {screen.view === "people" && <PeopleScreen tab={screen.tab} />}
          {screen.view === "check" && <CheckScreen />}
        </main>
        <nav className="t-tabs">
          {TABS.map((t) => (
            <a
              key={t.view}
              // The tab you're on starts it over (all photos, the To name list); another brings back where you were.
              href={tabOf(screen) === t.view ? t.href : (tabMemory.get(t.view) ?? t.href)}
              className={`t-tab ${tabOf(screen) === t.view ? "is-on" : ""}`}
            >
              <span className="t-tab__icon" aria-hidden>
                {look.on ? <Icon name={t.line} size={25} stroke={1.8} /> : t.icon}
              </span>
              <span className="t-tab__label">{t.label}</span>
              {!!counts[t.view] && <span className="t-tab__count">{counts[t.view]! > 999 ? "999+" : counts[t.view]}</span>}
            </a>
          ))}
        </nav>
      </div>
      {overlay && (
        <div className="t-layer">
          <Viewer
            key={`${base}|${overlay.ids?.join(",") ?? ""}`}
            id={overlay.id}
            list={viewerList(screen, overlay)}
            faces={overlay.faces}
            backLabel={backLabel}
            onClose={closePhoto}
          />
        </div>
      )}
      {unreachable && <CantReach stale={!!stats.data} busy={retrying} onRetry={retry} waiting={waiting} />}
      {!unreachable && <Welcome onStart={() => go("#/date")} />}
      <UndoToast />
      <Menu open={menu} onClose={() => setMenu(false)} />
    </div>
  );
}
