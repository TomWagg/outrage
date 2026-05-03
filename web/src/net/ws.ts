/**
 * WebSocket client with auto-reconnect and a typed intent/event dispatch.
 */

type Listener = (payload: any) => void;

export interface WsClientOptions {
  url: string;
  onOpen?: () => void;
  onClose?: () => void;
}

export class WsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private eventListeners = new Map<string, Set<Listener>>();
  private pending = new Map<string, (ok: boolean, msg: any) => void>();
  private reqSeq = 0;
  private reconnectTimer: number | null = null;
  private shouldReconnect = true;
  private opts: WsClientOptions;

  constructor(opts: WsClientOptions) {
    this.opts = opts;
    this.url = opts.url;
  }

  connect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws = new WebSocket(this.url);
    this.ws.addEventListener("open", () => {
      this.opts.onOpen?.();
    });
    this.ws.addEventListener("close", () => {
      this.opts.onClose?.();
      if (this.shouldReconnect) {
        this.reconnectTimer = window.setTimeout(() => this.connect(), 1500);
      }
    });
    this.ws.addEventListener("message", (ev) => {
      let msg: any;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      this.dispatch(msg);
    });
    this.ws.addEventListener("error", () => {
      // handled via close
    });
  }

  close(): void {
    this.shouldReconnect = false;
    this.ws?.close();
  }

  on(event: string, fn: Listener): () => void {
    let set = this.eventListeners.get(event);
    if (!set) {
      set = new Set();
      this.eventListeners.set(event, set);
    }
    set.add(fn);
    return () => set!.delete(fn);
  }

  /** Send an intent, optionally awaiting an ack/error. */
  send(name: string, payload: Record<string, unknown> = {}): Promise<void> {
    return new Promise((resolve, reject) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error("not connected"));
        return;
      }
      const request_id = `r${++this.reqSeq}`;
      this.pending.set(request_id, (ok, msg) => {
        if (ok) resolve();
        else reject(new Error(msg?.message || msg?.code || "unknown error"));
      });
      this.ws.send(JSON.stringify({ type: "intent", name, payload, request_id }));
      setTimeout(() => {
        if (this.pending.has(request_id)) {
          this.pending.delete(request_id);
          reject(new Error("request timed out"));
        }
      }, 10_000);
    });
  }

  private dispatch(msg: any): void {
    switch (msg.type) {
      case "ack":
      case "error": {
        const id = msg.request_id;
        if (id && this.pending.has(id)) {
          const cb = this.pending.get(id)!;
          this.pending.delete(id);
          cb(msg.type === "ack", msg);
        }
        if (msg.type === "error") {
          this.emit("__error__", msg);
        }
        return;
      }
      case "event":
        this.emit(msg.name, msg.payload);
        return;
      case "snapshot":
        this.emit("__snapshot__", msg.state);
        return;
    }
  }

  private emit(event: string, payload: any): void {
    const set = this.eventListeners.get(event);
    if (set) for (const fn of set) fn(payload);
  }
}

export function wsUrl(path = "/ws"): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}
