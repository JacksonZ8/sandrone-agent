import { useEffect, useRef, useState } from "react";
import "@xterm/xterm/css/xterm.css";
import ui from "../ui/ui.module.css";
import styles from "./TaskTray.module.css";

interface Props {
  taskId: number;
  label: string;
}

/** 终端类智能体（Oh-DSH）的真终端：她把任务打进去，你看着它干活，也可以直接在这里打字接手。 */
export default function TerminalPanel({ taskId, label }: Props) {
  const host = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<"connecting" | "open" | "closed">("connecting");

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};
    void Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]).then(
      ([{ Terminal }, { FitAddon }]) => {
        if (disposed || !host.current) return;
        const term = new Terminal({
          convertEol: false,
          cursorBlink: true,
          fontSize: 12,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          theme: { background: "#1d2329" },
        });
        const fit = new FitAddon();
        term.loadAddon(fit);
        term.open(host.current);
        fit.fit();
        const scheme = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(
          `${scheme}://${window.location.host}/api/agent-tasks/${taskId}/terminal`,
        );
        ws.binaryType = "arraybuffer";
        const sendSize = () => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
          }
        };
        ws.onopen = () => {
          setState("open");
          sendSize();
        };
        ws.onmessage = (event) => {
          if (event.data instanceof ArrayBuffer) term.write(new Uint8Array(event.data));
        };
        ws.onclose = () => setState("closed");
        const typing = term.onData((data) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data }));
        });
        const observer = new ResizeObserver(() => {
          fit.fit();
          sendSize();
        });
        observer.observe(host.current);
        cleanup = () => {
          observer.disconnect();
          typing.dispose();
          ws.close();
          term.dispose();
        };
      },
    );
    return () => {
      disposed = true;
      cleanup();
    };
  }, [taskId]);

  return (
    <div className={styles.terminalWrap}>
      <div ref={host} className={styles.terminal} aria-label={`${label} 的终端`} />
      <p className={ui.hint}>
        {state === "connecting" && `正在连上 ${label} 的终端…`}
        {state === "open" && "可以直接在终端里打字接手；它要你确认时也在这里回答。"}
        {state === "closed" && "终端已关闭。"}
      </p>
    </div>
  );
}
