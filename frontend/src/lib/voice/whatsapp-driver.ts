import type { DriverEmit, DriverInit, DriverSignal, SoftphoneDriver } from "./driver";
import { errorSummary } from "../errors";
import { finalSdp, normalizeIceServers, waitForIceGathering } from "./webrtc";

/**
 * WhatsApp Business Calling, behind the driver interface.
 *
 * There is no vendor SDK. The browser speaks WebRTC straight to Meta's relay and the session
 * descriptions travel through Excom: Meta's offer arrives by realtime (`signal({type: "offer"})`),
 * our answer goes back over `api.whatsapp_calling.answer`, which sends `pre_accept` and `accept`.
 * For a call the agent places, the offer goes out over `start_call` and Meta's answer comes back
 * by `signal({type: "answer"})`.
 *
 * Meta is ICE-lite, takes no trickle ICE and never renegotiates, and supplies no STUN/TURN. So
 * every description is sent whole (gathering complete), and the ICE servers come from Excom.
 *
 * `call(destination, params)` takes the conversation in `params.thread`; `destination` is only the
 * name shown. Registration means "this browser can do WebRTC"; the heartbeat that puts the agent in
 * the ring set is the coordinator's, as for every line.
 */

const API = "excom.excom.api.whatsapp_calling";

interface Pending {
  callId: string;
  call: string;
  sdp: string;
  from: string;
}

interface Current {
  callId: string;
  call: string;
  direction: "in" | "out";
  connected: boolean;
}

async function post<T>(method: string, args: Record<string, unknown>): Promise<T> {
  const res = await fetch(`/api/method/${API}.${method}`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Frappe-CSRF-Token": (window as any).csrf_token || "",
    },
    body: JSON.stringify(args),
  });
  const body: any = await res.json().catch(() => ({}));
  if (!res.ok) throw { httpStatus: res.status, ...body };
  return body?.message as T;
}

function reasonOf(e: unknown, fallback: string): string {
  if (e instanceof Error) return e.message || fallback;
  return errorSummary(e as any, fallback);
}

export class WhatsAppDriver implements SoftphoneDriver {
  readonly vendor = "whatsapp";
  readonly exclusive = false;

  private readonly emit: DriverEmit;
  private readonly iceServers: RTCIceServer[];
  private pc: RTCPeerConnection | null = null;
  private stream: MediaStream | null = null;
  private audio: HTMLAudioElement | null = null;
  private pending: Pending | null = null;
  private current: Current | null = null;
  private muted = false;

  constructor({ options, emit }: DriverInit) {
    this.emit = emit;
    this.iceServers = normalizeIceServers(options?.iceServers) as RTCIceServer[];
  }

  async start(): Promise<void> {
    if (typeof window === "undefined" || !window.RTCPeerConnection || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser cannot take WhatsApp calls.");
    }
    this.emit({ type: "registered" });
  }

  refresh(): void {
    /* nothing to refresh: the Frappe session is the credential */
  }

  destroy(): void {
    this.teardown();
    this.pending = null;
  }

  // ── server signalling ───────────────────────────────────────────────────

  signal(message: DriverSignal): void {
    const id = message.provider_call_id || "";
    switch (message.type) {
      case "offer":
        // Already on a call: stay silent; the call rings the agent's colleagues.
        if (this.current || !message.sdp || !message.call) return;
        this.pending = { callId: id, call: message.call, sdp: message.sdp, from: message.from || "" };
        this.emit({ type: "incoming", callId: id, from: message.from || "" });
        return;
      case "cancel":
        if (this.pending && this.pending.callId === id) {
          this.pending = null;
          this.emit({ type: "incomingCancelled" });
        }
        return;
      case "answer":
        if (this.current?.direction === "out" && message.sdp && (!this.current.callId || this.current.callId === id)) {
          void this.applyAnswer(message.sdp);
        }
        return;
      case "status":
        if (!this.current || this.current.callId !== id) return;
        if (message.status === "ACCEPTED") this.markConnected();
        if (message.status === "REJECTED") this.finish("They declined the call.");
        return;
      case "terminate":
        if (this.pending && this.pending.callId === id) {
          this.pending = null;
          this.emit({ type: "incomingCancelled" });
          return;
        }
        if (this.current && this.current.callId === id) this.finish(message.reason || undefined);
        return;
    }
  }

  // ── calls ───────────────────────────────────────────────────────────────

  call(_destination: string, params: Record<string, string>): void {
    void this.placeCall(params.thread || "");
  }

  answer(): void {
    void this.acceptCall();
  }

  reject(): void {
    const p = this.pending;
    this.pending = null;
    if (p) post("decline", { call: p.call }).catch(() => {
      /* the call rings out on its own */
    });
  }

  hangup(): void {
    const cur = this.current;
    this.teardown();
    if (cur?.callId || cur?.call) {
      post("hangup", { call: cur.call, provider_call_id: cur.callId }).catch(() => {
        /* the person may have hung up first; the terminate webhook settles it */
      });
    }
    this.emit({ type: "ended" });
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.current?.direction === "out" || this.current?.connected) this.setSending(!muted);
  }

  sendDigit(tone: string): void {
    try {
      this.pc?.getSenders().find((s) => s.track?.kind === "audio")?.dtmf?.insertDTMF(tone);
    } catch {
      /* DTMF is in-band only (RFC 4733); a peer that did not negotiate it ignores the tone */
    }
  }

  // ── internals ───────────────────────────────────────────────────────────

  private async placeCall(thread: string) {
    if (!thread) {
      this.emit({ type: "ended", reason: "Open the WhatsApp conversation to call this person." });
      return;
    }
    this.current = { callId: "", call: "", direction: "out", connected: false };
    try {
      const pc = await this.openPeer(false);
      await pc.setLocalDescription(await pc.createOffer());
      await waitForIceGathering(pc);
      const sdp = finalSdp(pc.localDescription?.sdp);
      const res = await post<{ call: string; provider_call_id: string; sdp_answer?: string }>("start_call", { thread, sdp });
      if (!this.current) {
        // The agent hung up while WhatsApp was placing the call.
        post("hangup", { call: res.call, provider_call_id: res.provider_call_id }).catch(() => {});
        return;
      }
      this.current.callId = res.provider_call_id;
      this.current.call = res.call;
      this.emit({ type: "outgoing", callId: res.provider_call_id });
      if (res.sdp_answer) await this.applyAnswer(res.sdp_answer);
    } catch (e) {
      this.finish(reasonOf(e, "The WhatsApp call could not be placed."));
    }
  }

  private async acceptCall() {
    const p = this.pending;
    if (!p) return;
    this.pending = null;
    this.current = { callId: p.callId, call: p.call, direction: "in", connected: false };
    try {
      // The microphone stays silent until Meta confirms the accept, or the caller hears a clipped start.
      const pc = await this.openPeer(true, p.sdp);
      await pc.setLocalDescription(await pc.createAnswer());
      await waitForIceGathering(pc);
      const sdp = finalSdp(pc.localDescription?.sdp);
      await post("answer", { call: p.call, sdp });
      if (!this.current) return;
      this.current.connected = true;
      this.setSending(!this.muted);
      this.emit({ type: "connected", callId: p.callId });
    } catch (e) {
      this.finish(reasonOf(e, "The WhatsApp call could not be answered."));
    }
  }

  private async applyAnswer(sdp: string) {
    const pc = this.pc;
    if (!pc || pc.remoteDescription) return;
    try {
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch (e) {
      this.finish(reasonOf(e, "WhatsApp's answer could not be applied."));
    }
  }

  /** A peer with the microphone on it, and (incoming) Meta's offer applied. */
  private async openPeer(silent: boolean, remoteOffer?: string): Promise<RTCPeerConnection> {
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
      this.emit({ type: "micDenied", denied: false });
    } catch (e: any) {
      if (e?.name === "NotAllowedError") this.emit({ type: "micDenied", denied: true });
      throw new Error("The microphone is not available. Allow it in the browser address bar.");
    }
    this.stream = stream;

    const pc = new RTCPeerConnection({ iceServers: this.iceServers, bundlePolicy: "max-bundle", rtcpMuxPolicy: "require" });
    this.pc = pc;
    pc.ontrack = (ev) => this.play(ev.streams?.[0] ?? new MediaStream([ev.track]));
    pc.onconnectionstatechange = () => {
      if (pc !== this.pc) return;
      if (pc.connectionState === "connected" && this.current?.direction === "out") this.markConnected();
      if (pc.connectionState === "failed") {
        this.finish("The call's audio could not connect. A TURN server may be needed on this network.");
      }
    };

    if (remoteOffer) await pc.setRemoteDescription({ type: "offer", sdp: remoteOffer });
    const track = stream.getAudioTracks()[0];
    if (!track) throw new Error("No microphone was found.");
    track.enabled = !silent;
    // One audio track, one stream: all Meta accepts.
    pc.addTrack(track, stream);
    return pc;
  }

  private markConnected() {
    if (!this.current || this.current.connected) return;
    this.current.connected = true;
    this.setSending(!this.muted);
    this.emit({ type: "connected", callId: this.current.callId || null });
  }

  private setSending(on: boolean) {
    this.stream?.getAudioTracks().forEach((t) => (t.enabled = on));
  }

  private play(stream: MediaStream) {
    try {
      if (!this.audio) {
        this.audio = new Audio();
        this.audio.autoplay = true;
      }
      this.audio.srcObject = stream;
      void this.audio.play().catch(() => {
        /* autoplay is allowed after the click that answered or placed the call */
      });
    } catch {
      /* no audio element in this environment */
    }
  }

  private finish(reason?: string) {
    const had = Boolean(this.current);
    this.teardown();
    if (had) this.emit({ type: "ended", reason });
  }

  private teardown() {
    this.current = null;
    try {
      this.pc?.close();
    } catch {
      /* already closed */
    }
    this.pc = null;
    this.stream?.getTracks().forEach((t) => {
      try {
        t.stop();
      } catch {
        /* already stopped */
      }
    });
    this.stream = null;
    if (this.audio) {
      try {
        this.audio.pause();
        this.audio.srcObject = null;
      } catch {
        /* nothing playing */
      }
    }
  }
}
