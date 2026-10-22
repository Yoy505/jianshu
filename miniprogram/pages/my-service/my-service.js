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
const CONSENT_LABEL = {
  steward_service: '平台服务授权',
  steward_device_data: '设备厂商数据授权'
};
// 激活缺项里非门禁行的文案（S2 试点放行）
const MISSING_LABEL = {
  pilot_release: '试点放行未配置（服务方完成上线准备后开放）'
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
    consentRecords: [],
    outreach: {},
    professional: null,
    phone: null,
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
        releaseBlocked: detail.pilot_release_ready === false,
        releaseNote: detail.pilot_release_reason || '',
        todayTasks: (detail.today_tasks || []).map(this.mapTask),
        endsAtText: detail.ends_at ? format.full(detail.ends_at) : '',
        activatedText: detail.activated_at ? format.full(detail.activated_at) : ''
      });
      const month = this.data.month || (detail.activated_at ? monthOf(detail.activated_at) : '');
      this.setData({ instance, month });
      this.loadTimeline();
      this.loadConsentRecords();
      this.loadOutreachStatus();
      this.loadProfessional();
      this.loadPhoneStatus();
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

  loadPhoneStatus() {
    api.getPhoneStatus().then((data) => {
      this.setData({
        phone: {
          note: data.note || '',
          contactCount: (data.contacts || []).length,
          recentOrders: (data.orders || []).slice(0, 3).map((o) => ({
            id: o.id,
            purpose: o.purpose,
            statusLabel: ({ pending_review: '待审核', queued: '待拨打', pending_config: '待配置', in_progress: '执行中', no_answer: '未接通', busy: '忙线', wrong_number: '错号', refused: '对方拒绝', transferred_human: '人工跟进', completed: '已完成', failed: '失败', cancelled: '已停止' })[o.status] || o.status
          }))
        }
      });
    }).catch(() => {});
  },

  loadProfessional() {
    api.getInstanceProfessional(this.data.currentId).then((data) => {
      this.setData({
        professional: {
          orders: (data.orders || []).map((o) => Object.assign({}, o, {
            typeLabel: ({ initial_assessment: '初评', data_quality: '数据质量', physician_review: '医师复核', final_review: '期末复评', exception: '异常跟进' })[o.order_type] || o.order_type,
            statusLabel: ({ pending: '待处理', in_progress: '处理中', done: '已完成', cancelled: '已取消' })[o.status] || o.status
          })),
          reports: data.reports || [],
          followups: data.followups || [],
          emergencyNote: data.emergency_note || ''
        }
      });
    }).catch(() => {});
  },

  loadOutreachStatus() {
    api.getOutreachStatus().then((st) => {
      this.setData({ outreach: st || {} });
    }).catch(() => {});
  },

  toggleOutreach() {
    const st = this.data.outreach || {};
    const next = !st.subscribed;
    const self = this;
    wx.showModal({
      title: next ? '恢复每日提醒' : '退订每日提醒',
      content: next
        ? '恢复后服务期内的每日事项提醒将继续安排（当前提醒通道待配置，由管家人工跟进）。'
        : '退订后立即停止后续提醒安排；不影响服务履约本身，也不代表申请退款。',
      confirmText: next ? '恢复' : '退订',
      cancelText: '暂不',
      success(res) {
        if (!res.confirm) return;
        api.setOutreachSubscription(next).then(() => {
          wx.showToast({ title: next ? '已恢复' : '已退订', icon: 'success' });
          self.loadOutreachStatus();
        }).catch((err) => {
          wx.showToast({ title: err.detail || '操作失败', icon: 'none' });
        });
      }
    });
  },

  loadConsentRecords() {
    api.getStewardConsentRecords().then((list) => {
      this.setData({
        consentRecords: (list || []).map((r) => Object.assign({}, r, {
          label: CONSENT_LABEL[r.purpose] || r.purpose,
          grantedText: r.granted_at ? format.full(r.granted_at) : '',
          statusText: !r.terms_available
            ? '条款待配置'
            : r.granted ? (r.outdated ? '已授权（条款已更新，需重新确认）' : '已授权 ' + r.version)
              : '未授权'
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

  // S1：先展示服务端条款全文，本人确认后提交所见版本；服务端校验版本一致
  grantConsent(e) {
    const purpose = e.currentTarget.dataset.purpose;
    if (this.data.busy) return;
    this.setData({ busy: true });
    api.getStewardConsentTerms().then((terms) => {
      const current = (terms || []).find((t) => t.purpose === purpose);
      if (!current) {
        this.setData({ busy: false });
        wx.showToast({ title: '该授权条款尚未配置', icon: 'none' });
        return;
      }
      const self = this;
      wx.showModal({
        title: current.title + '（' + current.version + '）',
        content: current.text,
        confirmText: '同意并确认',
        cancelText: '再看看',
        success(res) {
          if (!res.confirm) {
            self.setData({ busy: false });
            return;
          }
          api.grantStewardConsent({ purpose: purpose, version: current.version }).then(() => {
            self.setData({ busy: false });
            wx.showToast({ title: '授权成功', icon: 'success' });
            self.loadDetail();
          }).catch((err) => {
            self.setData({ busy: false });
            const detail = err.detail || '授权失败';
            wx.showToast({ title: typeof detail === 'string' ? detail : '条款版本已更新，请重新查看', icon: 'none' });
            if (err.detail && err.detail.indexOf && err.detail.indexOf('版本') >= 0) self.loadDetail();
          });
        },
        fail() {
          self.setData({ busy: false });
        }
      });
    }).catch(() => {
      this.setData({ busy: false });
      wx.showToast({ title: '条款加载失败', icon: 'none' });
    });
  },

  revokeConsent(e) {
    const id = Number(e.currentTarget.dataset.id);
    const record = (this.data.consentRecords || []).find((r) => r.consent_id === id);
    if (!id || !record || this.data.busy) return;
    const self = this;
    wx.showModal({
      title: '撤回「' + record.label + '」',
      content: '撤回后对应服务准备项将回退，进行中的服务会停止相应的后续处理；撤回授权不代表申请退款。',
      confirmText: '确认撤回',
      cancelText: '暂不',
      success(res) {
        if (!res.confirm) return;
        self.setData({ busy: true });
        api.revokeConsent(id).then(() => {
          self.setData({ busy: false });
          wx.showToast({ title: '已撤回', icon: 'success' });
          self.loadDetail();
        }).catch((err) => {
          self.setData({ busy: false });
          wx.showToast({ title: err.detail || '撤回失败', icon: 'none' });
        });
      }
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
      const missingText = missing
        ? missing.map((k) => MISSING_LABEL[k] || k).join('；')
        : '';
      wx.showModal({
        title: '暂时无法开通',
        content: missing
          ? '以下条件尚未就绪：' + missingText
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
  },

  goDeviceData() {
    require('../../utils/access').navigate('/pages/device-data/device-data');
  }
}, 'my-service'));
