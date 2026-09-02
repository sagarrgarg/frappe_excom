import React, { useState, useEffect } from "react";
import { PhoneCall, PhoneOff, User, Mic, MicOff, Maximize2 } from "lucide-react";

interface ActiveCallData {
  call_id: string;
  provider_call_id?: string;
  direction?: string;
  status?: string;
  to_number?: string;
  from_number?: string;
  agent?: string;
  caller_name?: string;
  thread?: string;
  started_at?: number;
}

interface ActiveCallWidgetProps {
  call: ActiveCallData | null;
  onHangup?: (callId: string) => void;
  onOpenThread?: (threadId: string) => void;
}

export const ActiveCallWidget: React.FC<ActiveCallWidgetProps> = ({ call, onHangup, onOpenThread }) => {
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    if (!call || call.status === "Completed" || call.status === "Missed") {
      setDuration(0);
      return;
    }
    const timer = setInterval(() => {
      setDuration((prev) => prev + 1);
    }, 1000);
    return () => clearInterval(timer);
  }, [call]);

  if (!call || call.status === "Completed" || call.status === "Missed") {
    return null;
  }

  const formatTimer = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  const isRinging = call.status === "Ringing";

  return (
    <div className="fixed bottom-6 right-6 z-50 animate-in fade-in slide-in-from-bottom-5 duration-300">
      <div className="bg-slate-900/95 text-white border border-slate-700/80 shadow-2xl backdrop-blur-md rounded-2xl p-4 flex items-center gap-4 min-w-[320px]">
        {/* Pulsing Avatar */}
        <div className="relative">
          <div className={`w-11 h-11 rounded-full flex items-center justify-center ${
            isRinging ? "bg-amber-500 animate-pulse" : "bg-emerald-500"
          }`}>
            <PhoneCall size={20} className="text-white" />
          </div>
          {isRinging && (
            <span className="absolute -top-1 -right-1 flex h-3 w-3">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-3 w-3 bg-amber-500"></span>
            </span>
          )}
        </div>

        {/* Call Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-bold text-slate-100 truncate">
              {call.caller_name || call.to_number || call.from_number || "Active Call"}
            </span>
            <span className={`text-[9.5px] px-1.5 py-0.2 rounded font-semibold ${
              isRinging ? "bg-amber-500/20 text-amber-300" : "bg-emerald-500/20 text-emerald-300"
            }`}>
              {isRinging ? "Ringing..." : "Connected"}
            </span>
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-0.5">
            {isRinging ? "Dialing..." : formatTimer(duration)} · {call.direction || "Outbound"}
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          {call.thread && onOpenThread && (
            <button
              onClick={() => onOpenThread(call.thread!)}
              className="p-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
              title="Open Conversation"
            >
              <Maximize2 size={15} />
            </button>
          )}

          <button
            onClick={() => onHangup && onHangup(call.call_id)}
            className="p-2.5 rounded-xl bg-red-600 hover:bg-red-700 text-white transition-transform active:scale-95 shadow-lg shadow-red-600/30"
            title="Hangup Call"
          >
            <PhoneOff size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};