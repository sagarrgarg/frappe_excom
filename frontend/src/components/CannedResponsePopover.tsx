import { useState, useEffect, useRef, useCallback } from "react";
import { Zap, Search, Loader2 } from "lucide-react";
import { Badge } from "./ui/badge";
import { useCannedResponses } from "../hooks/useCannedResponses";

interface CannedResponsePopoverProps {
  isOpen: boolean;
  searchText: string;
  channel?: string;
  onSelect: (content: string) => void;
  onClose: () => void;
}

const CATEGORY_COLORS: Record<string, string> = {
  General: "bg-surface-active text-ink-3 border-border-strong",
  Sales: "bg-crayon-green-tint text-crayon-green-text border-crayon-green-base/40",
  Support: "bg-crayon-blue-tint text-crayon-blue-text border-crayon-blue-base/40",
  Billing: "bg-crayon-amber-tint text-crayon-amber-text border-crayon-amber-base/40",
};

export function CannedResponsePopover({
  isOpen,
  searchText,
  channel,
  onSelect,
  onClose,
}: CannedResponsePopoverProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const { responses, isLoading } = useCannedResponses(
    isOpen ? searchText : null,
    channel,
  );

  useEffect(() => {
    setActiveIndex(0);
  }, [searchText, responses.length]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!isOpen || responses.length === 0) return;

      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((prev) => Math.min(prev + 1, responses.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        onSelect(responses[activeIndex].content);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    },
    [isOpen, responses, activeIndex, onSelect, onClose],
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      return () => document.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, handleKeyDown]);

  useEffect(() => {
    if (listRef.current && activeIndex >= 0) {
      const el = listRef.current.children[activeIndex] as HTMLElement;
      el?.scrollIntoView({ block: "nearest" });
    }
  }, [activeIndex]);

  if (!isOpen) return null;

  return (
    <div className="absolute bottom-full left-0 right-0 mb-2 z-50">
      <div className="bg-surface border border-border-strong rounded-xl shadow-ex overflow-hidden max-h-72">
        <div className="px-3 py-2 border-b border-border flex items-center gap-2">
          <Zap className="w-3.5 h-3.5 text-crayon-amber-text" />
          <span className="text-xs font-medium text-ink-2">
            Canned Responses
          </span>
          {searchText && (
            <span className="text-xs text-ink-3 ml-auto">
              /{searchText}
            </span>
          )}
        </div>

        <div ref={listRef} className="max-h-56 overflow-y-auto">
          {isLoading ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-5 h-5 text-ink-3 animate-spin" />
            </div>
          ) : responses.length === 0 ? (
            <div className="py-4 text-center">
              <p className="text-xs text-ink-3">No matching responses</p>
              <p className="text-xs text-ink-3 mt-1">
                Create one in Excom Canned Response
              </p>
            </div>
          ) : (
            responses.map((response, index) => (
              <button
                key={response.name}
                onClick={() => onSelect(response.content)}
                className={`w-full text-left px-3 py-2.5 transition-colors border-b border-border last:border-0 ${
                  index === activeIndex
                    ? "bg-crayon-blue-tint"
                    : "hover:bg-surface-sunken"
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-medium text-ink-1 truncate">
                    {response.title}
                  </span>
                  <span className="text-xs text-ink-3 font-mono">
                    /{response.shortcode}
                  </span>
                  <Badge
                    className={`ml-auto text-xs px-1.5 h-4 border ${
                      CATEGORY_COLORS[response.category] ||
                      CATEGORY_COLORS.General
                    }`}
                  >
                    {response.category}
                  </Badge>
                </div>
                <p className="text-xs text-ink-3 line-clamp-2 leading-relaxed">
                  {response.content}
                </p>
              </button>
            ))
          )}
        </div>

        <div className="px-3 py-1.5 border-t border-border bg-surface">
          <span className="text-xs text-ink-3">
            ↑↓ navigate · Enter select · Esc close
          </span>
        </div>
      </div>
    </div>
  );
}
