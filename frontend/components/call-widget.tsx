"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, PhoneCall, PhoneOff } from "lucide-react";
import { toast } from "sonner";
import { PipecatClient } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type CallState = "idle" | "connecting" | "connected" | "ending";

export interface CallContext {
  direction: "inbound" | "outbound";
  contact_id?: string;
  campaign_id?: string;
}

export function CallWidget({
  context,
  label = "Call agent",
  onCallEnded,
}: {
  context: CallContext;
  label?: string;
  onCallEnded?: () => void;
}) {
  const [state, setState] = useState<CallState>("idle");
  const [botSpeaking, setBotSpeaking] = useState(false);
  const clientRef = useRef<PipecatClient | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const cleanup = useCallback(() => {
    clientRef.current = null;
    setState("idle");
    setBotSpeaking(false);
    onCallEnded?.();
  }, [onCallEnded]);

  useEffect(() => {
    return () => {
      clientRef.current?.disconnect();
    };
  }, []);

  async function startCall() {
    setState("connecting");
    try {
      const transport = new SmallWebRTCTransport();
      const client = new PipecatClient({
        transport,
        enableMic: true,
        enableCam: false,
        callbacks: {
          onConnected: () => setState("connected"),
          onDisconnected: cleanup,
          onBotStartedSpeaking: () => setBotSpeaking(true),
          onBotStoppedSpeaking: () => setBotSpeaking(false),
          onTrackStarted: (track: MediaStreamTrack) => {
            if (track.kind === "audio" && audioRef.current) {
              audioRef.current.srcObject = new MediaStream([track]);
              audioRef.current.play().catch(() => {
                /* autoplay blocked; user gesture already occurred so unlikely */
              });
            }
          },
        },
      });
      clientRef.current = client;
      await client.connect({
        webrtcRequestParams: {
          endpoint: `${API_BASE}/api/webrtc/offer`,
          requestData: { ...context },
        },
      });
    } catch (e) {
      toast.error(`Could not start call: ${(e as Error).message}`);
      cleanup();
    }
  }

  async function endCall() {
    setState("ending");
    try {
      await clientRef.current?.disconnect();
    } finally {
      cleanup();
    }
  }

  return (
    <div className="flex items-center gap-3">
      {/* remote audio sink */}
      <audio ref={audioRef} autoPlay className="hidden" />
      {state === "idle" && (
        <Button onClick={startCall}>
          <PhoneCall className="mr-1 h-4 w-4" /> {label}
        </Button>
      )}
      {state === "connecting" && (
        <Button disabled>
          <PhoneCall className="mr-1 h-4 w-4 animate-pulse" /> Connecting…
        </Button>
      )}
      {(state === "connected" || state === "ending") && (
        <>
          <Button variant="destructive" onClick={endCall} disabled={state === "ending"}>
            <PhoneOff className="mr-1 h-4 w-4" />
            {state === "ending" ? "Ending…" : "End call"}
          </Button>
          <Badge variant={botSpeaking ? "default" : "secondary"} className="gap-1">
            <Mic className={cn("h-3 w-3", botSpeaking && "animate-pulse")} />
            {botSpeaking ? "Agent speaking" : "Listening"}
          </Badge>
        </>
      )}
    </div>
  );
}
