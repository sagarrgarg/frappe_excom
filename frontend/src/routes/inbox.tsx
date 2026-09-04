import { MessageSquare, PanelLeftOpen } from "lucide-react";
import { ThreadList } from "../components/inbox/ThreadList";
import { RecordPane } from "../components/record/RecordPane";
import { DetailsDrawer } from "../components/record/DetailsDrawer";
import { EmptyState, Button } from "../components/primitives";
import { useInbox } from "../components/shell/InboxProvider";
import { MOD } from "../lib/hotkeys";
import { cn } from "../components/ui/utils";

/**
 * Inbox + record in one route element so the list keeps its scroll/filter state when a record opens.
 *  phone/tablet: list OR record (drill-in). laptop: list + record (+ push drawer). wide: + persistent details.
 */
export function InboxRoute() {
  const { bp, selectedId, selected, setNewOpen, detailsOpen, listCollapsed, toggleList, totalUnread } = useInbox();
  const drill = bp === "phone" || bp === "tablet";
  const showList = !drill || !selectedId;
  const showRecord = !drill || Boolean(selectedId);

  return (
    <>
      {showList && (!drill && listCollapsed ? (
        <button type="button" onClick={toggleList} title="Show conversations" aria-label="Show conversations" className="w-10 shrink-0 border-r border-border bg-surface-sunken flex flex-col items-center pt-2 gap-2 text-ink-3 hover:text-ink-1">
          <PanelLeftOpen className="size-5" />
          {totalUnread > 0 && <span className="text-2xs font-medium tabular-nums rounded-full bg-crayon-blue-base text-white px-1.5">{totalUnread > 99 ? "99+" : totalUnread}</span>}
        </button>
      ) : (
        <ThreadList className={cn(drill ? "flex-1" : "w-list shrink-0 border-r border-border")} />
      ))}
      {showRecord && (
        <div className="flex-1 min-w-0 min-h-0 flex">
          {selectedId ? (
            <RecordPane key={selectedId} />
          ) : (
            <div className="flex-1 flex items-center justify-center bg-surface">
              <EmptyState
                icon={<MessageSquare />}
                title="Pick a conversation"
                hint={`All channels for a contact live in one thread. Press ${MOD}+K to search, or start a new conversation.`}
                action={<Button variant="primary" size="sm" onClick={() => setNewOpen(true)}>New conversation</Button>}
              />
            </div>
          )}
          {!drill && selected && detailsOpen && <DetailsDrawer />}
        </div>
      )}
      {drill && selected && <DetailsDrawer />}
    </>
  );
}
