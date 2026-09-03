/**
 * Retry policy for every SWR fetch: a 429 waits 20s (retrying sooner just extends the block),
 * other 4xx never retry, 5xx/network back off 5s → 10s → 20s and stop after three tries.
 */
export function retryPolicy(err: { httpStatus?: number } | undefined, _key: string, _cfg: unknown, revalidate: (o: { retryCount: number }) => void, { retryCount }: { retryCount: number }) {
  const st = err?.httpStatus;
  if (st === 429) { setTimeout(() => revalidate({ retryCount }), 20_000); return; }
  if (st && st >= 400 && st < 500) return;
  if (retryCount >= 3) return;
  setTimeout(() => revalidate({ retryCount }), 5_000 * 2 ** retryCount);
}
