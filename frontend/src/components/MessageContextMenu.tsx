import { useState, useRef, useEffect, useCallback } from "react";
import { Pin, PinOff, Reply, SmilePlus } from "lucide-react";
import { useFrappePostCall } from "frappe-react-sdk";
import { toast } from "sonner";
import type { Message } from "../types";

const QUICK_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "🎉", "🔥", "👏"];

interface MessageContextMenuProps {
  message: Message;
  position: { x: number; y: number } | null;
  onClose: () => void;
  onReply: (message: Message) => void;
  onRefresh: () => void;
}

export function MessageContextMenu({
  message,
  position,
  onClose,
  onReply,
  onRefresh,
}: MessageContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [showEmojiPicker, setShowEmojiPicker] = useState(false);

  const { call: pinCall } = useFrappePostCall("excom.excom.api.chat.pin_message");
  const { call: unpinCall } = useFrappePostCall("excom.excom.api.chat.unpin_message");
  const { call: reactCall } = useFrappePostCall("excom.excom.api.chat.toggle_reaction");

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose]);

  const handlePin = useCallback(async () => {
    try {
      if (message.isPinned) {
        await unpinCall({ message_name: message.id });
        toast.success("Message unpinned");
      } else {
        await pinCall({ message_name: message.id });
        toast.success("Message pinned");
      }
      onRefresh();
    } catch {
      toast.error("Failed to update pin");
    }
    onClose();
  }, [message, pinCall, unpinCall, onRefresh, onClose]);

  const handleReply = useCallback(() => {
    onReply(message);
    onClose();
  }, [message, onReply, onClose]);

  const handleReaction = useCallback(
    async (emoji: string) => {
      try {
        await reactCall({ message_name: message.id, emoji });
        onRefresh();
      } catch {
        toast.error("Failed to react");
      }
      onClose();
    },
    [message, reactCall, onRefresh, onClose]
  );

  if (!position) return null;

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-surface-sunken border border-border-strong rounded-lg shadow-ex py-1 min-w-[180px]"
      style={{
        left: Math.min(position.x, window.innerWidth - 220),
        top: Math.min(position.y, window.innerHeight - 300),
      }}
    >
      {showEmojiPicker ? (
        <div className="p-2">
          <div className="grid grid-cols-4 gap-1">
            {QUICK_EMOJIS.map((emoji) => (
              <button
                key={emoji}
                onClick={() => handleReaction(emoji)}
                className="w-9 h-9 flex items-center justify-center rounded-md hover:bg-surface-active text-lg transition-colors"
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <>
          <button
            onClick={() => setShowEmojiPicker(true)}
            className="w-full px-3 py-2 text-left text-sm text-ink-1 hover:bg-surface-active flex items-center gap-2 transition-colors"
          >
            <SmilePlus className="w-4 h-4 text-ink-3" />
            React
          </button>
          <button
            onClick={handleReply}
            className="w-full px-3 py-2 text-left text-sm text-ink-1 hover:bg-surface-active flex items-center gap-2 transition-colors"
          >
            <Reply className="w-4 h-4 text-ink-3" />
            Reply
          </button>
          <div className="border-t border-border-strong my-1" />
          <button
            onClick={handlePin}
            className="w-full px-3 py-2 text-left text-sm text-ink-1 hover:bg-surface-active flex items-center gap-2 transition-colors"
          >
            {message.isPinned ? (
              <>
                <PinOff className="w-4 h-4 text-ink-3" />
                Unpin
              </>
            ) : (
              <>
                <Pin className="w-4 h-4 text-ink-3" />
                Pin
              </>
            )}
          </button>
        </>
      )}
    </div>
  );
}

interface ReactionBarProps {
  reactions: Record<string, string[]>;
  messageId: string;
  onRefresh: () => void;
}

export function ReactionBar({ reactions, messageId, onRefresh }: ReactionBarProps) {
  const { call: reactCall } = useFrappePostCall("excom.excom.api.chat.toggle_reaction");

  const handleToggle = async (emoji: string) => {
    try {
      await reactCall({ message_name: messageId, emoji });
      onRefresh();
    } catch {
      toast.error("Failed to react");
    }
  };

  const entries = Object.entries(reactions);
  if (entries.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {entries.map(([emoji, users]) => (
        <button
          key={emoji}
          onClick={() => handleToggle(emoji)}
          className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-surface-active hover:bg-surface-active border border-border-strong text-xs transition-colors"
          title={users.join(", ")}
        >
          <span>{emoji}</span>
          <span className="text-ink-3">{users.length}</span>
        </button>
      ))}
    </div>
  );
}
