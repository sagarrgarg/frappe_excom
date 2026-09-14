import type { DriverEmit, DriverInit, SoftphoneDriver } from "./driver";

/**
 * Plivo's browser SDK, behind the driver interface.
 *
 * The behaviour here is unchanged from when this lived in `lib/softphone.ts`; what moved is only
 * where it lives. Two hard-won details are load-bearing and must not be tidied away:
 *
 * 1. **The SDK is a singleton.** Its constructor ends `window._PlivoInstance = this`, and the
 *    wrapper hands that instance back to every later construction while discarding the new
 *    options. Building a second client therefore does nothing — which is why `exclusive` is true,
 *    and why `destroy()` clears the global.
 * 2. **Its ringtone is taken away.** The SDK appends an `<audio>` pointing at its CDN and plays it
 *    without a user gesture, so the browser rejects it and the call arrives in silence. Excom
 *    generates its own ring instead, and two ringers at once is worse than either.
 */
export class PlivoDriver implements SoftphoneDriver {
  readonly vendor = "plivo";
  /** One client per page, whatever we do. See the note above. */
  readonly exclusive = true;

  private sdk: any = null;
  private client: any = null;
  private readonly options: Record<string, unknown>;
  private readonly emit: DriverEmit;

  constructor({ options, emit }: DriverInit) {
    this.options = options;
    this.emit = emit;
  }

  async start(token: string): Promise<void> {
    if (typeof window === "undefined" || !window.RTCPeerConnection) {
      throw new Error("This browser cannot make calls.");
    }

    let Ctor: any;
    try {
      // Dynamic so the SDK is not in the main bundle: most sessions never open a softphone, and
      // the package is large.
      const mod: any = await import("plivo-browser-sdk");
      Ctor = mod?.default ?? mod?.Plivo ?? (window as any).Plivo;
    } catch {
      Ctor = (window as any).Plivo;
    }
    if (typeof Ctor !== "function") {
      throw new Error("The calling library could not be loaded.");
    }

    this.sdk = new Ctor(this.options);
    this.client = this.sdk.client;
    if (!this.client) throw new Error("The calling library started but exposed no client.");

    this.wire();
    silenceVendorRingtone();
    this.login(token);
  }

  refresh(token: string): void {
    if (!this.client) return;
    try {
      this.login(token);
    } catch {
      /* the SDK re-registers on its own timer; a failed refresh is not fatal mid-call */
    }
  }

  destroy(): void {
    try {
      this.client?.logout?.();
    } catch {
      /* a client that will not log out cleanly is about to be discarded anyway */
    }
    teardownVendorAudio();
    try {
      // Without this the next construction returns this very client, complete with the previous
      // account's credentials — which registers happily and then fails the first INVITE with an
      // authentication error.
      delete (window as any)._PlivoInstance;
    } catch {
      /* if the global cannot be cleared, the next start reuses the client and re-logs in */
    }
    this.sdk = null;
    this.client = null;
  }

  call(destination: string, params: Record<string, string>): void {
    if (!this.client) throw new Error("The softphone is not connected.");
    this.client.call(destination, params);
  }

  answer(callId: string | null): void {
    if (!callId) return;
    this.client?.answer?.(callId);
  }

  reject(callId: string | null): void {
    this.client?.reject?.(callId ?? undefined);
  }

  hangup(): void {
    this.client?.hangup?.();
  }

  setMuted(muted: boolean): void {
    if (muted) this.client?.mute?.();
    else this.client?.unmute?.();
  }

  sendDigit(tone: string): void {
    this.client?.sendDtmf?.(tone);
  }

  // ── wiring ────────────────────────────────────────────────────────────────

  private login(token: string) {
    const c = this.client;
    if (!c) return;
    // The docs name this differently across SDK versions and platforms, so try the token methods in
    // turn. Never log in with the SIP password: the browser is not allowed to hold it.
    const fn =
      typeof c.loginWithAccessToken === "function"
        ? c.loginWithAccessToken
        : typeof c.loginWithJwtToken === "function"
          ? c.loginWithJwtToken
          : null;
    if (!fn) {
      this.emit({
        type: "unsupported",
        reason: "This version of the calling library cannot use a login token.",
      });
      return;
    }
    fn.call(c, token);
  }

  private on(event: string, handler: (...args: any[]) => void) {
    try {
      this.client?.on?.(event, handler);
    } catch {
      /* unknown events differ between SDK versions; an unwired one is not fatal */
    }
  }

  private wire() {
    this.on("onLogin", () => this.emit({ type: "registered" }));
    this.on("onLoginFailed", (reason: any) =>
      this.emit({
        type: "registrationFailed",
        reason: typeof reason === "string" ? reason : "The softphone could not sign in.",
      }),
    );
    this.on("onLogout", () => this.emit({ type: "unregistered" }));
    this.on("onWebrtcNotSupported", () =>
      this.emit({
        type: "unsupported",
        reason: "Calling in the browser is not available on this network or browser.",
      }),
    );

    this.on("onMediaPermission", (result: any) =>
      this.emit({ type: "micDenied", denied: Boolean(result?.status === "failure" || result?.error) }),
    );

    this.on("onIncomingCall", (...args: any[]) => {
      // Again here, not only at start: the SDK builds its audio elements lazily, so the call at
      // start-up may have found nothing to silence.
      silenceVendorRingtone();
      const info = pickCallInfo(args);
      this.emit({ type: "incoming", callId: info.callUUID, from: info.from });
    });
    this.on("onIncomingCallCanceled", () => this.emit({ type: "incomingCancelled" }));

    this.on("onCalling", (...args: any[]) =>
      this.emit({ type: "outgoing", callId: pickCallInfo(args).callUUID }),
    );

    const connected = (...args: any[]) =>
      this.emit({ type: "connected", callId: pickCallInfo(args).callUUID });
    this.on("onCallAnswered", connected);
    this.on("onCallConnected", connected);

    this.on("onCallTerminated", () => this.emit({ type: "ended" }));
    this.on("onCallFailed", (reason: any) =>
      this.emit({ type: "ended", reason: typeof reason === "string" ? reason : "" }),
    );

    this.on("mediaMetrics", (metrics: any) => {
      if (metrics && typeof metrics === "object") this.emit({ type: "quality", metrics });
    });
  }
}

/**
 * Remove the audio elements the SDK appends to the body.
 *
 * Its setup appends `<audio>` tags with fixed ids — ringtone, ringback, connect tone, the remote
 * stream — and a second client appends another set. Duplicate ids mean `getElementById` picks
 * whichever came first, so the tone we silenced is not the one that plays.
 */
function teardownVendorAudio(): void {
  try {
    for (const el of document.querySelectorAll("audio[data-devicetype]")) el.remove();
  } catch {
    /* a stale element is noisy, never fatal */
  }
}

/**
 * Take the incoming ring away from the SDK.
 *
 * It plays a CDN-hosted tone with no prior user gesture, which the browser rejects — so the call
 * arrives silent and the rejection is logged at debug level. Excom rings from `lib/ringtone.ts`
 * instead. Dropping the source leaves the element in place, so the SDK's own lookup still finds
 * something and its `play()` resolves to nothing.
 */
function silenceVendorRingtone(): void {
  try {
    const el = document.getElementById("plivo_ringtone") as HTMLAudioElement | null;
    if (!el || !el.getAttribute("src")) return;
    el.pause();
    el.removeAttribute("src");
    el.load();
  } catch {
    /* worst case the vendor tone plays too — noisy, never broken */
  }
}

/**
 * Extract a call id and a peer number from an SDK callback, whose argument shape varies by version.
 * Reading positionally would break on an upgrade; reading by name does not.
 */
function pickCallInfo(args: any[]): { callUUID: string | null; from: string } {
  let callUUID: string | null = null;
  let from = "";
  for (const arg of args) {
    if (typeof arg === "string" && !from) from = arg;
    if (arg && typeof arg === "object") {
      callUUID = callUUID ?? arg.callUUID ?? arg.callUuid ?? arg.callId ?? null;
      from = from || arg.src || arg.from || arg.callerName || "";
    }
  }
  return { callUUID, from };
}
