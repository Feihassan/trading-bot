import { useEffect, useRef, useState } from "react";
import { API_KEY } from "../api";

/** Connects to the /ws/live endpoint (proxied to the FastAPI backend) and
 * keeps the most recent payload in state, reconnecting with backoff if
 * the connection drops - so the dashboard keeps updating without a
 * manual refresh (spec section 23) and recovers on its own if the API
 * process restarts. */
export function useLiveSocket() {
  const [data, setData] = useState(null);
  const [connected, setConnected] = useState(false);
  const retryDelay = useRef(1000);

  useEffect(() => {
    let socket;
    let closedByEffect = false;
    let retryTimer;

    function connect() {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const query = API_KEY ? `?api_key=${encodeURIComponent(API_KEY)}` : "";
      socket = new WebSocket(`${protocol}://${window.location.host}/ws/live${query}`);

      socket.onopen = () => {
        setConnected(true);
        retryDelay.current = 1000;
      };
      socket.onmessage = (event) => {
        try {
          setData(JSON.parse(event.data));
        } catch {
          // ignore malformed frame
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closedByEffect) {
          retryTimer = setTimeout(connect, retryDelay.current);
          retryDelay.current = Math.min(retryDelay.current * 1.5, 15000);
        }
      };
      socket.onerror = () => socket.close();
    }

    connect();
    return () => {
      closedByEffect = true;
      clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);

  return { data, connected };
}
