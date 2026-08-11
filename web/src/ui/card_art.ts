/**
 * Hand-drawn inline-SVG art for tower and raven cards.
 *
 * Everything here is authored rather than sourced, which keeps the set free of
 * attribution requirements and — more usefully — consistent: every face icon is
 * line art on the same 24x24 grid at one stroke weight, drawn with
 * ``stroke: currentColor`` so it picks up the gold/purple card theming for free.
 * The card *backs* are filled silhouettes instead, so they read as a solid
 * emblem at a glance.
 *
 * Add a new icon by adding one entry to TOWER_ICONS / RAVEN_ICONS. Anything
 * without an entry falls back to a neutral card glyph, so a missing icon looks
 * plain rather than broken.
 */

const GRID = 24;

/** Wrap raw path markup in a sized, stroked SVG on the shared grid. */
function strokeIcon(inner: string, size: number, extraClass = ""): string {
  return `<svg viewBox="0 0 ${GRID} ${GRID}" width="${size}" height="${size}" ` +
    `class="card-icon ${extraClass}" fill="none" stroke="currentColor" ` +
    `stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" ` +
    `aria-hidden="true" focusable="false">${inner}</svg>`;
}

/** Same grid, but filled — used for the bold card-back emblems. */
function fillIcon(inner: string, size: number, extraClass = ""): string {
  return `<svg viewBox="0 0 ${GRID} ${GRID}" width="${size}" height="${size}" ` +
    `class="card-icon ${extraClass}" fill="currentColor" stroke="none" ` +
    `aria-hidden="true" focusable="false">${inner}</svg>`;
}

// ---- Card backs ------------------------------------------------------------

/** Crenellated rampart tower, with the door and windows punched out. */
const TOWER_BACK = `
  <path fill-rule="evenodd" d="
    M4 22 L4 7 L7 7 L7 11 L10.5 11 L10.5 7 L13.5 7 L13.5 11 L17 11 L17 7
    L20 7 L20 22 Z
    M10.6 22 L10.6 16.6 A1.4 1.4 0 0 1 13.4 16.6 L13.4 22 Z
    M7.4 13.2 L9.4 13.2 L9.4 16.2 L7.4 16.2 Z
    M14.6 13.2 L16.6 13.2 L16.6 16.2 L14.6 16.2 Z
  "/>
`;

/** Perched raven: tail wedge, body, head, beak, plus a stroked wing line. */
const RAVEN_BACK = `
  <path d="M3.2 16.8 L10 12.8 L11.2 16.4 Z"/>
  <ellipse cx="12.8" cy="13.4" rx="5.1" ry="4"/>
  <circle cx="17.4" cy="8.9" r="3"/>
  <path d="M19.9 8.1 L23.6 9.3 L19.9 10.5 Z"/>
  <circle cx="18.4" cy="8.3" r="0.62" fill="rgba(0,0,0,0.65)"/>
  <path d="M11.4 19.2 h1.2 v2.6 h-1.2 Z"/>
  <path d="M14.6 19.2 h1.2 v2.6 h-1.2 Z"/>
  <path d="M10.2 21.4 h3.2 v1.1 h-3.2 Z"/>
  <path d="M13.4 21.4 h3.2 v1.1 h-3.2 Z"/>
  <path d="M9.6 12.4 q4 1.4 5.9 4.6" fill="none" stroke="rgba(0,0,0,0.45)"
        stroke-width="1.1" stroke-linecap="round"/>
`;

export function towerCardBack(size = 64): string {
  return fillIcon(TOWER_BACK, size, "card-icon-back");
}

export function ravenCardBack(size = 64): string {
  return fillIcon(RAVEN_BACK, size, "card-icon-back");
}

// ---- Tower card faces (keyed by card ``name``) -----------------------------

const TOWER_ICONS: Record<string, string> = {
  // -- burglary tools --
  File: `
    <path d="M14.5 3.5 L20.5 9.5 L11 19 L5 19 L5 13 Z"/>
    <path d="M8.5 11.5 L12.5 15.5 M11 9 L15 13 M6.5 14.5 L9.5 17.5"/>
  `,
  Dynamite: `
    <rect x="4.6" y="9" width="3.6" height="11" rx="1.3"/>
    <rect x="10.2" y="9" width="3.6" height="11" rx="1.3"/>
    <rect x="15.8" y="9" width="3.6" height="11" rx="1.3"/>
    <path d="M3.6 13.4 h16.8"/>
    <path d="M12 9 q0.4 -3.6 3.8 -4.4 t3.2 -2.4"/>
    <path d="M19.6 1.6 l1 1.8 l-2 0.1 Z" fill="currentColor"/>
  `,
  Crowbar: `
    <path d="M8.6 20.4 L15.2 6.6"/>
    <path d="M15.2 6.6 q1.4 -3.2 4.3 -2.6 q2.2 0.5 1.5 2.7"/>
    <path d="M8.6 20.4 l-3.4 1.3 l0.6 -3.6 Z" fill="currentColor"/>
  `,
  "A Key to the Jewel Cases": `
    <circle cx="7.5" cy="7.5" r="3.8"/>
    <path d="M10.2 10.2 L20 20"/>
    <path d="M17 17 l2.4 -2.4 M14.4 14.4 l2.2 -2.2"/>
  `,

  // -- weapons --
  // "Brace" means a pair, so a second pistol sits behind the first.
  "Brace of Pistols": `
    <g opacity="0.45" transform="translate(-1.6,5.4)">
      <path d="M4 6.5 h9.4 v2.9 h-2.2 l-1.1 3.6 q-0.3 1 -1.4 1 h-1.3
               q-1.1 0 -0.9 -1.2 l0.4 -3.4 H4 Z"/>
      <path d="M13.4 7.7 h4.2"/>
    </g>
    <path d="M4 5.8 h9.4 v2.9 h-2.2 l-1.1 3.6 q-0.3 1 -1.4 1 h-1.3
             q-1.1 0 -0.9 -1.2 l0.4 -3.4 H4 Z"/>
    <path d="M13.4 7 h4.6"/>
  `,
  // Seen from above: recurved limbs, string drawn back to the lock, bolt
  // pointing away up the stock.
  Crossbow: `
    <path d="M2.8 10.8 q4.6 -4.6 9.2 -1.4 q4.6 -3.2 9.2 1.4"/>
    <path d="M2.8 10.8 L12 12.8 L21.2 10.8"/>
    <path d="M12 3.6 V20.4"/>
    <path d="M12 3.6 l-1.7 2.7 h3.4 Z" fill="currentColor"/>
    <path d="M9.9 16 h4.2"/>
    <path d="M12 17.8 q-2.3 0.5 -2.7 2.6"/>
  `,
  "Suit of Armour": `
    <path d="M12 3 L19.5 6 v6 q0 6 -7.5 9 Q4.5 18 4.5 12 V6 Z"/>
    <path d="M12 3 V21"/>
    <path d="M7.5 9.5 h9"/>
  `,
  Mace: `
    <path d="M4 20 L10.5 13.5"/>
    <circle cx="14.5" cy="9.5" r="4"/>
    <path d="M14.5 3.5 V5 M14.5 14 v1.5 M8.5 9.5 H10 M19 9.5 h1.5
             M10.3 5.3 l1 1 M17.7 12.7 l1 1 M18.7 5.3 l-1 1 M11.3 12.7 l-1 1"/>
  `,
  // Blade as a tapered triangle rather than a line — the old version had a
  // hook at the point that read as a defect.
  Sword: `
    <path d="M20.6 3.4 L12.4 13.4 L10.6 11.6 Z"/>
    <path d="M8.8 9.8 L14.2 15.2"/>
    <path d="M11.2 12.8 L7.6 16.4"/>
    <circle cx="6.6" cy="17.4" r="1.4"/>
  `,
  Dagger: `
    <path d="M16.8 5.2 L11.8 11.8 L10.2 10.2 Z"/>
    <path d="M9 9 L13 13"/>
    <path d="M10.9 11.5 L7.9 14.5"/>
    <circle cx="7" cy="15.4" r="1.2"/>
  `,

  // -- traversal --
  Ladder: `
    <path d="M8 3 V21 M16 3 V21"/>
    <path d="M8 7 h8 M8 11 h8 M8 15 h8 M8 19 h8"/>
  `,
  Rope: `
    <path d="M6 4.5 q10 2 5.5 6 T7 16.5 q-1 3 5 3.5"/>
    <ellipse cx="15.5" cy="17" rx="4.5" ry="3"/>
  `,

  // -- utility --
  "Tower Pass": `
    <rect x="4" y="4.5" width="16" height="15" rx="2"/>
    <path d="M7.5 8.5 h6 M7.5 12 h4"/>
    <circle cx="15.5" cy="14.5" r="2.6"/>
    <path d="M15.5 17.1 v2.4"/>
  `,
  Sanctuary: `
    <path d="M5 20 V11 q7 -8 14 0 v9 Z"/>
    <path d="M12 4 V9 M9.8 6.3 h4.4"/>
    <path d="M12 20 v-5 a2 2 0 0 1 4 0 v5" opacity="0.6"/>
  `,
  Disguise: `
    <path d="M3.5 8.5 q8.5 -2 17 0 v3 q0 5 -5 5 q-3 0 -3.5 -3 q-0.5 3 -3.5 3
             q-5 0 -5 -5 Z"/>
    <circle cx="8" cy="11.5" r="1.1" fill="currentColor" stroke="none"/>
    <circle cx="16" cy="11.5" r="1.1" fill="currentColor" stroke="none"/>
  `,
  "Royal Pardon": `
    <path d="M5 6.5 q3 -2 6 0 t6 0 v11 q-3 2 -6 0 t-6 0 Z"/>
    <path d="M8.4 14.2 V10.4 l2.1 1.9 L12 9.2 l1.5 3.1 l2.1 -1.9 v3.8 Z"/>
    <path d="M8.4 15.9 h7.2"/>
  `,
  // A pardon from the Rack: the chain comes apart.
  "Rack Pardon": `
    <path d="M5 6.5 q3 -2 6 0 t6 0 v11 q-3 2 -6 0 t-6 0 Z"/>
    <path d="M10.6 10.6 a2.2 2.2 0 1 0 0 3.6"/>
    <path d="M13.4 10.6 a2.2 2.2 0 1 1 0 3.6"/>
    <path d="M11.2 9.4 l-0.9 -1.4 M12.8 9.4 l0.9 -1.4"/>
  `,
  Confession: `
    <path d="M20 3.5 q-9 1.5 -12 9 l-1.5 4.5 l4.5 -1.5 q7.5 -3 9 -12 Z"/>
    <path d="M6.5 17 L4 20"/>
    <path d="M15.5 8 q-4 1.5 -6.5 5.5"/>
  `,

  // -- custom --
  Firecrackers: `
    <path d="M12 12 L12 3.5 M12 12 L20.5 12 M12 12 L3.5 12 M12 12 L12 20.5"/>
    <path d="M12 12 L18 6 M12 12 L6 18 M12 12 L18 18 M12 12 L6 6"/>
    <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/>
  `,
  Lasso: `
    <ellipse cx="12" cy="8" rx="7" ry="4.2"/>
    <path d="M12 12.2 q-1 4 1.5 5.5 t-1 3.3"/>
  `,
  "Binary Disruption": `
    <path d="M12 4 V20"/>
    <path d="M8 4 H4.5 v6"/>
    <path d="M16 4 h3.5 v6"/>
    <path d="M4.5 10 l-1.6 -1.6 M4.5 10 l1.6 -1.6"/>
    <path d="M19.5 10 l-1.6 -1.6 M19.5 10 l1.6 -1.6"/>
    <circle cx="9" cy="16" r="1.1" fill="currentColor" stroke="none"/>
    <circle cx="15" cy="16" r="1.1" fill="currentColor" stroke="none"/>
  `,
  "Mass Accretor": `
    <path d="M6 19 V11 a6 6 0 0 1 12 0 v8"/>
    <path d="M6 19 h4 v-8 a2 2 0 0 1 4 0 v8 h4"/>
    <path d="M6 15.5 h4 M14 15.5 h4"/>
  `,
};

// ---- Raven card faces (keyed by ``effect_key``) ----------------------------

const RAVEN_ICONS: Record<string, string> = {
  // Every "go to <tower>" card shares the tower glyph; the destination is in
  // the card's own text, so 13 near-identical icons would add nothing.
  go_to_location: `
    <path d="M5 20 V8 h2.5 V5 h2.5 v3 h4 V5 h2.5 v3 H19 v12 Z"/>
    <path d="M10.6 20 v-4.4 a1.4 1.4 0 0 1 2.8 0 V20"/>
    <path d="M8.5 11 v2 M15.5 11 v2"/>
  `,
  go_to_jewel_view: `
    <path d="M3.5 17.5 L5.5 8 l4 4 L12 5.5 L14.5 12 l4 -4 l2 9.5 Z"/>
    <path d="M3.5 20 h17"/>
  `,
  call_warder_to_post: `
    <rect x="9" y="2.5" width="6" height="6" rx="1"/>
    <path d="M7 8.5 h10"/>
    <circle cx="12" cy="11.8" r="2.2"/>
    <path d="M7.5 21 v-4 a4.5 4.5 0 0 1 9 0 v4"/>
  `,
  return_warder_to_barracks: `
    <path d="M3.5 11 L12 4.5 L20.5 11"/>
    <path d="M5.5 11 V20 h13 V11"/>
    <path d="M9.5 20 v-5 h5 v5"/>
  `,
  pecked_by_ravens: `
    <path d="M4 15.5 L9.5 12 l1 3.2 Z"/>
    <ellipse cx="12" cy="12.5" rx="4.4" ry="3.4"/>
    <circle cx="16" cy="8.8" r="2.5"/>
    <path d="M18.2 8.2 L21.5 9.2 L18.2 10.2"/>
    <path d="M6.5 19.5 l2 2 M9.5 21.5 l2 -2"/>
  `,
  rest_on_bench: `
    <path d="M3.5 10.5 h17 M3.5 13.5 h17"/>
    <path d="M6 10.5 V20 M18 10.5 V20"/>
    <path d="M6 16.5 h12"/>
  `,
  photo_with_warder: `
    <rect x="3" y="7" width="18" height="12" rx="2"/>
    <path d="M8.5 7 L10 4.5 h4 L15.5 7"/>
    <circle cx="12" cy="13" r="3.4"/>
  `,
  // A search, not a zoom — the glass is over the jewel they're looking for.
  stopped_and_searched: `
    <circle cx="10.5" cy="10.5" r="6"/>
    <path d="M15 15 L20.5 20.5"/>
    <path d="M7.6 10.2 L10.5 7 l2.9 3.2 l-2.9 3.4 Z"/>
  `,
  clerk_tea_exception: `
    <path d="M4.5 8.5 h12 v5 a6 6 0 0 1 -12 0 Z"/>
    <path d="M16.5 9.5 h1.5 a2.5 2.5 0 0 1 0 5 h-1.5"/>
    <path d="M4 20.5 h14"/>
    <path d="M8 5.5 q1 -1.5 0 -3 M12 5.5 q1 -1.5 0 -3"/>
  `,
  // Same cup as the clerk's tea, but this one's an invitation from the
  // Governor — hence the crown rather than the steam.
  governors_tea: `
    <path d="M4.5 10.5 h12 v4.5 a6 6 0 0 1 -12 0 Z"/>
    <path d="M16.5 11.5 h1.5 a2.5 2.5 0 0 1 0 5 h-1.5"/>
    <path d="M4 21 h14"/>
    <path d="M7 8 V4.6 l2 1.7 l1.5 -2.6 l1.5 2.6 l2 -1.7 V8 Z"/>
  `,
  ghost: `
    <path d="M5.5 21 V10.5 a6.5 6.5 0 0 1 13 0 V21 l-2.2 -2 l-2.2 2 l-2.1 -2
             l-2.2 2 l-2.1 -2 Z"/>
    <circle cx="9.8" cy="10.5" r="1.1" fill="currentColor" stroke="none"/>
    <circle cx="14.2" cy="10.5" r="1.1" fill="currentColor" stroke="none"/>
  `,
  queens_birthday: `
    <path d="M4 20 v-6 h16 v6 Z"/>
    <path d="M4 17 q3 -1.6 5.3 0 t5.4 0 t5.3 0"/>
    <path d="M9 14 V11 M12 14 V10 M15 14 V11"/>
    <path d="M9 10.6 q0 -1.4 0 -2 M12 9.6 q0 -1.4 0 -2 M15 10.6 q0 -1.4 0 -2"/>
  `,
  lost: `
    <path d="M12 21 V4"/>
    <path d="M12 6 h7 l-2 2.2 l2 2.2 h-7"/>
    <path d="M12 13 H5 l2 2.2 l-2 2.2 h7"/>
  `,
  chief_yeoman_passes: `
    <circle cx="12" cy="7" r="2.4"/>
    <path d="M7 21 v-4.5 a5 5 0 0 1 10 0 V21"/>
    <path d="M9.5 13.5 h5"/>
  `,
  bowyer_questioning: `
    <rect x="4" y="4" width="16" height="16" rx="2"/>
    <path d="M9.6 9.4 a2.5 2.5 0 1 1 2.9 2.6 v1.4"/>
    <circle cx="12.5" cy="16.2" r="0.95" fill="currentColor" stroke="none"/>
  `,
  shop_for_film: `
    <rect x="3.5" y="6" width="17" height="12" rx="2"/>
    <path d="M3.5 9 h17 M3.5 15 h17"/>
    <path d="M7 6 V18 M17 6 V18"/>
  `,
  beauchamp_imprisonment: `
    <rect x="4" y="4" width="16" height="16" rx="1.5"/>
    <path d="M8.5 4 V20 M12 4 V20 M15.5 4 V20"/>
  `,
  rack_of_torment: `
    <circle cx="12" cy="12" r="7"/>
    <path d="M12 5 V19 M5 12 h14 M7 7 l10 10 M17 7 L7 17"/>
    <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>
  `,
  metallicity: `
    <path d="M4.5 9.5 L6 6.5 l3 1 l1 -3 l3 1.5"/>
    <path d="M6 14.5 l1.6 2.6 l2.9 -1.2 Z"/>
    <path d="M14 11 l2.2 3.6 l4 -1.6 L18 9 Z"/>
    <path d="M17 19.5 l1.4 2 l2.4 -1 Z"/>
  `,
};

// ---- Crown Jewels ----------------------------------------------------------

/**
 * The five jewels, for the board markers. Same 24x24 grid as the cards, but
 * *filled* silhouettes rather than line art: the board disc is only ~22px
 * across, and stroked outlines turn to mush at that size.
 *
 * St Edward's Crown is the arched imperial crown; the Prince of Wales's is an
 * open pointed coronet — the silhouettes differ so the two read apart even as
 * thumbnails. Returned as raw inner markup because the board renderer builds
 * its SVG through the DOM, and wraps these in its own sized, coloured <g>.
 */
const JEWEL_ICONS: Record<string, string> = {
  // The 1-unit gap between band and body matters: both are solid, so without
  // it the two merge into a shapeless blob at board scale.
  crown_st_edward: `
    <path d="M4.2 17.2 h15.6 v3.2 H4.2 Z"/>
    <path d="M5.4 16.2 q0.5 -6.6 6.6 -6.6 t6.6 6.6 Z"/>
    <path d="M11.1 9.8 V7.8 H9.5 V6 h1.6 V4 h1.8 v2 h1.6 v1.8 h-1.6 v2 Z"/>
  `,
  crown_prince_of_wales: `
    <path d="M4.2 17.2 h15.6 v3.2 H4.2 Z"/>
    <path d="M4.6 16.2 L6.2 7 l3.4 4 L12 5 l2.4 6 l3.4 -4 l1.6 9.2 Z"/>
  `,
  orb: `
    <circle cx="12" cy="15.2" r="5.9"/>
    <path d="M11.1 9.8 V7.6 H9.5 V5.8 h1.6 V3.8 h1.8 v2 h1.6 v1.8 h-1.6 v2.2 Z"/>
  `,
  sceptre: `
    <path d="M7.2 19.8 L13.9 8.4 L15.9 9.6 L9.2 21 Z"/>
    <circle cx="17.1" cy="6.5" r="3"/>
  `,
  sword: `
    <path d="M20.6 3.2 L13.7 12.4 L11.5 10.1 Z"/>
    <path d="M10.2 8.8 L15 13.6 L14 14.6 L9.2 9.8 Z"/>
    <path d="M11.6 11 L8 14.6 L9.2 15.8 L12.8 12.2 Z"/>
    <circle cx="7.6" cy="16.4" r="1.8"/>
  `,
};

/** Inner markup for a jewel marker, or "" when the id is unknown. */
export function jewelIconPaths(jewelId: string): string {
  return JEWEL_ICONS[jewelId] ?? "";
}

/** A jewel as it looks on the board: dark emblem on a gold disc. */
export function jewelDisc(jewelId: string, size = 28): string {
  const paths = jewelIconPaths(jewelId);
  if (!paths) return "";
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}"
       class="jewel-disc" aria-hidden="true" focusable="false">
    <circle cx="12" cy="12" r="11" fill="#f1c40f" stroke="#b7950b" stroke-width="1.4"/>
    <g transform="translate(4.8 4.8) scale(0.6)" fill="#3a2c00" stroke="none">${paths}</g>
  </svg>`;
}

/**
 * The jewel emblem on its own, in currentColor — for places that want the
 * shape rather than the board token (the results banner, for one).
 */
export function jewelEmblem(jewelId: string, size = 48): string {
  const paths = jewelIconPaths(jewelId);
  if (!paths) return "";
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}"
       fill="currentColor" stroke="none" aria-hidden="true" focusable="false">${paths}</svg>`;
}

/** The Devereux coin, drawn to match the jewel discs rather than an emoji. */
export function coinDisc(size = 28): string {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}"
       class="coin-disc" aria-hidden="true" focusable="false">
    <circle cx="12" cy="12" r="11" fill="#e8c34a" stroke="#a8842a" stroke-width="1.4"/>
    <circle cx="12" cy="12" r="7.6" fill="none" stroke="#a8842a" stroke-width="1"/>
    <path d="M9.6 15.6 V9.2 l2.4 2.1 L14 8.4 l1.6 2.9 l2.2 -2.1 v6.4 Z"
          transform="translate(-1.6 0.3) scale(0.86)" fill="#7a5f18"/>
  </svg>`;
}

/** Human name for a jewel id. */
export function jewelLabel(jewelId: string): string {
  switch (jewelId) {
    case "crown_st_edward": return "St Edward's Crown";
    case "crown_prince_of_wales": return "Prince of Wales's Crown";
    case "orb": return "The Orb";
    case "sceptre": return "The Sceptre";
    case "sword": return "The Sword";
    default: return jewelId;
  }
}

/** Neutral fallback so an unmapped card looks plain rather than broken. */
const FALLBACK_ICON = `
  <rect x="5" y="3.5" width="14" height="17" rx="2"/>
  <path d="M9 9 h6 M9 12.5 h6 M9 16 h3.5"/>
`;

// ---- Public lookups --------------------------------------------------------

export function towerCardIcon(cardName: string, size = 88): string {
  return strokeIcon(TOWER_ICONS[cardName] ?? FALLBACK_ICON, size, "card-icon-tower");
}

export function ravenCardIcon(effectKey: string, size = 88): string {
  return strokeIcon(RAVEN_ICONS[effectKey] ?? FALLBACK_ICON, size, "card-icon-raven");
}

/** True when we have real art for this card — used to skip the icon slot. */
export function hasTowerIcon(cardName: string): boolean {
  return cardName in TOWER_ICONS;
}
