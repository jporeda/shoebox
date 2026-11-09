/** True when a keystroke is text entry (a form field, an editable element, or an IME
 *  composition in progress), so global shortcuts like R, Y, Esc and arrows must ignore it. */
export function isTypingTarget(e: KeyboardEvent): boolean {
  if (e.isComposing) return true;
  const t = e.target;
  return (
    t instanceof HTMLInputElement ||
    t instanceof HTMLTextAreaElement ||
    t instanceof HTMLSelectElement ||
    (t instanceof HTMLElement && t.isContentEditable)
  );
}
