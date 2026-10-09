// 咚咚实时通道：wx.connectSocket 封装（契约 api.md 五之二）
// - 连接 /mp/ws?token=，30s 心跳 ping
// - handlers: { onFrame(frame), onOpen(), onClose() }
// - 断开由调用方决定降级策略（chat 页：降回轮询）
const config = require('./config');

const WS_BASE = config.BASE_URL.replace(/^http/, 'ws');
const PING_INTERVAL = 25000;

function connect(token, handlers) {
  if (!token) return null;

  const task = wx.connectSocket({
    url: WS_BASE + '/mp/ws?token=' + encodeURIComponent(token),
    fail: () => handlers.onClose && handlers.onClose()
  });
  if (!task) return null;

  const state = { task, open: false, pingTimer: null, closed: false };

  const stopPing = () => {
    if (state.pingTimer) {
      clearInterval(state.pingTimer);
      state.pingTimer = null;
    }
  };

  const close = () => {
    if (state.closed) return;
    state.closed = true;
    stopPing();
    try {
      task.close({ code: 1000 });
    } catch (e) {
      /* 已断开时忽略 */
    }
  };

  task.onOpen(() => {
    if (state.closed) {
      task.close({ code: 1000 });
      return;
    }
    state.open = true;
    state.pingTimer = setInterval(() => {
      send({ type: 'ping' });
    }, PING_INTERVAL);
    handlers.onOpen && handlers.onOpen();
  });

  task.onMessage((res) => {
    let frame = null;
    try {
      frame = JSON.parse(res.data);
    } catch (e) {
      return;
    }
    handlers.onFrame && handlers.onFrame(frame);
  });

  const onDown = () => {
    const wasOpen = state.open;
    state.open = false;
    stopPing();
    if (!state.closed) {
      state.closed = true;
      handlers.onClose && handlers.onClose(wasOpen);
    }
  };
  task.onClose(onDown);
  task.onError(onDown);

  function send(payload) {
    if (!state.open || state.closed) return false;
    task.send({
      data: JSON.stringify(payload),
      fail: () => onDown()
    });
    return true;
  }

  return {
    send,
    close,
    isOpen: () => state.open
  };
}

module.exports = { connect };
