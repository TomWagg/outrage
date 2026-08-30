/**
 * A hold on anything that would give away a roll the dice are still throwing.
 *
 * The server settles a whole turn in one intent, so a single snapshot carries
 * the dice, the move, the square that was landed on and everything that
 * followed. The dice then animate for the best part of a second — and for that
 * second the rest of the UI knows the answer. Left alone it says so: the piece
 * slides before the dice stop, and a toast announces the jewel you just failed
 * to steal while the dice are still deciding what you rolled.
 *
 * So the reveal is gated. Whoever is running the animation takes a hold; every
 * surface that shows consequences either waits for the hold to lift or is
 * re-rendered when it does.
 *
 * Holds nest, so an animation that starts while another is running keeps the
 * gate shut until both are done. A hold's release is idempotent — calling it
 * twice does not open the gate early on somebody else's behalf.
 */

let holds = 0;
const waiting: (() => void)[] = [];

/** True while at least one animation has the gate shut. */
export function revealsHeld(): boolean {
  return holds > 0;
}

/**
 * Take a hold. Returns the release; call it when the animation finishes.
 *
 * Always pair this with its release on every path, including the error one, or
 * the UI stops updating for the rest of the session.
 */
export function holdReveals(): () => void {
  holds++;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    holds = Math.max(0, holds - 1);
    if (holds === 0) flush();
  };
}

/**
 * Run ``fn`` now if nothing is being held back, otherwise once the gate opens.
 *
 * Queued callbacks run in the order they arrived, so a sequence of events still
 * reads as a sequence rather than arriving jumbled.
 */
export function afterReveals(fn: () => void): void {
  if (holds === 0) {
    fn();
    return;
  }
  waiting.push(fn);
}

function flush(): void {
  // Splice first: a callback may itself take a fresh hold (one animation
  // triggering the next), and anything it queues belongs to that new hold
  // rather than to this flush.
  const due = waiting.splice(0, waiting.length);
  for (const fn of due) {
    try {
      fn();
    } catch (err) {
      // One bad listener must not swallow the rest of the queue.
      console.error("deferred reveal failed", err);
    }
  }
}
