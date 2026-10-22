// 预约提交：日期（近7天，来自 slots）+ 时段 + 联系人/电话/地址 + 备注 + 费用确认 + 防重
const api = require('../../utils/api');
const format = require('../../utils/format');

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    service: null,
    slots: [],
    selectedDate: '',
    periodOptions: [],
    selectedPeriod: '',
    contactName: '',
    contactPhone: '',
    address: '',
    remark: '',
    submitting: false
  },

  onLoad(options) {
    this.serviceId = options.service_id;
    this.providerId = options.provider_id;
    if (!getApp().ensureLogin()) {
      // 未登录：弹窗已引导去登录，返回上一页
      wx.navigateBack();
      return;
    }
    this.fetch();
  },

  onUnload() {
    if (this.redirectTimer) {
      clearTimeout(this.redirectTimer);
      this.redirectTimer = null;
    }
  },

  fetch() {
    this.setData({ loading: true, error: '' });
    Promise.all([api.getService(this.serviceId), api.providerDetail(this.providerId)])
      .then((res) => {
        const s = res[0];
        const p = res[1];
        const slots = (p.slots || []).map((it) => ({
          date: it.date,
          label: format.mdOf(it.date),
          week: format.weekOf(it.date),
          periods: it.periods || []
        }));
        const isOnline = s.service_type === 'online';
        this.setData({
          loading: false,
          service: {
            id: s.id,
            name: s.name,
            price: format.price(s.price),
            unit: s.unit || '',
            isOnline: isOnline
          },
          provider: {
            id: p.id,
            name: p.name,
            title: p.title || ''
          },
          slots,
          selectedDate: slots.length ? slots[0].date : '',
          periodOptions: slots.length ? slots[0].periods : [],
          selectedPeriod: slots.length && slots[0].periods.length ? slots[0].periods[0] : ''
        });
      })
      .catch((err) => {
        this.setData({ loading: false, error: err.detail || '加载失败，请重试' });
      });
  },

  retry() {
    this.fetch();
  },

  selectDate(e) {
    const date = e.currentTarget.dataset.date;
    const slot = this.data.slots.filter((s) => s.date === date)[0] || { periods: [] };
    this.setData({
      selectedDate: date,
      periodOptions: slot.periods,
      selectedPeriod: slot.periods.length ? slot.periods[0] : ''
    });
  },

  selectPeriod(e) {
    this.setData({ selectedPeriod: e.currentTarget.dataset.period });
  },

  onName(e) {
    this.setData({ contactName: e.detail.value });
  },

  onPhone(e) {
    this.setData({ contactPhone: e.detail.value });
  },

  onAddress(e) {
    this.setData({ address: e.detail.value });
  },

  onRemark(e) {
    this.setData({ remark: e.detail.value });
  },

  submit() {
    if (this.data.submitting) return;
    if (!getApp().ensureLogin()) return;
    const d = this.data;
    if (!d.selectedDate || !d.selectedPeriod) {
      wx.showToast({ title: '请选择预约日期与时段', icon: 'none' });
      return;
    }
    if (!d.contactName.trim()) {
      wx.showToast({ title: '请填写联系人姓名', icon: 'none' });
      return;
    }
    if (!format.phoneValid(d.contactPhone)) {
      wx.showToast({ title: '请输入正确的联系电话', icon: 'none' });
      return;
    }
    if (!d.service.isOnline && !d.address.trim()) {
      wx.showToast({ title: '请填写服务地址', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    api
      .book({
        service_id: Number(this.serviceId),
        provider_id: Number(this.providerId),
        date: d.selectedDate,
        period: d.selectedPeriod,
        address: d.service.isOnline ? '' : d.address.trim(),
        contact_name: d.contactName.trim(),
        contact_phone: d.contactPhone.trim(),
        remark: d.remark.trim() || null
      })
      .then(() => {
        this.setData({ submitting: false });
        wx.showToast({ title: '演示预约已提交', icon: 'success' });
        this.redirectTimer = setTimeout(() => {
          wx.redirectTo({ url: '/pages/appointments/appointments' });
        }, 1200);
      })
      .catch(() => {
        this.setData({ submitting: false });
      });
  }
}, 'book'));
