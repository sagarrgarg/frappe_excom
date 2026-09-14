/**
 * The softphone, as a module-level singleton.
 *
 * A SIP registration is what makes an agent reachable, so it must outlive every route change and
 * must never exist twice. Both rules force it out of React: a hook that owned it would drop the
 * registration on unmount, and StrictMode's double mount would open two. So this module owns the
 * state and React subscribes to it.
 *
 * No vendor appears below this line. Each line's SDK lives in a driver under `lib/voice/`, and
 * this file only knows about registrations, one call, and the listeners. That split is what lets
 * an agent hold the Indian line on Plivo and the international line on Twilio at the same time.
 */

import { makeDriver, type DriverEvent, type SoftphoneDriver } from "./voice/driver";

export type RegistrationState =
  | "idle"          // nothing attempted yet
  | "loading"       // fetching a token / loading the SDK
  | "registering"   // socket opening
  | "registered"    // reachable
  | "failed"        // token or login rejected
  | "unsupported";  // no WebRTC in this browser, or the network blocks it

export type CallPhase = "none" | "ringing" | "outgoing" | "connected" | "ending";

export interface CallState {
  phase: CallPhase;
  callUUID: string | null;
  direction: "Inbound" | "Outbound" | null;
  peerNumber: string;
  /** Excom's own record, once the backend has told us about it. */
  callName: string | null;
  threadId: string | null;
  displayName: string;
  muted: boolean;
  startedAt: number | null;
  /** Only ever "Browser" here — a call on the agent's handset never reaches an SDK. */
  transport?: "Browser" | "Phone";
  /** Which line is carrying this call. Every control has to reach that line's own driver. */
  account: string | null;
}

/** One line's own registration, so the UI can name the desk that is down. */
export interface LineState {
  account: string;
  registration: RegistrationState;
  error: string;
}

export interface SoftphoneState {
  /** The best state across every line: an agent with one line up is reachable. */
  registration: RegistrationState;
  /** The line carrying the current call, else the first one that is up. For display. */
  activeAccount: string | null;
  error: string;
  micDenied: boolean;
  call: CallState;
  /** Last quality sample a driver reported, for the "the line was terrible" conversation. */
  quality: Record<string, unknown> | null;
  /** Per line, keyed by account name. */
  lines: Record<string, LineState>;
}

const EMPTY_CALL: CallState = {
  phase: "none",
  callUUID: null,
  direction: null,
  peerNumber: "",
  callName: null,
  threadId: null,
  displayName: "",
  muted: false,
  startedAt: null,
  account: null,
};

const INITIAL: SoftphoneState = {
  registration: "idle",
  activeAccount: null,
  error: "",
  micDenied: false,
  call: EMPTY_CALL,
  quality: null,
  lines: {},
};

/** Worst to best. The agent is as reachable as their best line. */
const REGISTRATION_RANK: RegistrationState[] = [
  "unsupported",
  "failed",
  "idle",
  "loading",
  "registering",
  "registered",
];

const REGISTRATION_TIMEOUT_MS = 20_000;

type Listener = (state: SoftphoneState) => void;

/**
 * Lifecycle tracing. On by default: a softphone that silently fails to register is the single
 * hardest thing to diagnose in this feature, and the agent reporting it cannot see server logs.
 * Turn it off with `localStorage.excom_softphone_debug = "0"`.
 */
const DEBUG = (() => {
  try {
    return localStorage.getItem("excom_softphone_debug") !== "0";
  } catch {
    return true;
  }
})();

export interface BootOptions {
  /** The line this driver serves. */
  account: string;
  /** Which vendor SDK, from the backend's own descriptor. */
  sdk: string;
  token: string;
  options: Record<string, unknown>;
  /** Called when a driver reports an incoming call, so the app can resolve the caller. */
  onIncoming?: (callUUID: string, from: string, account: string) => void;
  /**
   * Called when an outgoing leg gets its id. That does not happen synchronously inside `call()`,
   * so this is the only correct moment to register the leg with the server.
   */
  onOutgoing?: (callUUID: string, to: string, account: string) => void;
  /** Called when a call ends, with the id, so the app can refresh the thread. */
  onEnded?: (callUUID: string | null) => void;
  /** Called with quality samples worth persisting. */
  onQuality?: (callUUID: string | null, metrics: Record<string, unknown>) => void;
}

interface LineRuntime {
  driver: SoftphoneDriver;
  sdk: string;
}

class Softphone {
  private state: SoftphoneState = INITIAL;
  private listeners = new Set<Listener>();
  /** One driver per line. Several may be live at once, subject to each vendor's own rules. */
  private runtimes = new Map<string, LineRuntime>();
  private booting = new Map<string, Promise<void>>();
  private hooks: Omit<BootOptions, "token" | "options" | "account" | "sdk"> = {};
  /** One record per dial the agent asked for, however many times a driver reports progress. */
  private outgoingReported = false;

  // ── subscription ──────────────────────────────────────────────────────────

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState = (): SoftphoneState => this.state;

  private set(patch: Partial<SoftphoneState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l(this.state));
  }

  private setCall(patch: Partial<CallState>) {
    this.set({ call: { ...this.state.call, ...patch } });
  }

  /** Record one line's registration and recompute what the UI reads. */
  private setLine(account: string, patch: Partial<Omit<LineState, "account">>) {
    if (!account) return;
    const previous = this.state.lines[account] ?? {
      account,
      registration: "idle" as RegistrationState,
      error: "",
    };
    const lines = { ...this.state.lines, [account]: { ...previous, ...patch, account } };

    const best = Object.values(lines).reduce<RegistrationState>(
      (winner, line) =>
        REGISTRATION_RANK.indexOf(line.registration) > REGISTRATION_RANK.indexOf(winner)
          ? line.registration
          : winner,
      "idle",
    );
    // Only surface an error while nothing is working. One failed line out of two belongs on that
    // line, not as "the softphone is broken".
    const broken = Object.values(lines).filter((l) => l.error);
    const live = Object.values(lines).find((l) => l.registration === "registered");

    this.set({
      lines,
      registration: best,
      error: best === "registered" ? "" : broken[0]?.error || "",
      activeAccount: this.state.call.account || live?.account || null,
    });
  }

  private driverFor(account?: string | null): SoftphoneDriver | null {
    const name = account ?? this.state.call.account;
    return (name && this.runtimes.get(name)?.driver) || null;
  }

  /** Is this particular line reachable right now? */
  isRegisteredOn(account: string): boolean {
    return this.state.lines[account]?.registration === "registered";
  }

  /** The lines that actually came up. */
  get registeredAccounts(): string[] {
    return Object.values(this.state.lines)
      .filter((l) => l.registration === "registered")
      .map((l) => l.account);
  }

  get isRegistered() {
    return this.state.registration === "registered";
  }

  /**
   * Resolves once this line is registered, or false if it does not get there in time.
   *
   * Bringing a line up is not instant. Dialling into that gap fails with no useful error, so
   * anything that starts a line in order to place a call has to wait here first.
   */
  waitUntilRegistered(account: string, timeoutMs = 12_000): Promise<boolean> {
    if (this.isRegisteredOn(account)) return Promise.resolve(true);
    return new Promise((resolve) => {
      let done = false;
      const finish = (ok: boolean) => {
        if (done) return;
        done = true;
        unsubscribe();
        window.clearTimeout(timer);
        resolve(ok);
      };
      const timer = window.setTimeout(() => finish(false), timeoutMs);
      const unsubscribe = this.subscribe((state) => {
        const line = state.lines[account];
        if (!line) return;
        if (line.registration === "registered") finish(true);
        if (line.registration === "failed" || line.registration === "unsupported") finish(false);
      });
    });
  }

  // ── lifecycle ─────────────────────────────────────────────────────────────

  /**
   * Bring one line up. Safe to call for several lines at once.
   *
   * A vendor that allows only one live client of its own kind has its other lines torn down first
   * — that is the Plivo singleton, and it constrains Plivo alone. A Twilio line is untouched by it,
   * which is how both desks stay registered together.
   */
  async boot({ account, sdk, token, options, ...hooks }: BootOptions): Promise<void> {
    this.hooks = hooks;

    const existing = this.runtimes.get(account);
    if (existing) {
      existing.driver.refresh(token);
      return;
    }
    const inFlight = this.booting.get(account);
    if (inFlight) return inFlight;

    const started = this.startLine(account, sdk, token, options).finally(() => {
      this.booting.delete(account);
    });
    this.booting.set(account, started);
    return started;
  }

  private async startLine(
    account: string,
    sdk: string,
    token: string,
    options: Record<string, unknown>,
  ): Promise<void> {
    this.setLine(account, { registration: "loading", error: "" });

    let driver: SoftphoneDriver;
    try {
      driver = await makeDriver(sdk, {
        options,
        emit: (event) => this.onDriverEvent(account, event),
      });
    } catch (e: any) {
      this.setLine(account, {
        registration: "unsupported",
        error: e?.message || "The calling library could not be loaded.",
      });
      return;
    }

    // A vendor that cannot hold two clients gives up the others before this one starts.
    if (driver.exclusive) this.evictVendor(sdk, account);

    this.runtimes.set(account, { driver, sdk });

    try {
      if (DEBUG) console.info(`[excom softphone] starting ${account} on ${driver.vendor}`, options);
      this.setLine(account, { registration: "registering" });
      await driver.start(token);
      // A registration that never completes leaves the agent thinking they are on the queue.
      window.setTimeout(() => {
        if (this.state.lines[account]?.registration === "registering") {
          this.setLine(account, {
            registration: "failed",
            error:
              "The softphone did not connect within 20 seconds. This is usually a network blocking WebSocket or UDP traffic.",
          });
        }
      }, REGISTRATION_TIMEOUT_MS);
    } catch (e: any) {
      if (DEBUG) console.error(`[excom softphone] ${account} failed to start`, e);
      this.runtimes.delete(account);
      try {
        driver.destroy();
      } catch {
        /* it never started */
      }
      this.setLine(account, {
        registration: "failed",
        error: e?.message || "The softphone could not start.",
      });
    }
  }

  /** Drop every live line using this vendor, except the one about to take its place. */
  private evictVendor(sdk: string, keep: string) {
    for (const [name, runtime] of [...this.runtimes.entries()]) {
      if (name === keep || runtime.sdk !== sdk) continue;
      if (DEBUG) console.info(`[excom softphone] ${name} stands down for ${keep}`);
      try {
        runtime.driver.destroy();
      } catch {
        /* about to be discarded anyway */
      }
      this.runtimes.delete(name);
      this.setLine(name, { registration: "idle", error: "" });
    }
  }

  /** Swap in a fresh token without dropping a call in progress. */
  async relogin(account: string, token: string) {
    this.runtimes.get(account)?.driver.refresh(token);
  }

  /** Drop one line, or every line when no account is named. */
  shutdown(account?: string) {
    const names = account ? [account] : [...this.runtimes.keys()];
    for (const name of names) {
      try {
        this.runtimes.get(name)?.driver.destroy();
      } catch {
        /* nothing useful to do on teardown */
      }
      this.runtimes.delete(name);
    }
    if (account) {
      this.setLine(account, { registration: "idle", error: "" });
      return;
    }
    this.state = INITIAL;
    this.listeners.forEach((l) => l(this.state));
  }

  // ── events ────────────────────────────────────────────────────────────────

  /**
   * One driver's event, named by the line it came from.
   *
   * Registration belongs to that line. The call does not: an agent has one pair of ears however
   * many lines they work, so the call state is shared and carries the account that owns it.
   */
  private onDriverEvent(account: string, event: DriverEvent) {
    if (DEBUG) console.info(`[excom softphone] ${account} ${event.type}`, event);

    switch (event.type) {
      case "registered":
        this.setLine(account, { registration: "registered", error: "" });
        return;
      case "registrationFailed":
        // The SDK's own "your token is about to expire" is not a failure; the coordinator already
        // re-mints on a timer, and reporting it would flap the agent's status light.
        if (event.reason === "token-expiring") return;
        this.setLine(account, { registration: "failed", error: event.reason });
        return;
      case "unregistered":
        this.setLine(account, { registration: "idle" });
        return;
      case "unsupported":
        this.setLine(account, { registration: "unsupported", error: event.reason });
        return;
      case "micDenied":
        this.set({ micDenied: event.denied });
        return;

      case "incoming":
        this.setCall({
          phase: "ringing",
          callUUID: event.callId,
          direction: "Inbound",
          peerNumber: event.from,
          displayName: event.from,
          muted: false,
          startedAt: null,
          account,
        });
        this.hooks.onIncoming?.(event.callId ?? "", event.from, account);
        return;

      case "incomingCancelled":
        this.endLocally();
        return;

      case "outgoing": {
        const uuid = event.callId ?? this.state.call.callUUID;
        this.setCall({ phase: "outgoing", callUUID: uuid, direction: "Outbound", account });
        // Once per dial the agent actually asked for, not once per event.
        //
        // Plivo re-emits while it retries the INVITE, with a fresh id each time. Reporting every
        // one turned a single click into ten "Outgoing call" rows in the timeline within four
        // seconds. What the agent did was place one call; that is what gets recorded, and the
        // webhooks fill in what became of it.
        if (uuid && !this.outgoingReported) {
          this.outgoingReported = true;
          this.hooks.onOutgoing?.(uuid, this.state.call.peerNumber, account);
        }
        return;
      }

      case "connected":
        this.setCall({
          phase: "connected",
          callUUID: event.callId ?? this.state.call.callUUID,
          startedAt: this.state.call.startedAt ?? Date.now(),
        });
        return;

      case "ended":
        if (event.reason) this.set({ error: event.reason });
        this.endLocally();
        return;

      case "quality":
        this.set({ quality: event.metrics });
        this.hooks.onQuality?.(this.state.call.callUUID, event.metrics);
        return;
    }
  }

  private endLocally() {
    const uuid = this.state.call.callUUID;
    this.outgoingReported = false;
    this.set({ call: EMPTY_CALL });
    this.hooks.onEnded?.(uuid);
  }

  // ── actions ───────────────────────────────────────────────────────────────
  //
  // Each reaches for the driver holding the call. With two lines up, the one ringing is whichever
  // the caller dialled, and answering on the other does nothing while the phone keeps ringing.

  answer() {
    this.driverFor()?.answer(this.state.call.callUUID);
    this.setCall({ phase: "connected" });
  }

  reject() {
    this.driverFor()?.reject(this.state.call.callUUID);
    this.endLocally();
  }

  hangup() {
    this.setCall({ phase: "ending" });
    this.driverFor()?.hangup();
  }

  toggleMute(): boolean {
    const next = !this.state.call.muted;
    this.driverFor()?.setMuted(next);
    this.setCall({ muted: next });
    return next;
  }

  sendDigit(tone: string) {
    this.driverFor()?.sendDigit(tone);
  }

  /**
   * Place a call on a named line.
   *
   * The account is not optional in spirit: the server decides which line carries a destination —
   * an Indian number on the Indian line, everything else on the international one — and dialling
   * through the wrong line's driver puts the call out over the wrong provider account, which comes
   * back as a bare "Busy" with nothing to say the number was never the problem.
   */
  call(
    destination: string,
    params: Record<string, string>,
    meta: Partial<CallState> & { account: string },
  ) {
    const driver = this.runtimes.get(meta.account)?.driver;
    if (!driver) throw new Error("That line's softphone is not connected.");
    if (this.state.call.phase !== "none") throw new Error("You are already on a call.");

    this.outgoingReported = false;
    this.setCall({
      phase: "outgoing",
      direction: "Outbound",
      transport: "Browser",
      peerNumber: destination,
      displayName: meta.displayName || destination,
      threadId: meta.threadId ?? null,
      callName: null,
      muted: false,
      startedAt: null,
      account: meta.account,
    });
    driver.call(destination, params);
  }

  /** Bind Excom's own call record to the leg a driver is holding. */
  attachRecord(callName: string, threadId?: string | null) {
    this.setCall({ callName, threadId: threadId ?? this.state.call.threadId });
  }

  setPeer(displayName: string) {
    this.setCall({ displayName });
  }
}

export const softphone = new Softphone();

// A handle an agent can read in the console when the softphone will not connect:
//   __excomSoftphone.lines()   -> every line and its registration
// Read-only, and it exposes nothing the page does not already hold.
if (typeof window !== "undefined") {
  (window as any).__excomSoftphone = {
    state: () => softphone.getState(),
    registered: () => softphone.isRegistered,
    lines: () => softphone.getState().lines,
    registeredOn: (account: string) => softphone.isRegisteredOn(account),
    live: () => softphone.registeredAccounts,
  };
}
