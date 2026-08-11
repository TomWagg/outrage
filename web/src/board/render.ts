/**
 * SVG renderer for the Outrage! board.
 *
 * The board.json gives every space an integer ``coords`` or ``coords_region``
 * in game-grid units. We project those into pixels with a fixed CELL size
 * and draw each space as a rect, colour-coded by ``kind``/``region``. Player
 * pieces are overlaid as circles. Clickable destinations highlight when the
 * engine is in ``CHOOSING_PATH`` and the viewer is the current player.
 */
import type { BoardData, BoardSpace, GameSnapshot } from "../state.js";
import { jewelIconPaths } from "../ui/card_art.js";

const CELL = 32;                          // pixels per grid unit

// Zoom, persisted across renders (the renderer rebuilds the SVG on every
// event, so this can't live in the DOM). 1 = fit the whole board in the
// viewport; above that the viewport scrolls.
const ZOOM_MIN = 1;
const ZOOM_MAX = 4;
const ZOOM_STEP = 1.25;
let boardZoom = 1;
const PAD = 16;                           // viewport padding
const PIECE_RADIUS = CELL * 0.32;
const MAX_PIECES_PER_SPACE = 6;
const MOVE_ANIM_MS = 320;

// Persisted across renders: current animated pixel position per player.
//
// animatePiece() updates this map each rAF frame so that if renderBoard is
// called mid-tween (from a server event arriving during the animation) the
// new circle starts from wherever the piece currently is rather than jumping
// to the destination and making the move look instant.
const lastPieceCoords = new Map<string, { x: number; y: number }>();

// Same pattern for warder icons — persisted so warder movement animates.
const lastWarderCoords = new Map<string, { x: number; y: number }>();

// Human-readable description for each space kind. Used for tooltips and to
// decide which spaces get the "has an effect" dot indicator.
const SPACE_KIND_DESC: Record<string, string> = {
  start:            "Start — begin your escape from here.",
  escape:           "Escape — reach here with a jewel and coin to win.",
  tower:            "Tower card — draw a tower card on landing.",
  devereux:         "Devereux Tower — draw a tower card. Excess coin is stored here.",
  museum:           "Museum — draw a tower card on landing.",
  hospital:         "Hospital — miss a turn. Also where you're sent after losing combat.",
  royal_armouries:  "Royal Armouries — draw a tower card on landing.",
  shop:             "Shop — browsing takes a while; miss a turn.",
  rack:             "The Rack — sent here by effect. Lose your coin (or whole hand); miss 3 turns.",
  rack_sender:      "Rack sender — can dispatch players to The Rack.",
  bench:            "Bench — sit here and miss a turn.",
  chapel_royal:     "Chapel Royal — landing here ends your turn.",
  chapel_st_john:   "Chapel of St John — landing here ends your turn.",
  bloody_tower:     "Bloody Tower — imprisoned 3 turns. Roll a double to escape early.",
  beauchamp_tower:  "Beauchamp Tower — imprisoned 3 turns. Escape with a double, Rope, or Ladder.",
  bowyer_tower:     "Bowyer Tower — tortured 3 turns. Escape with a double or play Confession.",
  queens_house:     "Queen's House — roll odd each turn to become accredited and enter the Inner Ward freely.",
  barracks:         "Barracks — Yeoman Warder home base.",
  warder_post:      "Warder Post — a Yeoman Warder blocks passage here; play Disguise to pass.",
  jewel:            "Jewel space — attempt to steal the jewel (roll ≥ 12 − your burglary tool values).",
  raven_trigger:    "Raven card — draw a raven card on landing.",
  white_tower_body: "White Tower — jewels are kept here. Firecrackers card affects all occupants.",
};

// Shared tooltip DOM element (created once, reused across renders).
let _tooltipEl: HTMLDivElement | null = null;
function ensureTooltip(): HTMLDivElement {
  if (!_tooltipEl) {
    _tooltipEl = document.createElement("div");
    _tooltipEl.className = "board-tooltip";
    document.body.appendChild(_tooltipEl);
  }
  return _tooltipEl;
}
function showTooltip(text: string, clientX: number, clientY: number): void {
  const el = ensureTooltip();
  el.textContent = text;
  el.style.display = "block";
  el.style.left = `${clientX + 14}px`;
  el.style.top  = `${clientY - 10}px`;
}
function hideTooltip(): void {
  if (_tooltipEl) _tooltipEl.style.display = "none";
}

/**
 * Dismiss the board tooltip from outside the renderer.
 *
 * The tooltip is only hidden by mousemove/mouseleave on the board SVG, so a
 * modal opening over the board leaves the last tooltip stranded on screen —
 * the pointer never moves off the square that spawned it. Anything that covers
 * the board should call this as it opens.
 */
export function hideBoardTooltip(): void {
  hideTooltip();
  // Don't rely on the cached reference: a hot module reload during development
  // leaves the old instance owning the live element, so hide by query too.
  for (const el of document.querySelectorAll<HTMLElement>(".board-tooltip")) {
    el.style.display = "none";
  }
}

// Fill colours keyed by space kind. Anything not listed falls back to a
// region-based default.
// Values use CSS custom properties so the user can override them in main.css.
// CSS variables work in SVG only via element.style.fill (not the fill attribute),
// so spaceRect consumers must apply these through style, not setAttribute.
const KIND_FILL: Record<string, string> = {
  // "normal" is intentionally absent: those spaces fall through to REGION_FILL
  // so wall-walk, inner-ward, and white-tower normals each get their region color.
  start:          "var(--sq-start)",
  escape:         "var(--sq-escape)",
  tower:          "var(--sq-tower)",
  devereux:       "var(--sq-devereux)",
  museum:         "var(--sq-museum)",
  hospital:       "var(--sq-hospital)",
  royal_armouries:"var(--sq-royal-armouries)",
  shop:           "var(--sq-shop)",
  rack:           "var(--sq-rack)",
  rack_sender:    "var(--sq-rack-sender)",
  bench:          "var(--sq-bench)",
  chapel_royal:   "var(--sq-chapel-royal)",
  chapel_st_john: "var(--sq-chapel-st-john)",
  bloody_tower:   "var(--sq-bloody-tower)",
  beauchamp_tower:"var(--sq-beauchamp-tower)",
  bowyer_tower:   "var(--sq-bowyer-tower)",
  queens_house:   "var(--sq-queens-house)",
  barracks:       "var(--sq-barracks)",
  warder_post:    "var(--sq-warder-post)",
  jewel:          "var(--sq-jewel)",
  raven_trigger:  "var(--sq-raven)",
  white_tower_body:"var(--sq-white-tower)",
};

const REGION_FILL: Record<string, string> = {
  wall_walk:      "var(--sq-yellow)",
  inner_ward:     "var(--sq-inner-ward)",
  white_tower:    "var(--sq-white-tower)",
  exterior_south: "var(--sq-exterior)",
  special:        "var(--sq-special)",
};

export interface RenderOptions {
  board: BoardData;
  game: GameSnapshot | null;
  youUsername: string | null;
  /** Invoked when the viewer clicks a highlighted destination. */
  onChooseDestination?: (spaceId: string) => void;
  /** Invoked when the viewer clicks any space (for debug / future UI). */
  onSpaceClick?: (spaceId: string) => void;
}

/**
 * Build (or re-render) the SVG inside ``container``.
 */
export function renderBoard(container: HTMLElement, opts: RenderOptions): void {
  const { board, game, youUsername, onChooseDestination } = opts;

  // ------------------ geometry ------------------
  const bounds = computeBounds(board);
  const w = (bounds.maxX - bounds.minX + 1) * CELL + PAD * 2;
  const h = (bounds.maxY - bounds.minY + 1) * CELL + PAD * 2;

  // Board coords: (0,0) is the BOTTOM-LEFT corner of the physical board,
  // with x growing rightward and y growing upward. SVG's native convention
  // is y-down so we flip y; x maps through directly so the board reads
  // left-to-right as you'd expect standing over the table.
  const toPx = (x: number, y: number) => [
    PAD + (x - bounds.minX) * CELL,
    PAD + (bounds.maxY - y) * CELL,
  ] as const;

  // Who's currently choosing a path?
  const currentTurn = game?.turn_order[game.current_turn_index] ?? null;
  const pm = game?.turn.pending_move ?? null;
  const dests = pm?.destinations ?? null;
  const imChoosing =
    game?.phase === "CHOOSING_PATH" &&
    currentTurn === youUsername &&
    dests &&
    Object.keys(dests).length > 0;
  // When choosing for the split-7 target, use a distinct highlight colour.
  const choosingForTarget = imChoosing && pm?.is_for_target === true;

  // Build the SVG.
  // width/height are intentionally omitted so the SVG is intrinsically sized
  // by its viewBox alone; CSS then sets width:100%/height:auto so the board
  // fills whatever space the container offers and reflows during transitions.
  const svg = createSVG("svg", {
    viewBox: `0 0 ${w} ${h}`,
    "data-board": "1",
  });
  // The SVG fills its canvas box; preserveAspectRatio (default "xMidYMid meet")
  // letterboxes the viewBox inside it, so at zoom 1 the whole board is visible
  // however the container is shaped.
  svg.style.display = "block";
  svg.style.width = "100%";
  svg.style.height = "100%";
  svg.style.background = "#223";
  svg.style.borderRadius = "8px";

  // --- display regions (e.g. raven deck label) ---
  const regionLayer = createSVG("g", { "data-layer": "regions" });
  for (const dr of board.display_regions ?? []) {
    const [[x0, y0], [x1, y1]] = dr.coords_region as [[number, number], [number, number]];
    const [px, py] = toPx(Math.min(x0, x1), Math.max(y0, y1));
    const rw = (Math.abs(x1 - x0) + 1) * CELL;
    const rh = (Math.abs(y1 - y0) + 1) * CELL;
    regionLayer.appendChild(createSVG("rect", {
      x: String(px),
      y: String(py),
      width: String(rw),
      height: String(rh),
      rx: "6",
      ry: "6",
      fill: "#2c3e50",
      stroke: "#7f8c8d"
    }));
    const lbl = createSVG("text", {
      x: String(px + rw / 2),
      y: String(py + rh / 2),
      "text-anchor": "middle",
      "font-size": "10",
      fill: "#bdc3c7",
    });
    lbl.textContent = dr.label;
    regionLayer.appendChild(lbl);
  }
  svg.appendChild(regionLayer);

  // Pixel rect for every space — shared by the slide and edge layers below.
  const rectMap = new Map<string, { x: number; y: number; w: number; h: number }>();
  for (const sp of board.spaces) {
    const r = spaceRect(sp, toPx);
    if (r) rectMap.set(sp.id, r);
  }

  // --- slides ---
  // A slide is a tunnel joining two spaces that aren't grid-adjacent; on the
  // physical board it runs *underneath* the wall walk. We draw it as a rounded
  // rectangle covering the slide's ``path_coords`` cells with a double-headed
  // arrow inside, labelled "SLIDE".
  //
  // The layer is appended before the space layer so that any square the slide
  // passes beneath (the Bloody Tower, ww12, ww20) visibly bridges over it. The
  // label is therefore placed on the longest stretch of the arrow that no
  // space covers, so it never ends up hidden under a bridging square.
  const slideTooltips: [string, string][] = [];
  {
    const slideLayer = createSVG("g", { "data-layer": "slides" });

    for (const sl of board.slides ?? []) {
      const coords = (sl.path_coords ?? []) as [number, number][];
      if (coords.length === 0) continue;

      const srcId = sl.from_space;
      const dstId = sl.to_space;
      const rSrc = rectMap.get(srcId);
      const rDst = rectMap.get(dstId);
      if (!rSrc || !rDst) continue;

      // ---- the rectangle: bounding box of the path cells ----
      const xs = coords.map((c) => c[0]);
      const ys = coords.map((c) => c[1]);
      const [bx0, by0] = toPx(Math.min(...xs), Math.max(...ys));
      const box = {
        x: bx0 + 1,
        y: by0 + 1,
        w: (Math.max(...xs) - Math.min(...xs) + 1) * CELL - 2,
        h: (Math.max(...ys) - Math.min(...ys) + 1) * CELL - 2,
      };

      const slideId = sl.id ?? `${srcId}__${dstId}`;
      const bg = createSVG("rect", {
        x: String(box.x),
        y: String(box.y),
        width: String(box.w),
        height: String(box.h),
        rx: "5",
        ry: "5",
        stroke: "#2f3944",
        "stroke-width": "1.5",
        "data-space-id": `slide:${slideId}`,
      });
      bg.style.fill = "var(--sq-slide)";
      slideLayer.appendChild(bg);
      // Most slide endpoints are plain unlabelled squares, so only name the
      // ones that actually carry a label (e.g. the Cradle Tower exit).
      const named = [srcId, dstId].map((id) => spaceLabel(board, id)).filter(Boolean);
      const dir = sl.bidirectional === false ? "one way" : "in both directions";
      slideTooltips.push([
        `slide:${slideId}`,
        "Slide — a tunnel running under the wall walk.\n" +
          (named.length === 2
            ? `Connects ${named[0]} and ${named[1]}, ${dir}.`
            : named.length === 1
              ? `Connects to ${named[0]}, ${dir}.`
              : `Connects the squares at either end, ${dir}.`),
      ]);

      // ---- the arrow: centres of the path cells, with a stub at each end
      // reaching the border it shares with the space it connects to ----
      const centre = (r: { x: number; y: number; w: number; h: number }) => ({
        x: r.x + r.w / 2,
        y: r.y + r.h / 2,
      });
      const cellCentre = (c: [number, number]) => {
        const [px, py] = toPx(c[0], c[1]);
        return { x: px + CELL / 2, y: py + CELL / 2 };
      };
      const mid = (a: Pt, b: Pt) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
      const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y);

      const cells = coords.map(cellCentre);
      const cSrc = centre(rSrc);
      const cDst = centre(rDst);
      // path_coords are listed from one endpoint to the other, but which
      // endpoint sits at coords[0] isn't guaranteed — pick the nearer one.
      const srcFirst = dist(cells[0], cSrc) <= dist(cells[0], cDst);
      const head = srcFirst ? cSrc : cDst;
      const tail = srcFirst ? cDst : cSrc;
      const pts: Pt[] = [
        mid(cells[0], head),
        ...cells,
        mid(cells[cells.length - 1], tail),
      ];

      const arrowLayer = createSVG("g", { "pointer-events": "none" });
      arrowLayer.appendChild(createSVG("polyline", {
        points: pts.map((p) => `${p.x},${p.y}`).join(" "),
        fill: "none",
        stroke: "var(--sq-slide-ink)",
        "stroke-width": "1.6",
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
      }));

      // Arrowhead at the tail end always; at the head end too when the slide
      // can be travelled in both directions (all of them, currently).
      appendArrowhead(arrowLayer, pts[pts.length - 2], pts[pts.length - 1]);
      if (sl.bidirectional !== false) appendArrowhead(arrowLayer, pts[1], pts[0]);

      // ---- the "SLIDE" label, on the longest uncovered stretch ----
      const blockers = [...rectMap.entries()]
        .filter(([id]) => id !== srcId && id !== dstId)
        .map(([, r]) => r)
        .filter((r) => r.x < box.x + box.w && r.x + r.w > box.x &&
                       r.y < box.y + box.h && r.y + r.h > box.y);
      // Keep clear of both ends so the label pill never sits on an arrowhead.
      const slot = clearestStretch(pts, blockers, 10);
      if (slot) {
        // Shrink the type if the clear stretch is short. "SLIDE" is 5 glyphs
        // and renders roughly 4.6x the font size wide with the letter-spacing
        // below, so this keeps the label inside its stretch.
        const fs = Math.max(5.5, Math.min(8, (slot.length - 8) / 4.6));
        const labelW = fs * 4.6;
        const g = createSVG("g", {
          transform: `translate(${slot.x},${slot.y}) rotate(${slot.angle})`,
          "pointer-events": "none",
        });
        // Pill in the slide's own fill so the arrow shaft doesn't run through
        // the lettering.
        const pill = createSVG("rect", {
          x: String(-labelW / 2 - 3),
          y: String(-fs / 2 - 2),
          width: String(labelW + 6),
          height: String(fs + 4),
          rx: "2",
        });
        pill.style.fill = "var(--sq-slide)";
        g.appendChild(pill);
        const t = createSVG("text", {
          x: "0",
          y: String(fs * 0.36),
          "text-anchor": "middle",
          "font-size": String(fs),
          "letter-spacing": "0.8",
          "font-weight": "bold",
        });
        t.style.fill = "var(--sq-slide-ink)";
        t.textContent = "SLIDE";
        g.appendChild(t);
        arrowLayer.appendChild(g);
      }

      slideLayer.appendChild(arrowLayer);
    }

    svg.appendChild(slideLayer);
  }

  // --- spaces ---
  const spaceLayer = createSVG("g", { "data-layer": "spaces" });
  const NO_CIRCLE_KINDS = new Set(["raven_trigger", "chapel_royal", "chapel_st_john", "museum", "royal_armouries", "hospital", "warder_post", "bench", "rack", "bloody_tower"]);
  for (const sp of board.spaces) {
    const rect = spaceRect(sp, toPx);
    if (!rect) continue;

    const fill = KIND_FILL[sp.kind] ?? REGION_FILL[sp.region] ?? "var(--sq-fallback)";
    const el = createSVG("rect", {
      x: String(rect.x),
      y: String(rect.y),
      width: String(rect.w),
      height: String(rect.h),
      rx: "3",
      ry: "3",
      stroke: "#111",
      "stroke-width": "1",
      "data-space-id": sp.id,
    });
    // CSS variables only work via the style property, not SVG presentation attrs.
    el.style.fill = fill;
    el.style.cursor = "pointer";
    if (imChoosing && dests && sp.id in dests) {
      // Orange for your own movement, purple for "send the target here".
      el.setAttribute("stroke", choosingForTarget ? "#9b59b6" : "#e67e22");
      el.setAttribute("stroke-width", "3");
      el.addEventListener("click", () => onChooseDestination?.(sp.id));
    } else if (opts.onSpaceClick) {
      el.addEventListener("click", () => opts.onSpaceClick!(sp.id));
    }
    // Label: render text inside any non-normal space that has a label.
    // foreignObject lets us use HTML flexbox for centering and word-wrap.
    if (sp.label && sp.kind !== "normal" && sp.kind !== "jewel" && sp.kind !== "rack_sender") {
      const fo = createSVG("foreignObject", {
        x: String(rect.x),
        y: String(rect.y),
        width: String(rect.w),
        height: String(rect.h),
        "pointer-events": "none",
      });
      // Use the HTML namespace so the browser treats the div as HTML inside SVG.
      const div = document.createElementNS(
        "http://www.w3.org/1999/xhtml", "div",
      ) as HTMLElement;
      // Flexbox on the div fills the foreignObject and centres the text.
      // Tall-narrow spaces (warder posts, some wall-walk towers) get vertical
      // writing so the label reads downward rather than wrapping into a sliver.
      const vertical = rect.h > rect.w * 1.5;
      div.style.cssText =
        "width:100%;height:100%;display:flex;align-items:center;" +
        "justify-content:center;text-align:center;font-size:8px;" +
        "color:#111;line-height:1.2;word-break:break-word;" +
        "overflow-wrap:break-word;padding:2px;box-sizing:border-box;" +
        (vertical ? "writing-mode:vertical-rl;" : "");
      div.textContent = sp.label;
      fo.appendChild(div);
      spaceLayer.appendChild(el);
      spaceLayer.appendChild(fo);
    } else {
      spaceLayer.appendChild(el);
    }
    // Draw a small circle inside inner-ward non-raven squares. On the physical
    // board, circles distinguish "safe" inner-ward squares from raven-trigger ones.
    if (sp.region === "inner_ward" && !NO_CIRCLE_KINDS.has(sp.kind)) {
      const r = Math.min(rect.w, rect.h) * 0.28;
      const dot = createSVG("circle", {
        cx: String(rect.x + rect.w / 2),
        cy: String(rect.y + rect.h / 2),
        r: String(r),
        stroke: "#555",
        "stroke-width": "1",
        "pointer-events": "none",
      });
      dot.style.fill = "var(--sq-raven-dot, rgba(0,0,0,0.18))";
      spaceLayer.appendChild(dot);
    }
    // Small black dot in the top-right corner for spaces that have a special
    // effect on landing, so players can spot them at a glance.
    // Raven triggers are excluded — the absence of the inner-ward circle already
    // signals "raven card here". Labeled normal spaces (e.g. wall-walk action
    // squares) are included because their kind gives no hint of the effect.
    const hasEffect =
      (SPACE_KIND_DESC[sp.kind] && sp.kind !== "raven_trigger") ||
      (sp.kind === "normal" && !!sp.label);
    if (hasEffect) {
      const dot = createSVG("circle", {
        cx: String(rect.x + rect.w - 5),
        cy: String(rect.y + 5),
        r: "3",
        fill: "#111",
        "pointer-events": "none",
      });
      spaceLayer.appendChild(dot);
    }
  }
  svg.appendChild(spaceLayer);

  // --- edges (neighbor connections) ---
  // Draw a short line segment (CELL/2 long total, CELL/4 into each space)
  // centred on the shared border between each pair of neighbouring spaces.
  //
  // To avoid diagonal lines wherever possible we snap to an axis when the
  // smaller space's centre falls inside the larger space's extent:
  //   - cx_S within L's x-range → VERTICAL line at x = cx_S
  //   - cy_S within L's y-range → HORIZONTAL line at y = cy_S
  //   - otherwise              → diagonal centre-to-centre fallback
  {
    const edgeLayer = createSVG("g", { "data-layer": "edges", "pointer-events": "none" });

    // Visit each undirected edge exactly once.
    const drawn = new Set<string>();
    for (const sp of board.spaces) {
      const rA = rectMap.get(sp.id);
      if (!rA) continue;

      for (const nid of sp.neighbors) {
        // avoid drawing an edge from chapel to salt tower (too far)
        if ((sp.id === "iw_chapel_royal" && nid === "ww23_salt") ||
            (nid === "iw_chapel_royal" && sp.id === "ww23_salt")) {
          continue;
        }

        const key = sp.id < nid ? `${sp.id}|${nid}` : `${nid}|${sp.id}`;
        if (drawn.has(key)) continue;
        drawn.add(key);
        const rB = rectMap.get(nid);
        if (!rB) continue;

        // Pixel centres.
        const cxA = rA.x + rA.w / 2, cyA = rA.y + rA.h / 2;
        const cxB = rB.x + rB.w / 2, cyB = rB.y + rB.h / 2;

        // S = smaller space, L = larger (by pixel area; equal area → S=A, L=B).
        const useA = rA.w * rA.h <= rB.w * rB.h;
        const [rS, cxS, cyS, rL, cxL, cyL] = useA
          ? [rA, cxA, cyA, rB, cxB, cyB]
          : [rB, cxB, cyB, rA, cxA, cyA];

        let x1: number, y1: number, x2: number, y2: number;

        if (cxS >= rL.x && cxS <= rL.x + rL.w) {
          // ---- vertical line at x = cx_S ----
          // Find the y-edges on each rect that face the other space.
          const ey_s = cyS < cyL ? rS.y + rS.h : rS.y;
          const ey_l = cyS < cyL ? rL.y          : rL.y + rL.h;
          const borderY = (ey_s + ey_l) / 2;
          x1 = cxS; y1 = borderY - CELL / 8;
          x2 = cxS; y2 = borderY + CELL / 8;

        } else if (cyS >= rL.y && cyS <= rL.y + rL.h) {
          // ---- horizontal line at y = cy_S ----
          const ex_s = cxS < cxL ? rS.x + rS.w : rS.x;
          const ex_l = cxS < cxL ? rL.x          : rL.x + rL.w;
          const borderX = (ex_s + ex_l) / 2;
          x1 = borderX - CELL / 8; y1 = cyS;
          x2 = borderX + CELL / 8; y2 = cyS;

        } else {
          // ---- diagonal fallback: centre-to-centre direction, CELL/2 long ----
          const dx = cxB - cxA, dy = cyB - cyA;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist === 0) continue;
          const ux = (dx / dist) * (CELL / 8);
          const uy = (dy / dist) * (CELL / 8);
          const mx = (cxA + cxB) / 2, my = (cyA + cyB) / 2;
          x1 = mx - ux; y1 = my - uy;
          x2 = mx + ux; y2 = my + uy;
        }

        // Ensure (x2, y2) is the endpoint toward B (nid), not just the
        // "larger-y / larger-x" end produced by the axis-snapping above.
        // Dot the line direction against the A→B vector; if it's negative,
        // the endpoints are backwards — swap them.
        const dotAB = (x2 - x1) * (cxB - cxA) + (y2 - y1) * (cyB - cyA);
        if (dotAB < 0) {
          [x1, x2] = [x2, x1];
          [y1, y2] = [y2, y1];
        }

        // One-way if nid does NOT list sp as a neighbour.
        const one_way = !(board.spaces.find((s) => s.id === nid)?.neighbors.includes(sp.id) ?? false);

        edgeLayer.appendChild(createSVG("line", {
          x1: String(x1), y1: String(y1),
          x2: String(x2), y2: String(y2),
          stroke: "rgba(0,0,0,0.3)",
          "stroke-width": "1",
          "stroke-linecap": "round",
        }));

        if (one_way) {
          // Draw a small filled triangle at (x2, y2) pointing in the A→B direction.
          // We build it pointing right (+x) then rotate into place.
          // Note: Math.atan2 returns radians; SVG rotate() takes degrees.
          const angleDeg = Math.atan2(y2 - y1, x2 - x1) * (180 / Math.PI);
          const s = 5; // half-base of the arrowhead triangle
          edgeLayer.appendChild(createSVG("path", {
            d: `M ${x2 + s} ${y2} L ${x2} ${y2 - s / 2} L ${x2} ${y2 + s / 2} Z`,
            fill: "rgba(0,0,0,0.45)",
            transform: `rotate(${angleDeg} ${x2} ${y2})`,
          }));
        }
      }
    }
    svg.appendChild(edgeLayer);
  }

  // --- jewels (at offset from their home space) ---
  if (game) {
    const jewelLayer = createSVG("g", { "data-layer": "jewels" });
    const off = board.jewel_display_offset || { x: 0, y: 0 };
    for (const [jewelId, spaceId] of Object.entries(game.jewels_available)) {
      const sp = board.spaces.find((s) => s.id === spaceId);
      if (!sp || !sp.coords) continue;
      const [px, py] = toPx(sp.coords[0] + off.x, sp.coords[1] + off.y);
      const ring = createSVG("circle", {
        cx: String(px + CELL / 2),
        cy: String(py + CELL / 2),
        r: String(CELL * 0.35),
        fill: "#f1c40f",
        stroke: "#b7950b",
        "stroke-width": "2",
      });
      jewelLayer.appendChild(ring);

      // The jewel's own emblem on the gold disc. The art is authored on a
      // 24x24 grid, so scale it down and offset to the disc's centre. Filled,
      // not stroked — the disc is only ~22px across.
      const ICON_PX = CELL * 0.6;
      const scale = ICON_PX / 24;
      const paths = jewelIconPaths(jewelId);
      if (paths) {
        const g = createSVG("g", {
          transform:
            `translate(${px + CELL / 2 - ICON_PX / 2}, ${py + CELL / 2 - ICON_PX / 2}) ` +
            `scale(${scale})`,
          fill: "#3a2c00",
          stroke: "none",
          "pointer-events": "none",
        });
        g.innerHTML = paths;
        jewelLayer.appendChild(g);
      }
      const title = createSVG("title");
      title.textContent = jewelName(jewelId);
      ring.appendChild(title);
    }
    svg.appendChild(jewelLayer);
  }

  // --- warders ---
  if (game) {
    const warderLayer = createSVG("g", { "data-layer": "warders" });

    // Draw warders at their posts as animated person-with-tall-hat icons.
    const barracksId = board.barracks_space ?? "barracks";
    let barracksCount = 0;

    for (const w of game.warders) {
      const isBarracks = w.location === barracksId;
      const sp = board.spaces.find((s) => s.id === w.location);
      if (!sp) continue;
      const rect = spaceRect(sp, toPx);
      if (!rect) continue;
      const tx = rect.x + rect.w / 2;
      const ty = rect.y + rect.h / 2;

      if (isBarracks) {
        // Accumulate count; rendered as a single badge after the loop.
        barracksCount++;
        // Still track coords so animation works if they leave barracks.
        if (!lastWarderCoords.has(w.id)) {
          // Spread warders across barracks width so they don't all animate
          // from exactly the same point.
          const idx = barracksCount - 1;
          const spread = rect.w / 5;
          lastWarderCoords.set(w.id, { x: rect.x + spread * (idx + 0.75), y: ty });
        }
        continue;
      }

      const prev = lastWarderCoords.get(w.id);
      const startX = prev ? prev.x : tx;
      const startY = prev ? prev.y : ty;

      const g = createSVG("g");
      g.setAttribute("transform", `translate(${startX},${startY})`);
      appendWarderIcon(g);
      warderLayer.appendChild(g);

      if (startX !== tx || startY !== ty) {
        lastWarderCoords.set(w.id, { x: startX, y: startY });
        animateWarder(g, w.id, startX, startY, tx, ty, MOVE_ANIM_MS * 1.5);
      } else {
        lastWarderCoords.set(w.id, { x: tx, y: ty });
      }
    }

    // Barracks badge: mini icons for each warder stowed there.
    if (barracksCount > 0) {
      const barracksSp = board.spaces.find((s) => s.id === barracksId);
      if (barracksSp) {
        const r = spaceRect(barracksSp, toPx);
        if (r) {
          // Spread mini warder icons evenly across the barracks strip.
          const slotW = r.w / 4;
          for (let i = 0; i < barracksCount && i < 4; i++) {
            const iconX = r.x + slotW * i + slotW / 2;
            const iconY = r.y + r.h / 2;
            const mg = createSVG("g");
            mg.setAttribute("transform", `translate(${iconX},${iconY}) scale(0.55)`);
            appendWarderIcon(mg, true);
            warderLayer.appendChild(mg);
          }
        }
      }
    }

    // Prune coords for warders no longer in the game.
    const activeIds = new Set(game.warders.map((w) => w.id));
    for (const id of [...lastWarderCoords.keys()]) {
      if (!activeIds.has(id)) lastWarderCoords.delete(id);
    }

    svg.appendChild(warderLayer);
  }

  // --- pieces ---
  if (game) {
    const pieceLayer = createSVG("g", { "data-layer": "pieces" });
    // Group players by position for neat stacking.
    const byPos = new Map<string, typeof game.players>();
    for (const p of game.players) {
      if (p.escaped) continue;
      const list = byPos.get(p.position) ?? [];
      list.push(p);
      byPos.set(p.position, list);
    }
    for (const [spaceId, occupants] of byPos) {
      const sp = board.spaces.find((s) => s.id === spaceId);
      if (!sp) continue;
      const rect = spaceRect(sp, toPx);
      if (!rect) continue;
      const cx = rect.x + rect.w / 2;
      const cy = rect.y + rect.h / 2;
      occupants.slice(0, MAX_PIECES_PER_SPACE).forEach((p, i) => {
        const angle = (i / occupants.length) * 2 * Math.PI;
        const offset = occupants.length === 1 ? 0 : CELL * 0.2;
        const dx = Math.cos(angle) * offset;
        const dy = Math.sin(angle) * offset;
        const isTurn = p.username === currentTurn;
        const targetX = cx + dx;
        const targetY = cy + dy;
        const prev = lastPieceCoords.get(p.username);
        // If we rendered this piece before and the target coords have moved,
        // start the circle at the old coords and tween to the target.
        const startX = prev ? prev.x : targetX;
        const startY = prev ? prev.y : targetY;
        const c = createSVG("circle", {
          cx: String(startX),
          cy: String(startY),
          r: String(PIECE_RADIUS),
          fill: p.color,
          stroke: isTurn ? "#fff" : "#111",
          "stroke-width": isTurn ? "2.5" : "1",
        });
        const title = createSVG("title");
        title.textContent = `${p.username}${isTurn ? " (to play)" : ""}`;
        c.appendChild(title);
        pieceLayer.appendChild(c);

        if (startX !== targetX || startY !== targetY) {
          // Keep lastPieceCoords at the START for now.  animatePiece will
          // update it to the current interpolated position each rAF frame so
          // that any re-render that fires mid-tween starts from wherever the
          // piece currently is rather than teleporting to the destination.
          lastPieceCoords.set(p.username, { x: startX, y: startY });
          animatePiece(c, p.username, startX, startY, targetX, targetY, MOVE_ANIM_MS);
        } else {
          lastPieceCoords.set(p.username, { x: targetX, y: targetY });
        }
      });
    }
    // Prune history for players no longer on the board (escaped / removed).
    const living = new Set(game.players.filter((p) => !p.escaped).map((p) => p.username));
    for (const name of [...lastPieceCoords.keys()]) {
      if (!living.has(name)) lastPieceCoords.delete(name);
    }
    svg.appendChild(pieceLayer);
  }

  // Build tooltip text for every space, keyed by id.
  const tooltipMap = new Map<string, string>();
  for (const sp of board.spaces) {
    const kindDesc = SPACE_KIND_DESC[sp.kind];
    if (kindDesc) {
      // Named kind: show label (if any) then the kind description.
      tooltipMap.set(sp.id, sp.label ? `${sp.label}\n${kindDesc}` : kindDesc);
    } else if (sp.kind === "normal" && sp.label) {
      // Labeled normal space (e.g. wall-walk action squares): the label is the
      // full description — there's no separate kind text to add.
      tooltipMap.set(sp.id, sp.label);
    }
  }
  for (const [key, text] of slideTooltips) tooltipMap.set(key, text);
  // Augment warder-post tooltips with occupancy status.
  if (game) {
    const barracksId = board.barracks_space ?? "barracks";
    for (const sp of board.spaces.filter((s) => s.kind === "warder_post")) {
      const occupied = game.warders.some(
        (w) => w.location === sp.id && w.location !== barracksId,
      );
      const base = tooltipMap.get(sp.id) ?? (sp.label ?? sp.id);
      tooltipMap.set(sp.id, `${base}\n${occupied ? "⚠ Warder on duty — need Disguise to pass" : "Warder post (unoccupied)"}`);
    }
  }

  // Event-delegated tooltip: a single pair of listeners on the SVG rather than
  // one per rect so they survive piece-layer rebuilds without accumulating.
  svg.addEventListener("mousemove", (e) => {
    const target = (e.target as Element).closest<Element>("[data-space-id]");
    if (target) {
      const text = tooltipMap.get(target.getAttribute("data-space-id")!);
      if (text) { showTooltip(text, e.clientX, e.clientY); return; }
    }
    hideTooltip();
  });
  svg.addEventListener("mouseleave", hideTooltip);

  mountBoard(container, svg);
}

/**
 * Put ``svg`` into the container's zoom scaffolding, building it on first use.
 *
 * renderBoard runs on every server event and rebuilds the SVG from scratch, so
 * this preserves both the zoom level (module-level) and the viewport's scroll
 * position — otherwise a zoomed-in player would be thrown back to the top-left
 * corner every time anything happened.
 */
function mountBoard(container: HTMLElement, svg: SVGElement): void {
  let viewport = container.querySelector<HTMLElement>(".board-viewport");
  let canvas = container.querySelector<HTMLElement>(".board-canvas");

  if (!viewport || !canvas) {
    container.innerHTML = "";
    container.appendChild(buildZoomBar(container));
    viewport = document.createElement("div");
    viewport.className = "board-viewport";
    canvas = document.createElement("div");
    canvas.className = "board-canvas";
    viewport.appendChild(canvas);
    container.appendChild(viewport);
  }

  const { scrollLeft, scrollTop } = viewport;
  canvas.innerHTML = "";
  canvas.appendChild(svg);
  applyZoom(container);
  viewport.scrollLeft = scrollLeft;
  viewport.scrollTop = scrollTop;
}

function buildZoomBar(container: HTMLElement): HTMLElement {
  const bar = document.createElement("div");
  bar.className = "board-zoombar";
  bar.innerHTML = `
    <button type="button" data-zoom="out" title="Zoom out">−</button>
    <span class="board-zoom-level" data-zoom-level>100%</span>
    <button type="button" data-zoom="in" title="Zoom in">+</button>
    <button type="button" data-zoom="fit" title="Fit the whole board">Fit</button>
  `;
  bar.addEventListener("click", (e) => {
    const action = (e.target as HTMLElement).closest("[data-zoom]")?.getAttribute("data-zoom");
    if (!action) return;
    const next =
      action === "in" ? boardZoom * ZOOM_STEP
      : action === "out" ? boardZoom / ZOOM_STEP
      : 1;
    setZoom(container, next);
  });
  return bar;
}

/**
 * Set the zoom level, keeping whatever is currently in the middle of the
 * viewport in the middle afterwards — zooming from the top-left corner makes
 * the board feel like it's running away from you.
 */
function setZoom(container: HTMLElement, next: number): void {
  const viewport = container.querySelector<HTMLElement>(".board-viewport");
  const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next));
  if (!viewport || clamped === boardZoom) return;

  // Fraction of the scrollable content currently at the viewport's centre.
  const cx = (viewport.scrollLeft + viewport.clientWidth / 2) / viewport.scrollWidth;
  const cy = (viewport.scrollTop + viewport.clientHeight / 2) / viewport.scrollHeight;

  boardZoom = clamped;
  applyZoom(container);

  viewport.scrollLeft = cx * viewport.scrollWidth - viewport.clientWidth / 2;
  viewport.scrollTop = cy * viewport.scrollHeight - viewport.clientHeight / 2;
}

function applyZoom(container: HTMLElement): void {
  const canvas = container.querySelector<HTMLElement>(".board-canvas");
  if (canvas) {
    canvas.style.width = `${boardZoom * 100}%`;
    canvas.style.height = `${boardZoom * 100}%`;
  }
  const level = container.querySelector<HTMLElement>("[data-zoom-level]");
  if (level) level.textContent = `${Math.round(boardZoom * 100)}%`;
  const out = container.querySelector<HTMLButtonElement>('[data-zoom="out"]');
  const zin = container.querySelector<HTMLButtonElement>('[data-zoom="in"]');
  if (out) out.disabled = boardZoom <= ZOOM_MIN + 1e-6;
  if (zin) zin.disabled = boardZoom >= ZOOM_MAX - 1e-6;
}

// ---------- helpers ----------

interface Bounds { minX: number; maxX: number; minY: number; maxY: number; }

interface Pt { x: number; y: number; }
interface PxRect { x: number; y: number; w: number; h: number; }

/** A space's human label, or "" when it has none (ids are not user-facing). */
function spaceLabel(board: BoardData, id: string): string {
  return board.spaces.find((s) => s.id === id)?.label ?? "";
}

/** Filled triangle at ``tip``, pointing in the ``from`` → ``tip`` direction. */
function appendArrowhead(parent: SVGElement, from: Pt, tip: Pt): void {
  const angleDeg = Math.atan2(tip.y - from.y, tip.x - from.x) * (180 / Math.PI);
  const len = 6, half = 3.4;
  const head = createSVG("path", {
    // Built pointing right (+x) about (tip), then rotated into place.
    d: `M ${tip.x} ${tip.y} L ${tip.x - len} ${tip.y - half} L ${tip.x - len} ${tip.y + half} Z`,
    transform: `rotate(${angleDeg} ${tip.x} ${tip.y})`,
  });
  head.style.fill = "var(--sq-slide-ink)";
  parent.appendChild(head);
}

/**
 * Walk the polyline ``pts`` and return the midpoint of its longest run that
 * no rect in ``blockers`` covers, along with the local heading (in degrees,
 * normalised so text never reads upside-down) and the run's pixel length.
 * ``endClearance`` px at each end of the polyline are treated as covered.
 *
 * Used to park the "SLIDE" label where the squares bridging over the slide
 * won't hide it. Returns null for a degenerate (zero-length) polyline.
 */
function clearestStretch(
  pts: Pt[],
  blockers: PxRect[],
  endClearance = 0,
): { x: number; y: number; angle: number; length: number } | null {
  const segs: { a: Pt; b: Pt; len: number }[] = [];
  let total = 0;
  for (let i = 1; i < pts.length; i++) {
    const len = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    if (len > 0) { segs.push({ a: pts[i - 1], b: pts[i], len }); total += len; }
  }
  if (total === 0) return null;

  const at = (d: number): { p: Pt; angle: number } => {
    let rem = Math.min(Math.max(d, 0), total);
    for (const s of segs) {
      if (rem <= s.len) {
        const t = rem / s.len;
        return {
          p: { x: s.a.x + (s.b.x - s.a.x) * t, y: s.a.y + (s.b.y - s.a.y) * t },
          angle: Math.atan2(s.b.y - s.a.y, s.b.x - s.a.x) * (180 / Math.PI),
        };
      }
      rem -= s.len;
    }
    const last = segs[segs.length - 1];
    return {
      p: last.b,
      angle: Math.atan2(last.b.y - last.a.y, last.b.x - last.a.x) * (180 / Math.PI),
    };
  };

  // A couple of px of slack so the label doesn't crowd a bridging square.
  const M = 2;
  const covered = (p: Pt) => blockers.some(
    (r) => p.x >= r.x - M && p.x <= r.x + r.w + M &&
           p.y >= r.y - M && p.y <= r.y + r.h + M,
  );

  const N = 160;
  let bestStart = 0, bestEnd = -1, runStart = -1;
  for (let i = 0; i <= N; i++) {
    const d = (i / N) * total;
    if (d >= endClearance && d <= total - endClearance && !covered(at(d).p)) {
      if (runStart < 0) runStart = i;
      if (i - runStart > bestEnd - bestStart) { bestStart = runStart; bestEnd = i; }
    } else {
      runStart = -1;
    }
  }
  // Fully covered: fall back to the polyline's midpoint rather than dropping
  // the label entirely.
  if (bestEnd < bestStart) { bestStart = 0; bestEnd = N; }

  const dMid = ((bestStart + bestEnd) / 2 / N) * total;
  const { p, angle } = at(dMid);
  const norm = angle > 90 ? angle - 180 : angle < -90 ? angle + 180 : angle;
  return { x: p.x, y: p.y, angle: norm, length: ((bestEnd - bestStart) / N) * total };
}

function computeBounds(board: BoardData): Bounds {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const push = (x: number, y: number) => {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  };
  for (const sp of board.spaces) {
    if (sp.coords) push(sp.coords[0], sp.coords[1]);
    if (sp.coords_region) for (const c of sp.coords_region) push(c[0], c[1]);
  }
  for (const dr of board.display_regions ?? []) {
    for (const c of dr.coords_region ?? []) push(c[0], c[1]);
  }
  if (!isFinite(minX)) return { minX: 0, maxX: 10, minY: 0, maxY: 10 };
  return { minX, maxX, minY, maxY };
}

function spaceRect(
  sp: BoardSpace,
  toPx: (x: number, y: number) => readonly [number, number],
): { x: number; y: number; w: number; h: number } | null {
  if (sp.coords_region && sp.coords_region.length >= 2) {
    const xs = sp.coords_region.map((c) => c[0]);
    const ys = sp.coords_region.map((c) => c[1]);
    // The *pixel* top-left corner is min board-x (flipped no more) and
    // max board-y (y is still flipped because SVG is y-down).
    const [x0, y0] = toPx(Math.min(...xs), Math.max(...ys));
    const w = (Math.max(...xs) - Math.min(...xs) + 1) * CELL;
    const h = (Math.max(...ys) - Math.min(...ys) + 1) * CELL;
    return { x: x0 + 1, y: y0 + 1, w: w - 2, h: h - 2 };
  }
  if (!sp.coords) return null;
  const [px, py] = toPx(sp.coords[0], sp.coords[1]);
  return { x: px + 1, y: py + 1, w: CELL - 2, h: CELL - 2 };
}


function jewelName(id: string): string {
  switch (id) {
    case "crown_st_edward": return "St Edward's Crown";
    case "crown_prince_of_wales": return "The Prince of Wales's Crown";
    case "orb": return "The Sovereign's Orb";
    case "sceptre": return "The Sceptre";
    case "sword": return "The Sword";
    default: return id;
  }
}

/**
 * Draw a Yeoman-Warder silhouette (person + tall flat-topped hat) centred on
 * (0, 0) into ``parent``.  Pass ``muted=true`` for the greyed-out barracks
 * mini-icons.
 *
 * Approximate bounding box: ±7 × –20..+14  (≈ 14 × 34 px).
 */
function appendWarderIcon(parent: SVGElement, muted = false): void {
  const hatColor  = muted ? "#555" : "#1a1a2e";
  const bodyColor = muted ? "#777" : "#8B1A1A";
  const skinColor = muted ? "#999" : "#e8c49a";
  const legColor  = muted ? "#555" : "#1a1a2e";
  const outlineColour = "#000"; // stroke colour for all shapes

  const shapes: Array<[string, Record<string, string>]> = [
    // Hat crown (tall flat-top)
    ["rect", { x: "-4.5", y: "-19", width: "9",  height: "11", rx: "1",   fill: hatColor, stroke: outlineColour }],
    // Hat brim
    ["rect", { x: "-6.5", y: "-9",  width: "13", height: "2.5",          fill: hatColor, stroke: outlineColour }],
    // Head
    ["circle", { cx: "0",  cy: "-4",  r: "3.8",                           fill: skinColor, stroke: outlineColour }],
    // Body / tunic
    ["rect", { x: "-5",   y: "0",   width: "10", height: "8",  rx: "1.5", fill: bodyColor, stroke: outlineColour }],
    // Left leg
    ["rect", { x: "-4.5", y: "8",   width: "3.5", height: "6", rx: "1",  fill: legColor, stroke: outlineColour  }],
    // Right leg
    ["rect", { x: "1",    y: "8",   width: "3.5", height: "6", rx: "1",  fill: legColor, stroke: outlineColour  }],
  ];

  for (const [tag, attrs] of shapes) {
    parent.appendChild(createSVG(tag, { ...attrs, "pointer-events": "none" }));
  }
}

/**
 * Animate a warder ``<g>`` element from (x0,y0) to (x1,y1) via its
 * ``transform`` attribute, updating ``lastWarderCoords`` each frame.
 */
function animateWarder(
  el: SVGElement,
  warderId: string,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  durationMs: number,
): void {
  const start = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / durationMs);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const cx = x0 + (x1 - x0) * e;
    const cy = y0 + (y1 - y0) * e;
    el.setAttribute("transform", `translate(${cx},${cy})`);
    lastWarderCoords.set(warderId, { x: cx, y: cy });
    if (t < 1 && el.isConnected) {
      requestAnimationFrame(step);
    } else {
      lastWarderCoords.set(warderId, { x: x1, y: y1 });
    }
  };
  requestAnimationFrame(step);
}

function createSVG(tag: string, attrs: Record<string, string> = {}): SVGElement {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function animatePiece(
  el: SVGElement,
  username: string,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  durationMs: number,
): void {
  // rAF tween with cubic ease-in-out.
  //
  // We update lastPieceCoords on every frame so that if the board is
  // re-rendered mid-tween (from a server event, notification, etc.) the new
  // circle starts from the current animated position rather than from the
  // destination — which would make the move look instant.
  //
  // When el.isConnected becomes false the SVG was wiped by a re-render; we
  // stop this cycle because the new renderBoard() will have spawned a fresh
  // animatePiece() call starting from wherever lastPieceCoords was at the
  // time of the rebuild.
  const start = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - start) / durationMs);
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    const cx = x0 + (x1 - x0) * e;
    const cy = y0 + (y1 - y0) * e;
    el.setAttribute("cx", String(cx));
    el.setAttribute("cy", String(cy));
    // Track current position so the next renderBoard starts from here.
    lastPieceCoords.set(username, { x: cx, y: cy });
    if (t < 1 && el.isConnected) {
      requestAnimationFrame(step);
    } else {
      // Snap to exact final position when done (or when detached).
      lastPieceCoords.set(username, { x: x1, y: y1 });
    }
  };
  requestAnimationFrame(step);
}
