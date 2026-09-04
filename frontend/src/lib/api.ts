import { useFrappeGetCall as sdkGetCall } from "frappe-react-sdk";
import type { SWRConfiguration } from "swr";

/**
 * useFrappeGetCall that really skips the request when the method is null.
 * The SDK builds its SWR key from the method string, so a null method used to fire GET /api/method/null
 * (417) on every render. A null SWR key is the only thing SWR honours as "don't fetch".
 */
export function useFrappeGetCall<T>(method: string | null | undefined, params?: Record<string, unknown>, swrKey?: string | null, options?: SWRConfiguration) {
  const active = Boolean(method);
  return sdkGetCall<T>(active ? (method as string) : "frappe.ping", active ? params : undefined, active ? swrKey : null, options as never);
}
