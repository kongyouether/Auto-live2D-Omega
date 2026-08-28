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

  if (isOverlay) document.documentElement.classList.add("overlay-mode");

  function emit(type, value) {
    for (const listener of listeners[type] || []) {
      try { listener(value); } catch (error) { console.error("stream listener failed", error); }
    }
  }

  function sendJson(value) {
    if (!opened || !socket) return false;
    socket.send(JSON.stringify(value));
    return true;
  }

  function flushModel() {
    if (!opened || !socket || !pendingModel) return;
    socket.send(JSON.stringify({ type: "model-meta", name: pendingModel.name, size: pendingModel.data.byteLength }));
    socket.send(pendingModel.data);
    pendingModel = null;
  }

  function connect() {
    clearTimeout(reconnectTimer);
    const protocol = global.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${global.location.hostname || "127.0.0.1"}:${wsPort}`);
    socket.binaryType = "arraybuffer";
    socket.addEventListener("open", () => {
      opened = true;
      sendJson({ type: "hello", role: isOverlay ? "overlay" : "control" });
      flushModel();
      emit("status", { connected: true });
    });
    socket.addEventListener("close", () => {
      opened = false;
      emit("status", { connected: false });
      reconnectTimer = setTimeout(connect, 1200);
    });
    socket.addEventListener("error", () => socket.close());
    socket.addEventListener("message", event => {
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
    if (listeners[type]) listeners[type].push(listener);
  }

  connect();
  global.AutoStream = { isOverlay, wsPort, sendModel, publishState, publishConfig, on };
})(window);
