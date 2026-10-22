// 咚咚聊天：气泡（user 右深青 / provider 左白 / system 居中灰）
// 实时通道 WebSocket 优先（契约五之二）：连上即停轮询，收帧即渲染；
// WS 不可用时自动降级为 3s 轮询增量消息（after_id），发送走 REST。
// onHide 暂停轮询；onUnload 清理全部定时器与连接。
const api = require('../../utils/api');
const config = require('../../utils/config');
const format = require('../../utils/format');
const wsLib = require('../../utils/ws');

const POLL_INTERVAL = 3000;
const TYPING_MAX_WAIT = 5000;

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    empty: false,
    messages: [],
    inputValue: '',
    typing: false,
    sending: false,
    scrollAnchor: '',
    realtime: false,
    tip: config.COMPLIANCE.chat
  },

  onLoad(options) {
    this.consultId = options.id;
    this.setData({hfServiceName: options.service_name ? decodeURIComponent(options.service_name) : '', hfProviderName: options.provider_name ? decodeURIComponent(options.provider_name) : ''});
    this.lastId = 0;
    this.loaded = false;
    this.hidden = false;
    this.unloaded = false;
    this.pollTimer = null;
    this.typingMaxTimer = null;
    this.ws = null;
    this.fetchMessages(true);
    this.connectRealtime();
  },

  onShow() {
    this.hidden = false;
    if (this.loaded && !(this.ws && this.ws.isOpen())) this.startPoll();
  },

  onHide() {
    this.hidden = true;
    this.stopPoll();
  },

  onUnload() {
    this.unloaded = true;
    this.stopPoll();
    this.clearTypingTimer();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  },

  // ───────── 实时通道 ─────────

  connectRealtime() {
    const token = getApp().globalData.token || wx.getStorageSync(config.STORAGE_KEYS.token) || '';
    if (!token) return;
    this.ws = wsLib.connect(token, {
      onOpen: () => {
        if (this.unloaded) return;
        this.setData({ realtime: true });
        this.stopPoll(); // WS 在线，轮询退位
      },
      onFrame: (frame) => this.handleFrame(frame),
      onClose: () => {
        if (this.unloaded) return;
        // 断线降级：回到轮询（页面仍存活时）
        this.setData({ realtime: false });
        if (this.loaded && !this.hidden) this.startPoll();
      }
    });
  },

  handleFrame(frame) {
    if (!frame || this.unloaded) return;
    if (frame.type === 'message' && frame.message) {
      if (String(frame.consult_id) !== String(this.consultId)) return;
      this.appendMessages([frame.message]);
      if (frame.message.sender === 'system' || frame.message.sender === 'provider') this.clearTypingTimer();
      return;
    }
    if (frame.type === 'error') {
      wx.showToast({ title: frame.detail || '发送失败', icon: 'none' });
      this.clearTypingTimer();
    }
  },

  // ───────── 轮询（降级通道）─────────

  stopPoll() {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  },

  clearTypingTimer() {
    if (this.typingMaxTimer) {
      clearTimeout(this.typingMaxTimer);
      this.typingMaxTimer = null;
    }
    if (this.data.typing) this.setData({ typing: false });
  },

  normalize(res) {
    if (Array.isArray(res)) return res;
    if (res && Array.isArray(res.items)) return res.items;
    return [];
  },

  /** 追加去重后的消息（WS 帧与轮询共用） */
  appendMessages(arr) {
    if (!arr || !arr.length) return;
    const byId = {};
    this.data.messages.forEach((m) => {
      byId[m.id] = m;
    });
    let changed = false;
    arr.forEach((m) => {
      const id = Number(m && m.id);
      if (!Number.isSafeInteger(id) || id <= 0) return;
      this.lastId = Math.max(this.lastId, id);
      if (byId[id]) return;
      byId[id] = {
        id,
        sender: m.sender,
        content: m.content,
        time: format.timeOf(m.created_at)
      };
      changed = true;
    });
    if (changed) {
      const messages = Object.keys(byId).map((id) => byId[id]).sort((a, b) => a.id - b.id);
      this.setData({
        messages,
        empty: false,
        scrollAnchor: 'anchor-bottom'
      }, () => { if (!this.hidden) wx.pageScrollTo({ selector: '#anchor-bottom', duration: 150 }); });
    }
  },

  fetchMessages(first) {
    api
      .getMessages(this.consultId, this.lastId)
      .then((res) => {
        const messages = this.normalize(res);
        this.appendMessages(messages);
        if (this.data.typing && messages.some((m) => m.sender === 'system')) this.clearTypingTimer();
        if (first) {
          this.loaded = true;
          this.setData({
            loading: false,
            empty: !this.data.messages.length
          });
          if (!(this.ws && this.ws.isOpen())) this.startPoll();
        }
      })
      .catch(() => {
        if (first) this.setData({ loading: false, error: '消息加载失败，请重试' });
      });
  },

  retry() {
    this.setData({ error: '', loading: true });
    this.fetchMessages(true);
  },

  startPoll() {
    if (this.pollTimer || !this.loaded) return;
    this.pollTimer = setInterval(() => {
      if (!this.hidden) this.fetchMessages(false);
    }, POLL_INTERVAL);
  },

  onInput(e) {
    this.setData({ inputValue: e.detail.value });
  },

  send() {
    const content = (this.data.inputValue || '').trim();
    if (!content || this.data.sending) return;
    this.setData({ sending: true });

    // ── WS 在线：走实时通道（回显 + 回复都由帧推送）──
    if (this.ws && this.ws.isOpen()) {
      const ok = this.ws.send({
        type: 'message',
        consult_id: Number(this.consultId),
        content
      });
      if (ok) {
        this.clearTypingTimer();
        this.setData({ inputValue: '', typing: true });
        this.typingMaxTimer = setTimeout(() => this.clearTypingTimer(), TYPING_MAX_WAIT);
        this.setData({ sending: false });
        return;
      }
    }

    // ── 降级：REST 发送 + typing 时序 + 轮询刷新 ──
    api
      .sendMessage(this.consultId, content)
      .then((result) => {
        this.setData({ inputValue: '' });
        this.appendMessages([result && result.user_message, result && result.system_message].filter(Boolean));
        this.clearTypingTimer();
        this.fetchMessages(false);
      })
      .catch(() => {})
      .then(() => {
        this.setData({ sending: false });
      });
  }
}, 'chat'));
