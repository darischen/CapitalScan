"use client";

import { useEffect, useState } from "react";

/**
 * Hover text, for a screen that cannot hover (DESIGN §11.9).
 *
 * The screener keeps a good deal of meaning in `title` attributes: which
 * session the bands were read at, when the live price was taken, what the
 * `W` mark stands for, why a reversal badge is dashed, how many past
 * signals stand behind a margin of error. A phone shows none of it, and
 * §11.9 requires every function desktop reaches by hover to stay reachable
 * by touch.
 *
 * **One reader for all of them rather than a copy of each text in the
 * markup.** Printing every note inline would put a sentence under most
 * numbers on a card, and the notes exist precisely because they are not
 * worth that room. So a tap on anything carrying a `title` shows that text
 * in a bar at the foot of the screen. The bar has a dismiss button, and a tap
 * on a control or another titled element replaces or clears it; a tap on blank
 * space clears it only where the browser sends a click from it (iOS Safari
 * sends none from a non-interactive element without `cursor: pointer`).
 * The `title` stays the single source, so a note edited for desktop is
 * edited for touch.
 *
 * **Under 720px only, and it renders nothing above it.** The listener
 * returns before reading the event on a wide viewport, so the desktop DOM
 * and behaviour are what they were before this file existed.
 */

/** Anything a tap already means something on. */
const CONTROLS = 'a, button, input, select, textarea, summary, label, [role="option"]';

/** The slice of `Element` this reads, so the rule is testable without a DOM. */
interface Tapped {
  closest(selector: string): { getAttribute(name: string): string | null } | null;
}

/**
 * The note a tap on `target` should show, or `null` for none.
 *
 * A control always wins. A tap on a date arrow steps the date; reading out
 * its `title` first would make it a two-tap control, and the same holds for
 * a control inside a titled cell and for a titled mark inside a link.
 */
export function noteFor(target: Tapped): string | null {
  if (target.closest(CONTROLS)) return null;
  const text = target.closest("[title]")?.getAttribute("title")?.trim();
  return text ? text : null;
}

/** The same breakpoint as every `@media (max-width: 720px)` block. */
const NARROW = "(max-width: 720px)";

export default function TouchNotes() {
  /** False on the server and on first paint, so hydration always matches. */
  const [narrow, setNarrow] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const query = window.matchMedia(NARROW);
    const sync = () => {
      setNarrow(query.matches);
      // A note left up across a rotation to a wide layout would have no bar
      // to sit in and no tap that clears it.
      if (!query.matches) setNote(null);
    };
    sync();
    query.addEventListener("change", sync);

    const onClick = (event: MouseEvent) => {
      if (!query.matches) return;
      const target = event.target;
      if (!(target instanceof Element)) return;
      // The bar's own dismiss button handles itself.
      if (target.closest(".note-bar")) return;
      setNote(noteFor(target));
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setNote(null);
    };
    // Capture, not bubble. The inference dialog stops propagation on its
    // own clicks so that a tap inside it does not reach the scrim, and the
    // dialog is where the notes that matter most are (the sample behind
    // each margin of error).
    document.addEventListener("click", onClick, true);
    document.addEventListener("keydown", onKey);
    return () => {
      query.removeEventListener("change", sync);
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  if (!narrow) return null;

  return (
    <>
      {/* Said once, at the foot of the list. A hover is discovered by
          resting the pointer; nothing on a touch screen suggests that a
          number can be tapped. */}
      <p className="note-hint dim">Tap a label, badge or value to read its note.</p>
      {note !== null && (
        <div className="note-bar" role="status">
          <span>{note}</span>
          <button type="button" aria-label="Dismiss note" onClick={() => setNote(null)}>
            ×
          </button>
        </div>
      )}
    </>
  );
}
