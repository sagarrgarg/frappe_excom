/**
 * The slice of the Plivo Browser SDK that Excom actually uses.
 *
 * The package ships an `index.d.ts`, but declaring our own surface here does two useful things:
 * a typecheck passes before `yarn install` has run, and this file is the written record of exactly
 * what `lib/softphone.ts` depends on — so an SDK upgrade that moves something shows up as a type
 * error rather than as calls that silently stop ringing.
 *
 * Everything is optional because the SDK's method names differ across versions (the token login in
 * particular), and `softphone.ts` probes for what exists rather than assuming.
 */
declare module "plivo-browser-sdk" {
  export interface PlivoClient {
    /** Token login. Named differently across SDK versions; softphone.ts tries both. */
    loginWithAccessToken?(token: string): void;
    loginWithJwtToken?(token: string): void;
    /** Username/password login. Deliberately unused — the browser never holds a SIP password. */
    login?(username: string, password: string): void;
    logout?(): void;

    call(destination: string, extraHeaders?: Record<string, string>): void;
    answer?(callUUID: string): void;
    reject?(callUUID?: string): void;
    hangup?(): void;
    mute?(): void;
    unmute?(): void;
    sendDtmf?(tone: string): void;

    on?(event: string, handler: (...args: any[]) => void): void;
  }

  export interface PlivoSdk {
    client: PlivoClient;
  }

  export interface PlivoOptions {
    debug?: string;
    permOnClick?: boolean;
    enableTracking?: boolean;
    closeProtection?: boolean;
    clientRegion?: string;
    enableNoiseReduction?: boolean;
    allowMultipleIncomingCalls?: boolean;
    registrationRefreshTimer?: number;
    [key: string]: unknown;
  }

  const Plivo: new (options?: PlivoOptions) => PlivoSdk;
  export default Plivo;
  export { Plivo };
}
