/**
 * Small WebRTC helpers for WhatsApp calls, kept free of the DOM so they can be unit tested.
 *
 * Meta's side is ICE-lite with no trickle ICE and no renegotiation, so the browser must hand over
 * one complete session description: every candidate gathered, CRLF line endings, one audio stream.
 */

export interface IceServerLike {
  urls: string | string[];
  username?: string;
  credential?: string;
}

export const DEFAULT_ICE_SERVERS: IceServerLike[] = [{ urls: ["stun:stun.l.google.com:19302"] }];
const SCHEMES = ["stun:", "turn:", "turns:"];

/** Keep only well-formed stun:/turn:/turns: servers; fall back to a public STUN server. */
export function normalizeIceServers(raw: unknown): IceServerLike[] {
  const list = Array.isArray(raw) ? raw : raw && typeof raw === "object" ? [raw] : [];
  const out: IceServerLike[] = [];
  for (const entry of list) {
    if (!entry || typeof entry !== "object") continue;
    const e = entry as Record<string, unknown>;
    const given = e.urls ?? e.url;
    const urls = (Array.isArray(given) ? given : [given])
      .map((u) => String(u ?? "").trim())
      .filter((u) => SCHEMES.some((s) => u.toLowerCase().startsWith(s)));
    if (!urls.length) continue;
    const server: IceServerLike = { urls };
    if (typeof e.username === "string" && e.username) server.username = e.username;
    if (typeof e.credential === "string" && e.credential) server.credential = e.credential;
    out.push(server);
  }
  return out.length ? out : DEFAULT_ICE_SERVERS;
}

/** The parts of an RTCPeerConnection the gathering wait needs. */
export interface GatheringTarget {
  iceGatheringState: string;
  addEventListener(type: string, listener: () => void): void;
  removeEventListener(type: string, listener: () => void): void;
}

/**
 * Resolve once ICE gathering is complete, or after `timeoutMs` with whatever was gathered.
 *
 * A TURN server that never answers would otherwise hold the call until Meta gives up (30–60 s);
 * a few seconds of host and server-reflexive candidates is a better offer than none.
 */
export function waitForIceGathering(pc: GatheringTarget, timeoutMs = 4000): Promise<"complete" | "timeout"> {
  if (pc.iceGatheringState === "complete") return Promise.resolve("complete");
  return new Promise((resolve) => {
    let done = false;
    const finish = (how: "complete" | "timeout") => {
      if (done) return;
      done = true;
      pc.removeEventListener("icegatheringstatechange", onChange);
      clearTimeout(timer);
      resolve(how);
    };
    const onChange = () => {
      if (pc.iceGatheringState === "complete") finish("complete");
    };
    const timer = setTimeout(() => finish("timeout"), timeoutMs);
    pc.addEventListener("icegatheringstatechange", onChange);
  });
}

/** RFC 8866 line endings, which Meta requires. */
export function toCrlf(sdp: string): string {
  const lines = String(sdp || "").replace(/\r\n/g, "\n").split("\n");
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines.length ? lines.join("\r\n") + "\r\n" : "";
}

export function candidateCount(sdp: string): number {
  return (String(sdp || "").match(/^a=candidate:/gm) || []).length;
}

/** One audio m-line and nothing else: what Meta accepts. */
export function isSingleAudio(sdp: string): boolean {
  const media = String(sdp || "").match(/^m=\w+/gm) || [];
  return media.length === 1 && media[0] === "m=audio";
}

/** A session description ready for Meta, or an error the agent can read. */
export function finalSdp(sdp: string | undefined | null): string {
  const text = toCrlf(sdp || "");
  if (!text || !isSingleAudio(text)) throw new Error("The browser could not prepare an audio-only call.");
  if (candidateCount(text) === 0) {
    throw new Error("No network path was found for the call. A TURN server may be needed on this network.");
  }
  return text;
}
