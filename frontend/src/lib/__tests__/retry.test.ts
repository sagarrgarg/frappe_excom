import { describe, it, expect, vi } from "vitest";
import { retryPolicy } from "../retry-policy";

describe("retryPolicy", () => {
  it("429 waits 20s and retries once per tick", () => {
    vi.useFakeTimers(); const revalidate = vi.fn();
    retryPolicy({ httpStatus: 429 }, "k", undefined, revalidate, { retryCount: 0 });
    vi.advanceTimersByTime(19_999); expect(revalidate).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1); expect(revalidate).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
  it("other 4xx never retry; 5xx back off and stop after 3", () => {
    vi.useFakeTimers(); const r = vi.fn();
    retryPolicy({ httpStatus: 403 }, "k", undefined, r, { retryCount: 0 }); vi.advanceTimersByTime(60_000); expect(r).not.toHaveBeenCalled();
    retryPolicy({ httpStatus: 500 }, "k", undefined, r, { retryCount: 0 }); vi.advanceTimersByTime(5_000); expect(r).toHaveBeenCalledTimes(1);
    retryPolicy({ httpStatus: 500 }, "k", undefined, r, { retryCount: 3 }); vi.advanceTimersByTime(60_000); expect(r).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
