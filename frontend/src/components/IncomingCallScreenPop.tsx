import React, { useEffect } from "react";
import { PhoneIncoming, User, Building2, Check, X, ArrowRight } from "lucide-react";

interface IncomingCallData {
  provider_call_id: string;
  from_number: string;
  business_number?: string;
  caller_name?: string;
  company_name?: string;
  linked_doctype?: string;
  linked_name?: string;
}

interface IncomingCallScreenPopProps {
  call: IncomingCallData | null;
  onAccept?: (call: IncomingCallData) => void;
  onDismiss?: () => void;
}

export const IncomingCallScreenPop: React.FC<IncomingCallScreenPopProps> = ({ call, onAccept, onDismiss }) => {
  if (!call) return null;

  return (
    <div className="fixed top-6 right-6 z-50 animate-in fade-in slide-in-from-top-4 duration-300 max-w-sm w-full">
      <div className="bg-slate-900 text-white border-2 border-emerald-500/80 shadow-2xl rounded-2xl p-4 overflow-hidden relative">
        {/* Top Glow Banner */}
        <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-emerald-400 via-teal-500 to-emerald-600 animate-pulse" />

        <div className="flex items-start gap-3.5">
          <div className="w-12 h-12 rounded-2xl bg-emerald-500/20 border border-emerald-500/40 text-emerald-400 flex items-center justify-center shrink-0">
            <PhoneIncoming size={22} className="animate-bounce" />
          </div>

          <div className="flex-1 min-w-0">
            <div className="flex items-center justify-between">
              <span className="text-[10px] uppercase font-bold tracking-wider text-emerald-400">
                Incoming Call
              </span>
              <button
                onClick={onDismiss}
                className="text-slate-400 hover:text-white p-0.5 rounded transition-colors"
              >
                <X size={14} />
              </button>
            </div>

            <h4 className="text-sm font-bold text-white truncate mt-0.5">
              {call.caller_name || call.from_number}
            </h4>
            <div className="text-xs text-slate-300 font-mono">
              {call.from_number}
            </div>

            {call.company_name && (
              <div className="flex items-center gap-1.5 text-[11px] text-slate-400 mt-1">
                <Building2 size={11} />
                <span className="truncate">{call.company_name}</span>
              </div>
            )}

            {call.linked_doctype && (
              <div className="mt-2 inline-flex items-center gap-1 text-[10px] bg-slate-800 text-slate-300 px-2 py-0.5 rounded-full border border-slate-700">
                <span>{call.linked_doctype}:</span>
                <span className="font-semibold text-emerald-300">{call.linked_name}</span>
              </div>
            )}
          </div>
        </div>

        {/* Buttons */}
        <div className="flex items-center gap-2 mt-4 pt-3 border-t border-slate-800">
          <button
            onClick={onDismiss}
            className="flex-1 py-2 text-xs font-semibold text-slate-300 bg-slate-800 hover:bg-slate-700 rounded-xl transition-colors"
          >
            Dismiss
          </button>
          <button
            onClick={() => onAccept && onAccept(call)}
            className="flex-1 py-2 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-500 rounded-xl transition-all shadow-md shadow-emerald-600/30 flex items-center justify-center gap-1.5"
          >
            <span>Answer</span>
            <ArrowRight size={13} />
          </button>
        </div>
      </div>
    </div>
  );
};