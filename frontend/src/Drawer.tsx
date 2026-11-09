/** The page sidebar. On phones (≤600px, CSS only) it is a slide-in drawer opened by FiltersButton;
 *  wider screens show it in place as before and never see the button, header or scrim. */
export function SidebarDrawer({
  open,
  onClose,
  title = "Filters",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <>
      {open && <div className="drawer-scrim" onClick={onClose} />}
      <aside className={`sidebar ${open ? "is-open" : ""}`} aria-label={title}>
        <div className="drawer-head">
          <b>{title}</b>
          <button className="btn btn--ghost" onClick={onClose}>
            Done
          </button>
        </div>
        {children}
      </aside>
    </>
  );
}

export function FiltersButton({ onClick, label = "Filters" }: { onClick: () => void; label?: string }) {
  return (
    <button className="btn filters-btn" onClick={onClick}>
      ☰ {label}
    </button>
  );
}
