import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Message } from "../types";
import { spokenText } from "./format";

/** 她的声音：按需合成，单一播放器，记录正在播放哪一条。 */
export function useVoice(onAudio: (messageId: number, audioPath: string) => void) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [busyIds, setBusyIds] = useState<number[]>([]);
  const [playingId, setPlayingId] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "auto";
    const onPlay = () => setPlaying(true);
    const onStop = () => setPlaying(false);
    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onStop);
    audio.addEventListener("ended", onStop);
    audioRef.current = audio;
    return () => {
      audio.pause();
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onStop);
      audio.removeEventListener("ended", onStop);
      audioRef.current = null;
    };
  }, []);

  const play = useCallback((messageId: number, audioPath: string) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (!audio.src.endsWith(audioPath)) audio.src = audioPath;
    audio.currentTime = 0;
    setPlayingId(messageId);
    // 浏览器可能拦截没有用户手势的自动播放；这时停在可点播放的状态。
    audio.play().catch(() => setPlaying(false));
  }, []);

  const speak = useCallback(
    async (message: Message, autoplay = true) => {
      if (message.audio_path) {
        if (autoplay) play(message.id, message.audio_path);
        return;
      }
      const text = spokenText(message.content);
      if (!text) return;
      setBusyIds((current) => [...current, message.id]);
      setError("");
      try {
        const result = await api.synthesizeTTS(text, message.id);
        onAudio(message.id, result.audio_path);
        if (autoplay) play(message.id, result.audio_path);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught));
      } finally {
        setBusyIds((current) => current.filter((id) => id !== message.id));
      }
    },
    [onAudio, play],
  );

  const toggle = useCallback(() => {
    const audio = audioRef.current;
    if (!audio || !audio.src) return;
    if (audio.paused) audio.play().catch(() => setPlaying(false));
    else audio.pause();
  }, []);

  const stop = useCallback(() => {
    audioRef.current?.pause();
  }, []);

  return { busyIds, playingId, playing, error, speak, play, toggle, stop };
}
