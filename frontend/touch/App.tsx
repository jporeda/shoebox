import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../src/api";
import { useJobWatcher, useStats } from "../src/hooks";
import { useTouchRoute } from "./route";
import { Photos } from "./screens/Photos";
import { Viewer } from "./screens/Viewer";
import { DateQueue } from "./screens/DateQueue";
import { PeopleScreen } from "./screens/People";
import { CheckScreen } from "./screens/Check";
import { Menu, UndoToast } from "./ui";

const TABS = [
  { view: "photos", href: "#/", label: "Photos", icon: "▦" },
  { view: "date", href: "#/date", label: "Date", icon: "◷" },
  { view: "people", href: "#/people", label: "People", icon: "☺" },
  { view: "check", href: "#/check", label: "Check", icon: "✓" },
] as const;

export function App() {
  const route = useTouchRoute();
  useJobWatcher(); // refresh what a background job changed (e.g. new name suggestions)
  const stats = useStats();
  const faceStats = useQuery({ queryKey: ["faceStats"], queryFn: api.faceStats });
  const [menu, setMenu] = useState(false);
  useEffect(() => setMenu(false), [route]);
  const counts: Record<string, number | undefined> = {
    date: stats.data ? stats.data.untagged + stats.data.inferred : undefined,
    check: faceStats.data?.to_review,
  };
  const viewer = route.view === "photo";
  return (
    <div className={`t-app ${viewer ? "is-viewer" : ""}`}>
      {!viewer && (
        <header className="t-top">
          <span className="t-top__title">{TABS.find((t) => t.view === route.view)?.label ?? "Shoebox"}</span>
          <button className="t-btn t-btn--plain t-top__menu" aria-label="Menu" onClick={() => setMenu(true)}>
            ⋯
          </button>
        </header>
      )}
      <main className="t-main">
        {route.view === "photos" && <Photos list={route.list} />}
        {route.view === "photo" && <Viewer id={route.id} list={route.list} />}
        {route.view === "date" && <DateQueue />}
        {route.view === "people" && <PeopleScreen />}
        {route.view === "check" && <CheckScreen />}
      </main>
      {!viewer && (
        <nav className="t-tabs">
          {TABS.map((t) => (
            <a key={t.view} href={t.href} className={`t-tab ${route.view === t.view ? "is-on" : ""}`}>
              <span className="t-tab__icon" aria-hidden>
                {t.icon}
              </span>
              <span className="t-tab__label">{t.label}</span>
              {!!counts[t.view] && <span className="t-tab__count">{counts[t.view]! > 999 ? "999+" : counts[t.view]}</span>}
            </a>
          ))}
        </nav>
      )}
      <UndoToast />
      <Menu open={menu} onClose={() => setMenu(false)} />
    </div>
  );
}
