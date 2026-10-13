import { useEffect, useReducer, useRef, useState } from "react";
import { liveReducer, type LiveGame } from "./live";
import type { Snapshot, StreamMessage } from "./types";

export type SocketStatus = "loading" | "connecting" | "open" | "reconnecting" | "error";

function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}

export function useLiveGame(gamePk: number | null): {
  live: LiveGame | null;
  status: SocketStatus;
} {
  const [live, dispatch] = useReducer(liveReducer, null);
  const [status, setStatus] = useState<SocketStatus>("loading");
  const lastId = useRef("0-0");

  useEffect(() => {
    if (live) lastId.current = live.lastId;
  }, [live]);

  useEffect(() => {
    if (gamePk === null) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    dispatch({ type: "reset" });
    setStatus("loading");

    const connect = () => {
      if (closed) return;
      setStatus(retry === 0 ? "connecting" : "reconnecting");
      socket = new WebSocket(wsUrl(`/ws/games/${gamePk}?last_id=${lastId.current}`));
      socket.onopen = () => {
        retry = 0;
        setStatus("open");
      };
      socket.onmessage = (ev) => {
        const message = JSON.parse(ev.data) as StreamMessage;
        lastId.current = message.id;
        dispatch({ type: "message", message });
      };
      socket.onclose = () => {
        if (closed) return;
        retry += 1;
        setStatus("reconnecting");
        timer = setTimeout(connect, Math.min(1000 * 2 ** retry, 15000));
      };
    };

    fetch(`/api/games/${gamePk}/snapshot`)
      .then((r) => {
        if (!r.ok) throw new Error(`snapshot ${r.status}`);
        return r.json() as Promise<Snapshot>;
      })
      .then((snapshot) => {
        if (closed) return;
        lastId.current = snapshot.stream_id;
        dispatch({ type: "snapshot", snapshot });
        connect();
      })
      .catch(() => setStatus("error"));

    return () => {
      closed = true;
      clearTimeout(timer);
      socket?.close();
    };
  }, [gamePk]);

  return { live, status };
}
