import { useMemo } from "react";
import { useFrappeGetCall } from "frappe-react-sdk";
import type { ExcomThread, UnifiedContact, Account, Message } from "../types";
import { parseFrappeDateTime } from "../utils/datetime";

/**
 * Fetches threads from the Frappe backend and transforms them into UnifiedContact
 * objects by grouping threads that share the same omni_identity.
 */
export function useThreads(
  search: string = "",
  team: string = "",
  broadcast: string = "",
  broadcastStatus: string = "",
  channel: string = "",
  account: string = "",
  dateFrom: string = "",
  dateTo: string = "",
  archived: boolean = false,
) {
  const params: Record<string, string | number> = { search, limit: 100, team };
  if (archived) params.archived = 1;
  if (broadcast) params.broadcast = broadcast;
  if (broadcastStatus) params.broadcast_status = broadcastStatus;
  if (channel && channel !== "all") params.channel = channel;
  if (account) params.account = account;
  if (dateFrom) params.date_from = dateFrom;
  if (dateTo) params.date_to = dateTo;

  const { data, error, isLoading, mutate } = useFrappeGetCall<{
    message: ExcomThread[];
  }>("excom.excom.api.chat.get_threads", params);

  const threads = data?.message ?? [];

  const unifiedContacts: UnifiedContact[] = useMemo(() => {
    const groupedByIdentity = new Map<string, ExcomThread[]>();

    for (const thread of threads) {
      const key = thread.omni_identity || thread.name;
      if (!groupedByIdentity.has(key)) {
        groupedByIdentity.set(key, []);
      }
      groupedByIdentity.get(key)!.push(thread);
    }

    return Array.from(groupedByIdentity.entries()).map(
      ([identityId, identityThreads]) => {
        const sortedThreads = [...identityThreads].sort(
          (a, b) =>
            parseFrappeDateTime(b.last_message_at).getTime() -
            parseFrappeDateTime(a.last_message_at).getTime()
        );

        const latestThread = sortedThreads[0];
        const channels = [...new Set(sortedThreads.map((t) => t.channel))];
        const totalUnread = sortedThreads.reduce(
          (sum, t) => sum + (t.unread_count || 0),
          0
        );

        const allAccounts: Account[] = sortedThreads.map((t) => ({
          id: t.name,
          name: t.account || t.channel,
          identifier: t.primary_phone || t.display_name,
          channel: t.channel,
          isActive: true,
          hasAccess: true,
        }));

        const allTags = sortedThreads.flatMap((t) => t.tags || []);
        const uniqueTags = allTags.filter(
          (tag, idx, arr) => arr.findIndex((t) => t.tag === tag.tag) === idx
        );

        const contact: UnifiedContact = {
          id: identityId,
          contactName: latestThread.display_name,
          contactAvatar: "",
          contactInfo: {
            email: latestThread.primary_email || "",
            phone: latestThread.primary_phone || "",
            company: latestThread.company || "",
            erpEntity: undefined,
          },
          status: "offline",
          lastMessage: latestThread.last_message_preview || "",
          timestamp: parseFrappeDateTime(latestThread.last_message_at),
          totalUnreadCount: totalUnread,
          aiStatus: undefined,
          assignedTo: latestThread.assigned_to
            ? {
                name: latestThread.assigned_to_name || latestThread.assigned_to,
                avatar: latestThread.assigned_to_avatar || "",
              }
            : undefined,
          allAccounts,
          activeAccountId: allAccounts[0]?.id || "",
          allMessages: [],
          channels,
          tags: uniqueTags.length > 0 ? uniqueTags : undefined,
          broadcastDeliveryStatus: latestThread.broadcast_delivery_status || undefined,
          assignedToUser: latestThread.assigned_to || undefined,
          assignedTeam: latestThread.assigned_team || undefined,
          assignedTeamName: latestThread.assigned_team_name || undefined,
          lastMessageDirection: latestThread.last_message_direction,
          threads: sortedThreads,
        };

        return contact;
      }
    );
  }, [threads]);

  return {
    threads,
    unifiedContacts,
    error,
    isLoading,
    refresh: mutate,
  };
}
