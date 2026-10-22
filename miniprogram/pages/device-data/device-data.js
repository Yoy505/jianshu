// 设备数据（工作二切片 C）：绑定状态、最近同步与趋势；只展示服务端真实事实，
// 无数据日不填补，趋势不作医学判断；厂商适配待配置时如实提示。
const api = require('../../utils/api');
const format = require('../../utils/format');

const METRICS = [
  { key: 'sleep_duration', label: '睡眠时长（分钟）' },
  { key: 'sleep_score', label: '睡眠评分' },
  { key: 'heart_rate', label: '心率（次/分）' },
  { key: 'steps', label: '步数' }
];

Page(require('../../utils/hifi-page')({
  data: {
    loading: true,
    error: '',
    device: null,
    metric: 'sleep_duration',
    metrics: METRICS,
    trend: null,
    points: [],
    unbinding: false
  },

  onShow() { this.reload(); },
  onPullDownRefresh() { this.reload(() => wx.stopPullDownRefresh()); },

  reload(done) {
    this.setData({ loading: true, error: '' });
    api.getMyDevice().then((device) => {
      this.setData({ loading: false, device });
      this.loadTrend();
      if (done) done();
    }).catch((err) => {
      this.setData({ loading: false, error: err.detail || '设备信息加载失败' });
      if (done) done();
    });
  },

  loadTrend() {
    api.getDeviceTrend(this.data.metric, 14).then((trend) => {
      const points = (trend.dates || []).map((date, index) => ({
        date,
        value: trend.series[index],
        isGap: false
      }));
      (trend.gaps || []).forEach((gap) => points.push({ date: gap, value: null, isGap: true }));
      points.sort((a, b) => (a.date < b.date ? -1 : 1));
      this.setData({
        trend,
        points,
        lastSyncText: trend.last_synced_at ? format.full(trend.last_synced_at) : '尚未同步'
      });
    }).catch((err) => {
      this.setData({ error: err.detail || '趋势加载失败' });
    });
  },

  onPickMetric(e) {
    this.setData({ metric: e.currentTarget.dataset.key });
    this.loadTrend();
  },

  unbind() {
    const device = this.data.device;
    if (!device || !device.binding || this.data.unbinding) return;
    const self = this;
    wx.showModal({
      title: '解绑设备',
      content: '解绑后停止数据同步；已同步数据保留归属记录。确定解绑？',
      confirmText: '解绑',
      cancelText: '暂不',
      success(res) {
        if (!res.confirm) return;
        self.setData({ unbinding: true });
        api.unbindDevice(device.binding.id, '本人申请解绑').then(() => {
          self.setData({ unbinding: false });
          wx.showToast({ title: '已解绑', icon: 'success' });
          self.reload();
        }).catch((err) => {
          self.setData({ unbinding: false });
          wx.showToast({ title: err.detail || '解绑失败', icon: 'none' });
        });
      }
    });
  }
}, 'device-data'));
