(function (global) {
  "use strict";

  const query = new URLSearchParams(global.location.search);
  const isOverlay = query.get("overlay") === "1";
  const wsPort = Number(query.get("wsPort") || 18766);
  const listeners = { model: [], state: [], config: [], status: [] };
  let socket = null;
  let opened = false;
  let reconnectTimer = null;
  let pendingModel = null;
  let lastStateAt = 0;
  let statusSnapshot = { connected: false };

  if (isOverlay) document.documentElement.classList.add("overlay-mode");

  function emit(type, value) {
    if (type === "status") statusSnapshot = value;
    for (const listener of listeners[type] || []) {
      try { listener(value); } catch (error) { console.error("stream listener failed", error); }
    }
  }

  function sendJson(value) {
    if (!opened || !socket || socket.readyState !== 1) return false;
    try {
      socket.send(JSON.stringify(value));
      return true;
    } catch (_) {
      return false;
    }
  }

  function flushModel() {
    if (!opened || !socket || socket.readyState !== 1 || !pendingModel) return;
    try {
      socket.send(JSON.stringify({ type: "model-meta", name: pendingModel.name, size: pendingModel.data.byteLength }));
      socket.send(pendingModel.data);
    } catch (_) {
      return;
    }
    pendingModel = null;
  }

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connect, 1200);
  }

  function connect() {
    clearTimeout(reconnectTimer);
    try {
      const WebSocketImpl = global.WebSocket;
      if (typeof WebSocketImpl !== "function") throw new Error("WebSocket is unavailable in this browser");
      const protocol = global.location.protocol === "https:" ? "wss:" : "ws:";
      const connection = new WebSocketImpl(`${protocol}//${global.location.hostname || "127.0.0.1"}:${wsPort}`);
      socket = connection;
      connection.binaryType = "arraybuffer";
      connection.addEventListener("open", () => {
        if (socket !== connection) return;
        opened = true;
        sendJson({ type: "hello", role: isOverlay ? "overlay" : "control" });
        flushModel();
        emit("status", { connected: true });
      });
      connection.addEventListener("close", () => {
        if (socket !== connection) return;
        opened = false;
        socket = null;
        emit("status", { connected: false });
        scheduleReconnect();
      });
      connection.addEventListener("error", () => {
        try { connection.close(); } catch (_) { scheduleReconnect(); }
      });
      connection.addEventListener("message", event => {
        if (typeof event.data !== "string") return;
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        if (data.type === "model-ready") emit("model", data);
        else if (data.type === "server-status") {
          emit("status", Object.assign({ connected: true }, data));
          if (isOverlay && data.modelReady) emit("model", { version: data.modelVersion, name: data.modelName });
        } else if (data.type === "state") emit("state", data);
        else if (data.type === "config") emit("config", data);
      });
    } catch (error) {
      opened = false;
      socket = null;
      emit("status", { connected: false, error: error.message || String(error) });
      scheduleReconnect();
    }
  }

  function sendModel(buffer, name) {
    pendingModel = { data: buffer.slice(0), name: name || "model.psd" };
    flushModel();
  }

  function publishState(params) {
    const now = performance.now();
    if (now - lastStateAt < 30) return;
    lastStateAt = now;
    sendJson({ type: "state", params });
  }

  function publishConfig(config) {
    sendJson({ type: "config", config });
  }

  function on(type, listener) {
    if (!listeners[type]) return;
    listeners[type].push(listener);
    if (type === "status" && statusSnapshot) {
      try { listener(statusSnapshot); } catch (error) { console.error("stream listener failed", error); }
    }
  }

  global.AutoStream = { isOverlay, wsPort, sendModel, publishState, publishConfig, on };
  connect();
})(window);
