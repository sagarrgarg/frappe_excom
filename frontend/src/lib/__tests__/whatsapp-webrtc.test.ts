import { describe, it, expect, vi } from "vitest";
import {
  DEFAULT_ICE_SERVERS,
  candidateCount,
  finalSdp,
  isSingleAudio,
  normalizeIceServers,
  toCrlf,
  waitForIceGathering,
  type GatheringTarget,
} from "../voice/webrtc";

/** Enough of an RTCPeerConnection to drive the gathering wait. */
function fakePeer(state = "gathering") {
  const listeners = new Set<() => void>();
  const pc: GatheringTarget & { finish(): void; listeners: Set<() => void> } = {
    iceGatheringState: state,
    addEventListener: (_t, l) => listeners.add(l),
    removeEventListener: (_t, l) => listeners.delete(l),
    finish() {
      this.iceGatheringState = "complete";
      listeners.forEach((l) => l());
    },
    listeners,
  };
  return pc;
}

const OFFER = [
  "v=0",
  "o=- 1 2 IN IP4 127.0.0.1",
  "s=-",
  "t=0 0",
  "m=audio 9 UDP/TLS/RTP/SAVPF 111 126",
  "a=rtpmap:111 opus/48000/2",
  "a=candidate:1 1 udp 2122260223 192.0.2.10 54400 typ host",
  "a=candidate:2 1 udp 1686052607 198.51.100.7 54400 typ srflx raddr 192.0.2.10 rport 54400",
  "",
].join("\n");

describe("ICE gathering (no trickle: the whole SDP goes to Meta at once)", () => {
  it("resolves at once when gathering is already complete", async () => {
    await expect(waitForIceGathering(fakePeer("complete"))).resolves.toBe("complete");
  });

  it("waits for the complete state and removes its listener", async () => {
    const pc = fakePeer();
    const done = waitForIceGathering(pc, 5000);
    expect(pc.listeners.size).toBe(1);
    pc.finish();
    await expect(done).resolves.toBe("complete");
    expect(pc.listeners.size).toBe(0);
  });

  it("gives up after the timeout with what it has", async () => {
    vi.useFakeTimers();
    const pc = fakePeer();
    const done = waitForIceGathering(pc, 4000);
    vi.advanceTimersByTime(4000);
    await expect(done).resolves.toBe("timeout");
    expect(pc.listeners.size).toBe(0);
    vi.useRealTimers();
  });
});

describe("SDP checks", () => {
  it("uses CRLF line endings with one final CRLF", () => {
    const out = toCrlf(OFFER);
    expect(out.includes("\n") && !/[^\r]\n/.test(out)).toBe(true);
    expect(out.endsWith("\r\n")).toBe(true);
    expect(out.endsWith("\r\n\r\n")).toBe(false);
  });

  it("counts candidates and accepts a single audio stream", () => {
    expect(candidateCount(OFFER)).toBe(2);
    expect(isSingleAudio(OFFER)).toBe(true);
    expect(isSingleAudio(OFFER + "m=video 9 UDP/TLS/RTP/SAVPF 96\n")).toBe(false);
  });

  it("refuses a description without candidates (nothing Meta could reach)", () => {
    const bare = OFFER.split("\n").filter((l) => !l.startsWith("a=candidate")).join("\n");
    expect(() => finalSdp(bare)).toThrow(/TURN/);
    expect(() => finalSdp("")).toThrow();
    expect(finalSdp(OFFER).startsWith("v=0\r\n")).toBe(true);
  });
});

describe("ICE servers from the backend descriptor", () => {
  it("keeps stun/turn entries and their credentials", () => {
    const out = normalizeIceServers([
      { urls: "stun:stun.example.com:3478" },
      { urls: ["turn:turn.example.com:3478", "https://not-ice"], username: "u", credential: "p" },
      { urls: "http://bad" },
      "junk",
    ]);
    expect(out).toEqual([
      { urls: ["stun:stun.example.com:3478"] },
      { urls: ["turn:turn.example.com:3478"], username: "u", credential: "p" },
    ]);
  });

  it("falls back to public STUN", () => {
    expect(normalizeIceServers(undefined)).toEqual(DEFAULT_ICE_SERVERS);
    expect(normalizeIceServers([{ urls: "ftp://x" }])).toEqual(DEFAULT_ICE_SERVERS);
  });
});
