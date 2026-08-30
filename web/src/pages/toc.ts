/**
 * The contents panel that sits in the left margin of every information page.
 *
 * One module for all three pages, because they differ only in what counts as a
 * "section": the rulebook is built from collapsible ``<details>`` boxes, while
 * About and the developer guide are ordinary prose under ``<h2>``s. Both reduce
 * to the same thing — a list of anchors, in document order — so the panel is
 * built from whichever it finds.
 *
 * Three behaviours beyond a plain list of links:
 *
 *   - **Scrollspy.** The entry you are currently reading is marked. Measured
 *     from the live layout on scroll rather than with an IntersectionObserver,
 *     because on the rulebook every section can change height under you as
 *     boxes are opened and closed, and a set of observers configured for one
 *     layout goes wrong in the other.
 *   - **Opening on click.** A rulebook entry whose box is shut opens it before
 *     scrolling, so following a link never lands you on a closed lid.
 *   - **Collapsing.** The panel folds to a tab at the edge, and remembers.
 */

const STORAGE_KEY = "toc:open";

/** Distance from the top of the viewport that counts as "what you're reading". */
const SPY_LINE_PX = 140;

/** Below this the margins are too narrow to hold the panel without overlapping. */
const ROOMY = "(min-width: 1320px)";

interface TocEntry {
  id: string;
  label: string;
  /** The element to scroll to, and to measure for scrollspy. */
  target: HTMLElement;
  /** Set when the target is a collapsible box that a click should open. */
  details: HTMLDetailsElement | null;
}

/**
 * Turn heading text into an id.
 *
 * Only used for pages whose headings have no id of their own; anything already
 * carrying one keeps it, so existing in-page links never break.
 */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .slice(0, 60);
}

/** The label for a rulebook box: its summary, minus the grey aside. */
function summaryLabel(details: HTMLDetailsElement): string {
  const summary = details.querySelector(":scope > summary");
  if (!summary) return details.id;
  const clone = summary.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".rule-section-blurb").forEach((n) => n.remove());
  return clone.textContent?.trim() ?? details.id;
}

function collectEntries(): TocEntry[] {
  const boxes = [...document.querySelectorAll<HTMLDetailsElement>("details.rule-section")];
  if (boxes.length > 0) {
    return boxes
      .filter((d) => d.id)
      .map((d) => ({ id: d.id, label: summaryLabel(d), target: d, details: d }));
  }

  const used = new Set<string>();
  return [...document.querySelectorAll<HTMLElement>("body.page > h2")].map((h) => {
    let id = h.id;
    if (!id) {
      const base = slugify(h.textContent ?? "");
      id = base;
      // Two headings could slugify the same; suffix rather than collide, or
      // both links would jump to the first one.
      let n = 2;
      while (!id || used.has(id) || document.getElementById(id)) id = `${base}-${n++}`;
      h.id = id;
    }
    used.add(id);
    return { id, label: h.textContent?.trim() ?? id, target: h, details: null };
  });
}

export function mountToc(): void {
  const entries = collectEntries();
  if (entries.length < 2) return;   // a one-item contents list is just clutter

  const roomy = window.matchMedia(ROOMY);

  const panel = document.createElement("aside");
  panel.className = "page-toc";
  panel.setAttribute("aria-label", "Contents");

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "page-toc-toggle";
  toggle.setAttribute("aria-controls", "page-toc-list");

  const list = document.createElement("nav");
  list.className = "page-toc-list";
  list.id = "page-toc-list";

  const links = new Map<string, HTMLAnchorElement>();
  for (const entry of entries) {
    const a = document.createElement("a");
    a.href = `#${entry.id}`;
    a.textContent = entry.label;
    a.addEventListener("click", (ev) => {
      ev.preventDefault();
      goTo(entry);
    });
    links.set(entry.id, a);
    list.appendChild(a);
  }

  panel.appendChild(toggle);
  panel.appendChild(list);
  document.body.appendChild(panel);

  // ---- open / closed ------------------------------------------------------

  const stored = localStorage.getItem(STORAGE_KEY);
  // Default to open only where the panel has a margin of its own to sit in.
  // On a narrower screen it overlays the prose, which is fine on demand but
  // not as the state you arrive in.
  let open = stored === null ? roomy.matches : stored === "true";

  function applyOpen(): void {
    panel.classList.toggle("is-open", open);
    toggle.setAttribute("aria-expanded", String(open));
    toggle.textContent = open ? "Contents ✕" : "Contents";
    toggle.title = open ? "Hide contents" : "Show contents";
  }

  toggle.addEventListener("click", () => {
    open = !open;
    localStorage.setItem(STORAGE_KEY, String(open));
    applyOpen();
  });
  applyOpen();

  // ---- navigating ---------------------------------------------------------

  function goTo(entry: TocEntry): void {
    // Open the box first: scrolling to a shut lid tells the reader nothing, and
    // the target's own top does not move when it expands, so the scroll that
    // follows is still aimed correctly.
    if (entry.details && !entry.details.open) entry.details.open = true;
    entry.target.scrollIntoView({ behavior: "smooth", block: "start" });
    history.replaceState(null, "", `#${entry.id}`);
    setActive(entry.id);
    // Where the panel is overlaying the text rather than sitting beside it,
    // get it out of the way of what was just asked for.
    if (!roomy.matches && open) {
      open = false;
      localStorage.setItem(STORAGE_KEY, "false");
      applyOpen();
    }
  }

  // ---- scrollspy ----------------------------------------------------------

  let activeId = "";

  function setActive(id: string): void {
    if (id === activeId) return;
    if (activeId) links.get(activeId)?.removeAttribute("aria-current");
    activeId = id;
    const link = links.get(id);
    if (!link) return;
    link.setAttribute("aria-current", "true");
    // Keep the marked entry visible when the list is long enough to scroll.
    link.scrollIntoView({ block: "nearest" });
  }

  function currentEntry(): TocEntry {
    // The last section whose top has passed the reading line. Works whether the
    // sections are page-height prose or a stack of shut boxes all on screen at
    // once, which an IntersectionObserver does not.
    let found = entries[0];
    for (const entry of entries) {
      if (entry.target.getBoundingClientRect().top <= SPY_LINE_PX) found = entry;
      else break;
    }
    // At the very bottom nothing further can cross the line, so the last
    // section would never light up on a short final section.
    const atBottom =
      window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
    return atBottom ? entries[entries.length - 1] : found;
  }

  let queued = false;
  function spy(): void {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      setActive(currentEntry().id);
    });
  }

  window.addEventListener("scroll", spy, { passive: true });
  window.addEventListener("resize", spy);
  // Opening or closing a rulebook box moves everything below it, so the entry
  // under the reading line changes without the page having scrolled.
  for (const entry of entries) {
    entry.details?.addEventListener("toggle", spy);
  }
  spy();
}
