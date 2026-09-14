/**
 * The ringer: the sound that tells an agent a call is theirs, right now.
 *
 * The SDK ships one of these already — an `<audio loop>` pointing at
 * `cdn.plivo.com/sdk/browser/audio/us-ring.mp3` — and it is the wrong thing to rely on twice over.
 * It needs the CDN to be reachable at the moment the phone rings, which is the one moment a
 * network hiccup must not matter; and it calls `play()` without ever having had a user gesture, so
 * on a tab the agent has not clicked, Chrome rejects it and the call arrives in total silence. The
 * SDK logs that rejection at debug level and carries on. Nobody hears the phone.
 *
 * So the ring is generated here instead, from two oscillators. No asset, no request, no CSP
 * question, and it sounds identical on every machine. The only thing we cannot route around is the
 * autoplay policy itself — so `arm()` exists, it is called from real user gestures, and `start()`
 * reports honestly when it could not make a sound rather than failing quietly the way the SDK does.
 *
 * The cadence is the familiar double ring: two bursts, then a long pause, repeating. It is read as
 * "a phone is ringing" without anyone having to learn it.
 */

/** 440 Hz and 480 Hz together are the classic bell pair — the beat between them is the warble. */
const TONES = [440, 480];

const BURST_SECONDS = 0.4;
const GAP_SECONDS = 0.2;
/** Burst, gap, burst, then the rest of the period is silence. */
const PERIOD_SECONDS = 3;
const PEAK = 0.22;

/**
 * Schedule this far ahead, and top up on a timer. A background tab clamps `setInterval` to about a
 * second, but `AudioContext.currentTime` keeps its own clock — so scheduling well ahead of the
 * timer is what keeps the ring steady while the agent is reading mail in another tab.
 */
const LOOKAHEAD_SECONDS = 4;
const TOP_UP_MS = 1500;

const MUTE_KEY = "excom_ringtone";

type Ctor = typeof AudioContext;

class Ringer {
  private ctx: AudioContext | null = null;
  private master: GainNode | null = null;
  private scheduled: OscillatorNode[] = [];
  private topUp = 0;
  private nextBurstAt = 0;
  private ringing = false;

  /** True once the browser has actually let us make sound. */
  get ready(): boolean {
    return Boolean(this.ctx && this.ctx.state === "running");
  }

  get muted(): boolean {
    try {
      return localStorage.getItem(MUTE_KEY) === "off";
    } catch {
      // A browser with site data blocked still gets a ringtone; it just cannot remember a choice.
      return false;
    }
  }

  setMuted(muted: boolean): void {
    try {
      localStorage.setItem(MUTE_KEY, muted ? "off" : "on");
    } catch {
      /* the preference is a convenience, not a requirement */
    }
    if (muted) this.stop();
    else this.arm();
  }

  /**
   * Prepare the audio context. Safe to call as often as you like, and it must be called from
   * inside a user gesture at least once — that is the whole reason it is a separate method from
   * `start()`. A click on "Taking calls" is the natural one: the agent is declaring they want to
   * be rung, which is exactly when we need permission to ring.
   */
  arm(): void {
    if (typeof window === "undefined") return;
    const Ctor = (window.AudioContext ?? (window as unknown as { webkitAudioContext?: Ctor }).webkitAudioContext) as
      | Ctor
      | undefined;
    if (!Ctor) return;
    try {
      if (!this.ctx) {
        this.ctx = new Ctor();
        this.master = this.ctx.createGain();
        this.master.gain.value = 0;
        this.master.connect(this.ctx.destination);
      }
      if (this.ctx.state === "suspended") {
        void this.ctx.resume().catch(() => {
          /* no gesture yet — `ready` stays false and `start` will say so */
        });
      }
    } catch {
      /* no Web Audio here; the toast and the flashing title still land */
    }
  }

  /**
   * Start ringing. Returns false only when the browser refused to make a sound, so the caller can
   * say so — a silent ringer that reports success is the bug this whole module exists to fix.
   * A deliberately muted ringer returns true: that is a choice, not a failure.
   */
  start(): boolean {
    if (this.muted) return true;
    this.arm();
    const ctx = this.ctx;
    if (!ctx || !this.master || ctx.state !== "running") return false;
    if (this.ringing) return true;

    this.ringing = true;
    this.master.gain.cancelScheduledValues(ctx.currentTime);
    this.master.gain.setValueAtTime(1, ctx.currentTime);
    // A beat of lead-in, so the first burst is not clipped by the scheduler itself.
    this.nextBurstAt = ctx.currentTime + 0.06;
    this.schedule();
    this.topUp = window.setInterval(() => this.schedule(), TOP_UP_MS);
    return true;
  }

  stop(): void {
    if (this.topUp) {
      window.clearInterval(this.topUp);
      this.topUp = 0;
    }
    this.ringing = false;

    const ctx = this.ctx;
    if (ctx && this.master) {
      this.master.gain.cancelScheduledValues(ctx.currentTime);
      this.master.gain.setValueAtTime(0, ctx.currentTime);
    }
    // Cutting the master gain silences it; stopping the nodes is what stops them being scheduled
    // work. Answering a call must not leave four seconds of ring queued behind it.
    for (const osc of this.scheduled.splice(0)) {
      try {
        osc.stop();
      } catch {
        /* already finished */
      }
      try {
        osc.disconnect();
      } catch {
        /* already detached */
      }
    }
  }

  private schedule(): void {
    const ctx = this.ctx;
    if (!ctx || !this.ringing) return;
    const horizon = ctx.currentTime + LOOKAHEAD_SECONDS;
    while (this.nextBurstAt < horizon) {
      this.burst(this.nextBurstAt);
      this.burst(this.nextBurstAt + BURST_SECONDS + GAP_SECONDS);
      this.nextBurstAt += PERIOD_SECONDS;
    }
  }

  /** One burst of the double ring, enveloped so it fades rather than clicks. */
  private burst(at: number): void {
    const ctx = this.ctx;
    const master = this.master;
    if (!ctx || !master || at < ctx.currentTime) return;

    const envelope = ctx.createGain();
    envelope.gain.setValueAtTime(0, at);
    envelope.gain.linearRampToValueAtTime(PEAK, at + 0.02);
    envelope.gain.setValueAtTime(PEAK, at + BURST_SECONDS - 0.05);
    envelope.gain.linearRampToValueAtTime(0, at + BURST_SECONDS);
    envelope.connect(master);

    const oscillators = TONES.map((hz) => {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(hz, at);
      osc.connect(envelope);
      osc.start(at);
      osc.stop(at + BURST_SECONDS + 0.02);
      this.scheduled.push(osc);
      return osc;
    });

    const last = oscillators[oscillators.length - 1];
    if (last) {
      last.onended = () => {
        try {
          envelope.disconnect();
        } catch {
          /* already detached */
        }
        for (const osc of oscillators) {
          const i = this.scheduled.indexOf(osc);
          if (i >= 0) this.scheduled.splice(i, 1);
        }
      };
    }
  }
}

export const ringtone = new Ringer();

/**
 * Arm on the first real interaction anywhere in the app, whatever it was.
 *
 * The explicit `arm()` on the availability toggle is the one we rely on, but an agent who was
 * already available when the page reloaded never touches it — and they are precisely the person
 * who is about to be rung. Any click will do, and once the context is running these come off.
 */
if (typeof window !== "undefined") {
  const GESTURES = ["pointerdown", "keydown", "touchstart"] as const;
  const onGesture = () => {
    ringtone.arm();
    if (!ringtone.ready) return;
    for (const event of GESTURES) window.removeEventListener(event, onGesture, true);
  };
  for (const event of GESTURES) {
    window.addEventListener(event, onGesture, { capture: true, passive: true });
  }
}
