import type { WsClient } from "../net/ws.js";
import { fetchAndRenderStats } from "./stats_table.js";

export function renderLogin(
  root: HTMLElement,
  ws: WsClient,
  onJoined: (username: string) => void,
): void {
  root.innerHTML = `
    <div class="login-screen">
      <h1>Outrage!<br /><small style="color: var(--muted); font-size: 0.7em;">Steal the Crown Jewels</small></h1>
      <p style="color: var(--muted);">Enter a username to join the lobby.</p>
      <div class="row">
        <input id="username" placeholder="Your name" maxlength="24" autofocus />
        <button id="join">Join</button>
      </div>
      <div class="error" id="error"></div>
      <div class="panel" style="margin-top:1.5rem;max-width:700px">
        <h3>Lifetime stats</h3>
        <div id="login-stats-info" style="font-size:0.8rem;color:var(--muted)">Loading…</div>
      </div>
    </div>
  `;

  const input = root.querySelector<HTMLInputElement>("#username")!;
  const button = root.querySelector<HTMLButtonElement>("#join")!;
  const err = root.querySelector<HTMLElement>("#error")!;

  const submit = async () => {
    const name = input.value.trim();
    if (!name) {
      err.textContent = "Enter a username";
      return;
    }
    button.disabled = true;
    err.textContent = "";
    try {
      await ws.send("join", { username: name });
      onJoined(name);
    } catch (e: any) {
      err.textContent = e?.message ?? String(e);
      button.disabled = false;
    }
  };

  button.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") submit();
  });

  // Fetch and render lifetime stats (no username highlight yet — user hasn't joined).
  void fetchAndRenderStats(root.querySelector<HTMLElement>("#login-stats-info")!);

  // Restore last-used name.
  const saved = localStorage.getItem("outrage:username");
  if (saved) input.value = saved;
  input.focus();

  // Save on submit.
  button.addEventListener("click", () => {
    const name = input.value.trim();
    if (name) localStorage.setItem("outrage:username", name);
  });
}
