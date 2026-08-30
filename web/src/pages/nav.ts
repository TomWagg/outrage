/**
 * The bar across the top of every information page.
 *
 * Built here rather than copied into each page's HTML so adding a fourth page
 * means editing one list. The link to the game itself is deliberately first and
 * styled as the title: these pages are somewhere you arrive *from* the game and
 * want an obvious way back out of.
 */

interface PageLink {
  href: string;
  label: string;
  /** Matched against the current path to mark the page you're already on. */
  key: string;
}

const LINKS: PageLink[] = [
  { href: "/about.html", label: "About", key: "about" },
  { href: "/rules.html", label: "Rulebook", key: "rules" },
  { href: "/developers.html", label: "Developers", key: "developers" },
];

/**
 * Render the nav into ``#page-nav``.
 *
 * ``current`` is the key of the page doing the rendering, so it can mark itself
 * rather than linking to itself.
 */
export function renderPageNav(current: string): void {
  const host = document.getElementById("page-nav");
  if (!host) return;

  host.className = "page-nav";
  host.innerHTML = "";

  const home = document.createElement("a");
  home.className = "page-nav-home";
  home.href = "/";
  home.textContent = "← Outrage!";
  home.title = "Back to the game";
  host.appendChild(home);

  const links = document.createElement("nav");
  links.className = "page-nav-links";
  links.setAttribute("aria-label", "Information pages");
  for (const link of LINKS) {
    const a = document.createElement("a");
    a.href = link.href;
    a.textContent = link.label;
    if (link.key === current) a.setAttribute("aria-current", "page");
    links.appendChild(a);
  }
  host.appendChild(links);
}
