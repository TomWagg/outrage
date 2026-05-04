/**
 * Animated dice display for two d6.
 *
 * Call ``createDiceDisplay(container)`` once to build the DOM, then call
 * ``display.roll(d1, d2)`` whenever a dice_rolled event arrives.  The dice
 * rapidly cycle through random faces (decelerating like real dice slowing
 * down) before settling on the final values.  Doubles get a gold border.
 */

// Dot grid positions: [row, col] where row 0 = top, col 0 = left.
// Each position maps to top/left percentages of 25 / 50 / 75.
const FACE_DOTS: Record<number, [number, number][]> = {
  1: [            [1, 1]                   ],
  2: [[0, 2],                   [2, 0]     ],
  3: [[0, 2],    [1, 1],        [2, 0]     ],
  4: [[0, 0], [0, 2], [2, 0], [2, 2]      ],
  5: [[0, 0], [0, 2], [1, 1], [2, 0], [2, 2]],
  6: [[0, 0], [1, 0], [2, 0], [0, 2], [1, 2], [2, 2]],
};

function renderFace(die: HTMLElement, value: number, isDouble: boolean): void {
  die.innerHTML = "";
  die.classList.toggle("die-double", isDouble);
  for (const [row, col] of FACE_DOTS[value] ?? []) {
    const dot = document.createElement("span");
    dot.className = "die-dot";
    dot.style.top  = `${25 + row * 25}%`;
    dot.style.left = `${25 + col * 25}%`;
    die.appendChild(dot);
  }
}

export interface DiceDisplay {
  el: HTMLElement;
  /** Animate then land on (d1, d2). */
  roll(d1: number, d2: number): void;
  /** Show the last roll without animating (e.g. on reconnect). */
  show(d1: number, d2: number): void;
}

export function createDiceDisplay(): DiceDisplay {
  const wrap = document.createElement("div");
  wrap.className = "dice-wrap";

  const die1 = document.createElement("div");
  die1.className = "die";
  const die2 = document.createElement("div");
  die2.className = "die";
  const sumEl = document.createElement("div");
  sumEl.className = "dice-sum";

  wrap.appendChild(die1);
  wrap.appendChild(die2);
  wrap.appendChild(sumEl);

  // Start with a neutral-looking face.
  renderFace(die1, 1, false);
  renderFace(die2, 1, false);

  // Scheduled timeout ids so we can cancel a mid-animation roll.
  const pending: ReturnType<typeof setTimeout>[] = [];

  function clearPending(): void {
    for (const id of pending) clearTimeout(id);
    pending.length = 0;
  }

  function show(d1: number, d2: number): void {
    const dbl = d1 === d2;
    renderFace(die1, d1, dbl);
    renderFace(die2, d2, dbl);
    sumEl.textContent = String(d1 + d2);
    sumEl.classList.toggle("dice-sum-double", dbl);
  }

  function roll(d1: number, d2: number): void {
    clearPending();
    sumEl.textContent = "";
    sumEl.classList.remove("dice-sum-double");
    die1.classList.remove("die-double");
    die2.classList.remove("die-double");

    // Build a schedule of random frames: intervals start short (~50 ms) and
    // stretch out exponentially, mimicking dice decelerating on a table.
    const frames: number[] = [];
    let t = 0;
    let interval = 50;
    while (t < 720) {
      frames.push(t);
      t += Math.round(interval);
      interval = Math.min(interval * 1.22, 200);
    }

    for (const delay of frames) {
      pending.push(
        setTimeout(() => {
          renderFace(die1, Math.ceil(Math.random() * 6), false);
          renderFace(die2, Math.ceil(Math.random() * 6), false);
        }, delay),
      );
    }

    // Land on the final values after the last random frame.
    pending.push(setTimeout(() => show(d1, d2), t));
  }

  return { el: wrap, roll, show };
}
