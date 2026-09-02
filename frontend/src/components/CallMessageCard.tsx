import React, { useState, useRef, useEffect } from "react";
import { Phone, PhoneIncoming, PhoneOutgoing, PhoneMissed, Play, Pause, Download, Volume2, Clock } from "lucide-react";

interface CallMessageCardProps {
  message: {
    name: string;
    direction?: string;
    content_text?: string;
    creation?: string;
    delivery_status?: string;
    call_id?: string;
    duration?: number;
    recording_url?: string;
  };
  threadCallerNumber?: string;
}

export const CallMessageCard: React.FC<CallMessageCardProps> = ({ message }) => {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(message.duration || 0);
  const [playbackRate, setPlaybackRate] = useState(1);
  const [hasAudio, setHasAudio] = useState(!!message.recording_url || !!message.call_id);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const isOutbound = message.direction === "Outbound";
  const isMissed = message.content_text?.toLowerCase().includes("missed") || message.delivery_status === "Failed";

  const audioSrc = message.call_id 
    ? `/api/method/excom.excom.api.voice.get_recording?call_id=${message.call_id}`
    : message.recording_url || "";

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate;
    }
  }, [playbackRate]);

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
      setIsPlaying(false);
    } else {
      audioRef.current.play().then(() => {
        setIsPlaying(true);
      }).catch((e) => {
        console.error("Audio playback failed:", e);
        setHasAudio(false);
      });
    }
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      setCurrentTime(audioRef.current.currentTime);
    }
  };

  const handleLoadedMetadata = () => {
    if (audioRef.current && !isNaN(audioRef.current.duration) && audioRef.current.duration !== Infinity) {
      setDuration(Math.floor(audioRef.current.duration));
    }
  };

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    const time = parseFloat(e.target.value);
    setCurrentTime(time);
    if (audioRef.current) {
      audioRef.current.currentTime = time;
    }
  };

  const cycleRate = () => {
    const rates = [1, 1.25, 1.5, 2];
    const next = rates[(rates.indexOf(playbackRate) + 1) % rates.length];
    setPlaybackRate(next);
  };

  const formatSeconds = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  };

  return (
    <div className={`flex flex-col my-2 max-w-md w-full ${isOutbound ? "ml-auto items-end" : "mr-auto items-start"}`}>
      <div className={`p-3.5 rounded-2xl border transition-all shadow-sm ${
        isMissed 
          ? "bg-red-50/80 border-red-200 text-red-950 dark:bg-red-950/30 dark:border-red-800 dark:text-red-200" 
          : isOutbound 
            ? "bg-blue-600 text-white border-blue-500 shadow-blue-500/10" 
            : "bg-white border-slate-200 text-slate-900 dark:bg-slate-800 dark:border-slate-700 dark:text-slate-100"
      }`}>
        {/* Header line */}
        <div className="flex items-center gap-2.5 mb-2">
          <div className={`p-1.5 rounded-full ${
            isMissed 
              ? "bg-red-100 text-red-600 dark:bg-red-900/50 dark:text-red-400" 
              : isOutbound 
                ? "bg-white/20 text-white" 
                : "bg-emerald-100 text-emerald-600 dark:bg-emerald-900/50 dark:text-emerald-400"
          }`}>
            {isMissed ? <PhoneMissed size={14} /> : isOutbound ? <PhoneOutgoing size={14} /> : <PhoneIncoming size={14} />}
          </div>
          <div>
            <div className="text-xs font-semibold tracking-tight">
              {isMissed ? "Missed Call" : isOutbound ? "Outbound Call" : "Inbound Call"}
            </div>
            <div className={`text-[10px] ${isOutbound ? "text-blue-100" : "text-slate-500 dark:text-slate-400"}`}>
              {message.creation ? new Date(message.creation).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ""}
            </div>
          </div>
          {duration > 0 && (
            <div className={`ml-auto text-[10px] font-mono px-2 py-0.5 rounded-full flex items-center gap-1 ${
              isOutbound ? "bg-white/20 text-white" : "bg-slate-100 text-slate-600 dark:bg-slate-700 dark:text-slate-300"
            }`}>
              <Clock size={10} />
              {formatSeconds(duration)}
            </div>
          )}
        </div>

        {/* Audio Player if recording available */}
        {hasAudio && !isMissed && (
          <div className={`mt-2 p-2.5 rounded-xl flex flex-col gap-2 ${
            isOutbound ? "bg-blue-700/60" : "bg-slate-50 dark:bg-slate-900/50 border border-slate-100 dark:border-slate-800"
          }`}>
            <audio
              ref={audioRef}
              src={audioSrc}
              onTimeUpdate={handleTimeUpdate}
              onLoadedMetadata={handleLoadedMetadata}
              onEnded={() => setIsPlaying(false)}
            />
            <div className="flex items-center gap-3">
              <button
                onClick={togglePlay}
                className={`p-2 rounded-full transition-transform active:scale-95 shadow-sm ${
                  isOutbound
                    ? "bg-white text-blue-600 hover:bg-blue-50"
                    : "bg-blue-600 text-white hover:bg-blue-700"
                }`}
                title={isPlaying ? "Pause" : "Play Recording"}
              >
                {isPlaying ? <Pause size={14} /> : <Play size={14} className="ml-0.5" />}
              </button>

              <div className="flex-1 flex flex-col gap-1">
                <input
                  type="range"
                  min="0"
                  max={duration || 100}
                  value={currentTime}
                  onChange={handleSeek}
                  className="w-full h-1.5 bg-slate-200 dark:bg-slate-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
                />
                <div className="flex justify-between text-[9px] font-mono opacity-80">
                  <span>{formatSeconds(currentTime)}</span>
                  <span>{formatSeconds(duration)}</span>
                </div>
              </div>

              <button
                onClick={cycleRate}
                className={`text-[9.5px] font-bold px-1.5 py-0.5 rounded border transition-colors ${
                  isOutbound
                    ? "border-white/30 hover:bg-white/10"
                    : "border-slate-300 dark:border-slate-600 hover:bg-slate-200 dark:hover:bg-slate-800"
                }`}
                title="Playback Speed"
              >
                {playbackRate}x
              </button>
            </div>
          </div>
        )}

        {/* Message text note */}
        {message.content_text && !hasAudio && (
          <div className="text-xs mt-1 opacity-90">
            {message.content_text}
          </div>
        )}
      </div>
    </div>
  );
};