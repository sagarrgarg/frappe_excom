import { useFrappeGetCall } from "@/lib/api";

export interface TeamOption { name: string; team_name: string }
export interface AccountOption { name: string; account_name: string; channel: string; email_address?: string; wa_phone_id?: string }
export interface BroadcastOption { name: string; broadcast_name: string; channel: string; status: string; total_recipients: number; sent_count: number; failed_count: number }

/** Reference data behind the `+ Filter` menu. Cached by SWR; one call each per session. */
export function useInboxMeta() {
  const { data: teams } = useFrappeGetCall<{ message: TeamOption[] }>("excom.excom.api.teams.get_my_teams", undefined, undefined, { revalidateOnFocus: false });
  const { data: accounts } = useFrappeGetCall<{ message: AccountOption[] }>("excom.excom.api.chat.get_channel_accounts", undefined, undefined, { revalidateOnFocus: false });
  const { data: broadcasts } = useFrappeGetCall<{ message: BroadcastOption[] }>("excom.excom.api.chat.get_recent_broadcasts_for_filter", undefined, undefined, { revalidateOnFocus: false });
  const { data: merge } = useFrappeGetCall<{ message: { count: number } }>("excom.excom.api.merge_suggestions.get_suggestion_count", undefined, undefined, { refreshInterval: 300_000 });
  return {
    teams: teams?.message ?? [],
    accounts: accounts?.message ?? [],
    broadcasts: broadcasts?.message ?? [],
    mergeCount: merge?.message?.count ?? 0,
  };
}

export function accountLabel(a: AccountOption): string {
  return a.account_name || a.email_address || a.wa_phone_id || a.name;
}
