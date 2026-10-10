import { useCallback, useEffect, useRef, useState } from "react";
import type { Message } from "../types";
import { MIC_WORKLET } from "./micWorklet";
import { PcmPlayer } from "./player";

export type CallState = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "ended";

export interface CallTimings {
  end_silence_ms: number;
  llm_first_token_ms: number;
  first_audio_ms: number;
  audio_seconds: number;
}

interface Options {
  replyLength: "short" | "long";
  onTurn: (messages: Message[]) => void;
}

type ServerEvent = Record<string, unknown> & { type: string };

function socketUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/voice/ws`;
}

/** 实时语音通话：麦克风 → WebSocket → 她的回复音频，带字幕、静音与打断。 */
export function useVoiceCall({ replyLength, onTurn }: Options) {
  const [state, setState] = useState<CallState>("idle");
  const [partial, setPartial] = useState("");
  const [userText, setUserText] = useState("");
  const [herText, setHerText] = useState("");
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [muted, setMuted] = useState(false);
  const [timings, setTimings] = useState<CallTimings | null>(null);
  const [error, setError] = useState("");
  const [tool, setTool] = useState("");
  const [emotion, setEmotion] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const playerRef = useRef<PcmPlayer | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mutedRef = useRef(false);
  const onTurnRef = useRef(onTurn);

  useEffect(() => {
    onTurnRef.current = onTurn;
  }, [onTurn]);

  const cleanup = useCallback(() => {
    socketRef.current?.close();
    socketRef.current = null;
    playerRef.current?.stop();
    playerRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close();
    contextRef.current = null;
  }, []);

  useEffect(() => cleanup, [cleanup]);

  const handleEvent = useCallback((event: ServerEvent) => {
    const player = playerRef.current;
    switch (event.type) {
      case "state":
        setState(event.state as CallState);
        if (event.state === "thinking") {
          setHerText("");
          setTool("");
        }
        break;
      case "partial":
        setPartial(String(event.text || ""));
        break;
      case "user_speech":
        setUserSpeaking(Boolean(event.speaking));
        break;
      case "final":
        setPartial("");
        if (event.text) setUserText(String(event.text));
        break;
      case "sentence":
        setHerText((current) => current + String(event.text || ""));
        break;
      case "tool":
        setTool(String(event.name || ""));
        break;
      case "emotion":
        // 她这句话的神态：交给 Live2D 表情/动作
        setEmotion(String(event.name || ""));
        break;
      case "audio_begin":
        player?.begin(Number(event.sample_rate) || 32000);
        break;
      case "audio_end":
        player?.end();
        break;
      case "interrupt":
        player?.stop();
        break;
      case "retract":
        // 她刚开口就被你接着说的话打断：这半句作废，等合并后的回答
        player?.stop();
        setHerText("");
        break;
      case "turn_done": {
        const messages = [event.user_message, event.assistant_message].filter(Boolean) as Message[];
        onTurnRef.current(messages);
        if (event.timings) setTimings(event.timings as CallTimings);
        break;
      }
      case "error":
        setError(String(event.message || "通话出错了。"));
        if (event.fatal) setState("ended");
        break;
    }
  }, []);

  const start = useCallback(async () => {
    if (socketRef.current) return;
    setError("");
    setState("connecting");
    setPartial("");
    setUserText("");
    setHerText("");
    setTimings(null);
    setMuted(false);
    mutedRef.current = false;
    try {
      // 回声消除让她的声音不被麦克风录回去（否则会自己打断自己）
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      streamRef.current = stream;
      const context = new AudioContext({ latencyHint: "interactive" });
      contextRef.current = context;
      const moduleUrl = URL.createObjectURL(new Blob([MIC_WORKLET], { type: "text/javascript" }));
      await context.audioWorklet.addModule(moduleUrl);
      URL.revokeObjectURL(moduleUrl);
      const player = new PcmPlayer(context);
      playerRef.current = player;

      const socket = new WebSocket(socketUrl());
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;
      player.onDrained = () => {
        if (socket.readyState === WebSocket.OPEN)
          socket.send(JSON.stringify({ type: "playback_drained" }));
      };

      socket.onmessage = (message) => {
        if (message.data instanceof ArrayBuffer) playerRef.current?.push(message.data);
        else handleEvent(JSON.parse(String(message.data)) as ServerEvent);
      };
      socket.onclose = () => {
        if (socketRef.current === socket) {
          cleanup();
          setState("ended");
        }
      };
      socket.onerror = () => setError("和后端的语音连接断开了。");
      await new Promise<void>((resolve, reject) => {
        socket.onopen = () => resolve();
        setTimeout(() => reject(new Error("语音连接超时。")), 8000);
      });
      socket.send(JSON.stringify({ type: "start", reply_length: replyLength, fillers: true }));

      const source = context.createMediaStreamSource(stream);
      const mic = new AudioWorkletNode(context, "mic-processor");
      mic.port.onmessage = (message: MessageEvent<ArrayBuffer>) => {
        if (!mutedRef.current && socket.readyState === WebSocket.OPEN) socket.send(message.data);
      };
      source.connect(mic);
      if (context.state === "suspended") await context.resume();
    } catch (caught) {
      cleanup();
      setState("ended");
      const message = caught instanceof Error ? caught.message : String(caught);
      setError(
        caught instanceof DOMException && caught.name === "NotAllowedError"
          ? "没有麦克风权限：请在浏览器地址栏允许使用麦克风。"
          : message,
      );
    }
  }, [cleanup, handleEvent, replyLength]);

  const stop = useCallback(() => {
    cleanup();
    setState("idle");
    setPartial("");
    setUserSpeaking(false);
  }, [cleanup]);

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    socketRef.current?.send(JSON.stringify({ type: "mute", muted: next }));
  }, []);

  const interrupt = useCallback(() => {
    playerRef.current?.stop();
    socketRef.current?.send(JSON.stringify({ type: "interrupt" }));
  }, []);

  const herLevel = useCallback((): number => {
    const analyser = playerRef.current?.analyser;
    if (!analyser) return 0;
    const data = new Uint8Array(analyser.fftSize);
    analyser.getByteTimeDomainData(data);
    let sum = 0;
    for (const value of data) sum += ((value - 128) / 128) ** 2;
    return Math.sqrt(sum / data.length);
  }, []);

  return {
    state,
    partial,
    userText,
    herText,
    userSpeaking,
    muted,
    timings,
    error,
    tool,
    emotion,
    start,
    stop,
    toggleMute,
    interrupt,
    herLevel,
  };
}
