// 我的服务（工作二）：45 天履约服务实例——准备清单、本人激活、今日事项与日历。
// 页面只呈现服务端事实：未配置的授权显示待配置，不承诺每日触达与送达。
const api = require('../../utils/api');
const format = require('../../utils/format');

const STATUS_LABEL = {
  preparing: '准备中', active: '服务中', paused: '已暂停',
  expired: '已到期', closed: '已结案', terminated: '已终止'
};
// 门禁项 → 授权 purpose（仅这两项支持本人自助授权；其余由服务方完成）
const CHECK_CONSENT = {
  device_vendor_consent: 'steward_device_data',
  platform_consent: 'steward_service'
};
const TASK_TYPE_LABEL = {
  daily: '日常', follow_up: '随访', review_prep: '复评准备',
  final_review: '期末复评', closure: '结案'
};

function pad(n) { return n < 10 ? '0' + n : '' + n; }
function monthOf(dateStr) { return dateStr.slice(0, 7); }
function monthShift(month, delta) {
  const y = Number(month.slice(0, 4));
  const m = Number(month.slice(5, 7)) + delta;
  const d = new Date(y, m - 1, 1);
  return d.getFullYear() + '-' + pad(d.getMonth() + 1);
}
function todayStr() {
  const d = new Date();
  return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
}

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    instances: [],
    currentId: 0,
    instance: null,
    view: 'overview',
    month: '',
    calendarDays: [],
    calendarWeeks: [],
    selectedDate: '',
    selectedTasks: [],
    timeline: [],
    busy: false,
    activating: false
  },

  onShow() { this.reload(); },

  onPullDownRefresh() {
    this.reload(() => wx.stopPullDownRefresh());
  },

  reload(done) {
    this.setData({ loading: true, error: '' });
    api.listStewardInstances().then((list) => {
      const items = (list || []).map((it) => Object.assign({}, it, {
        statusLabel: STATUS_LABEL[it.status] || it.status
      }));
      if (!items.length) {
        this.setData({ loading: false, instances: [], instance: null, currentId: 0 });
        if (done) done();
        return;
      }
      const keep = items.find((it) => it.id === this.data.currentId);
      this.setData({ loading: false, instances: items, currentId: keep ? keep.id : items[0].id });
      this.loadDetail();
      if (done) done();
    }).catch((err) => {
      this.setData({ loading: false, error: err.detail || '服务加载失败' });
      if (done) done();
    });
  },

  onPickInstance(e) {
    this.setData({ currentId: Number(e.currentTarget.dataset.id), view: 'overview' });
    this.loadDetail();
  },

  loadDetail() {
    const id = this.data.currentId;
    if (!id) return;
    api.getStewardInstance(id).then((detail) => {
      const checks = (detail.prep_checks || []).map((c) => Object.assign({}, c, {
        consentPurpose: CHECK_CONSENT[c.check_type] || '',
        canGrant: !!CHECK_CONSENT[c.check_type]
          && c.status !== 'passed' && (c.missing_reason || '').indexOf('待配置') < 0
      }));
      const instance = Object.assign({}, detail, {
        statusLabel: STATUS_LABEL[detail.status] || detail.status,
        prepChecks: checks,
        passedCount: checks.filter((c) => c.status === 'passed').length,
        todayTasks: (detail.today_tasks || []).map(this.mapTask),
        endsAtText: detail.ends_at ? format.full(detail.ends_at) : '',
        activatedText: detail.activated_at ? format.full(detail.activated_at) : ''
      });
      const month = this.data.month || (detail.activated_at ? monthOf(detail.activated_at) : '');
      this.setData({ instance, month });
      this.loadTimeline();
      if (this.data.view === 'calendar') this.loadCalendar();
    }).catch((err) => {
      this.setData({ error: err.detail || '服务详情加载失败' });
    });
  },

  mapTask(t) {
    return Object.assign({}, t, {
      typeLabel: TASK_TYPE_LABEL[t.task_type] || t.task_type,
      dueText: t.due_at ? format.full(t.due_at) : '',
      completed: t.status === 'done'
    });
  },

  loadTimeline() {
    api.listStewardTimeline(this.data.currentId).then((list) => {
      this.setData({
        timeline: (list || []).slice(0, 6).map((ev) => ({
          id: ev.id,
          type: ev.event_type,
          actor: ev.actor_type === 'user' ? '本人' : (ev.actor_type === 'admin' ? '服务方' : '系统'),
          created: format.full(ev.created_at)
        }))
      });
    }).catch(() => {});
  },

  onTab(e) {
    const view = e.currentTarget.dataset.v;
    this.setData({ view });
    if (view === 'calendar' && !this.data.calendarDays.length) this.loadCalendar();
  },

  loadCalendar() {
    const inst = this.data.instance;
    if (!inst || !this.data.month) return;
    api.getStewardCalendar(inst.id, this.data.month).then((days) => {
      const today = todayStr();
      const selected = this.data.selectedDate && this.data.selectedDate.slice(0, 7) === this.data.month
        ? this.data.selectedDate : today.slice(0, 7) === this.data.month ? today
          : (days.find((d) => d.tasks.length) || {}).date || '';
      const weeks = [];
      let week = new Array(new Date(this.data.month + '-01T00:00:00').getDay()).fill(null);
      days.forEach((d) => {
        week.push(Object.assign({}, d, {
          label: d.date.slice(8),
          isToday: d.date === today,
          hasTasks: d.tasks.length > 0,
          pendingCount: d.tasks.filter((t) => t.status === 'pending').length
        }));
        if (week.length === 7) { weeks.push(week); week = []; }
      });
      if (week.length) { while (week.length < 7) week.push(null); weeks.push(week); }
      this.setData({ calendarDays: days, calendarWeeks: weeks });
      this.pickDay(selected);
    }).catch((err) => {
      this.setData({ error: err.detail || '日历加载失败' });
    });
  },

  shiftMonth(e) {
    const delta = Number(e.currentTarget.dataset.d);
    this.setData({ month: monthShift(this.data.month, delta), selectedDate: '' });
    this.loadCalendar();
  },

  pickDay(e) {
    const date = typeof e === 'string' ? e : e.currentTarget.dataset.date;
    if (!date) { this.setData({ selectedDate: '', selectedTasks: [] }); return; }
    const day = this.data.calendarDays.find((d) => d.date === date);
    this.setData({
      selectedDate: date,
      selectedTasks: ((day && day.tasks) || []).map(this.mapTask)
    });
  },

  grantConsent(e) {
    const purpose = e.currentTarget.dataset.purpose;
    if (this.data.busy) return;
    this.setData({ busy: true });
    api.grantStewardConsent({ purpose }).then(() => {
      this.setData({ busy: false });
      wx.showToast({ title: '授权成功', icon: 'success' });
      this.loadDetail();
    }).catch((err) => {
      this.setData({ busy: false });
      wx.showToast({ title: err.detail || '授权失败', icon: 'none' });
    });
  },

  activate() {
    const inst = this.data.instance;
    if (!inst || this.data.activating) return;
    if (inst.passedCount < inst.prepChecks.length) {
      wx.showToast({ title: '准备项尚未全部完成', icon: 'none' });
      return;
    }
    this.setData({ activating: true });
    api.activateStewardInstance(inst.id).then(() => {
      this.setData({ activating: false });
      wx.showToast({ title: '服务已开通', icon: 'success' });
      this.reload();
    }).catch((err) => {
      this.setData({ activating: false });
      const detail = err.detail || {};
      const missing = detail && detail.missing ? detail.missing : null;
      wx.showModal({
        title: '暂时无法开通',
        content: missing
          ? '以下准备项尚未完成：' + missing.join('、')
          : (typeof detail === 'string' ? detail : '请稍后重试或联系管家'),
        showCancel: false
      });
    });
  },

  completeTask(e) {
    const id = Number(e.currentTarget.dataset.id);
    const task = (this.data.selectedTasks.find((t) => t.id === id)
      || (this.data.instance.todayTasks || []).find((t) => t.id === id));
    if (!task || task.assignee_type !== 'user' || task.completed) return;
    const self = this;
    wx.showModal({
      title: '完成「' + task.typeLabel + '」',
      editable: true,
      placeholderText: '记录今日执行情况（可选）',
      success(res) {
        if (!res.confirm) return;
        api.completeStewardTask(self.data.currentId, id, { result: res.content || '' }).then(() => {
          wx.showToast({ title: '已完成', icon: 'success' });
          self.loadDetail();
          if (self.data.view === 'calendar') self.loadCalendar();
        }).catch((err) => {
          wx.showToast({ title: err.detail || '操作失败', icon: 'none' });
        });
      }
    });
  },

  goBenefits() {
    require('../../utils/access').navigate('/pages/benefits/benefits');
  },

  goOrders() {
    require('../../utils/access').navigate('/pages/orders/orders');
  }
}, 'my-service'));
