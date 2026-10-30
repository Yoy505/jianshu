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
    chartState: 'none',
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
      const validValues = points.filter((p) => !p.isGap).map((p) => p.value);
      // 通用积累期三态：none（0 天，引导占位）/ single（1 天，画单点+提示差一天）/ ready（≥2 天，趋势线）
      const chartState = validValues.length === 0 ? 'none' : validValues.length === 1 ? 'single' : 'ready';
      this.setData({
        trend,
        points,
        chartState,
        lastSyncText: trend.last_synced_at ? format.full(trend.last_synced_at) : '尚未同步'
      }, () => this.drawChart());
    }).catch((err) => {
      this.setData({ error: err.detail || '趋势加载失败' });
    });
  },


  // 趋势折线：缺口日期断开不连；仅作展示，口径以接口 quality_note 为准
  drawChart() {
    const points = this.data.points || [];
    if (!points.length) return;
    const query = wx.createSelectorQuery().in(this);
    query.select('#trendChart').fields({ node: true, size: true }).exec((res) => {
      if (!res || !res[0] || !res[0].node) return;
      const canvas = res[0].node;
      const ctx = canvas.getContext('2d');
      const dpr = (wx.getSystemInfoSync().pixelRatio) || 2;
      const w = res[0].width;
      const h = res[0].height;
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, w, h);

      const validPoints = points.filter((p) => !p.isGap);
      if (!validPoints.length) return;
      const values = validPoints.map((p) => p.value);
      let min = Math.min.apply(null, values);
      let max = Math.max.apply(null, values);
      if (max === min) { max = min + 1; min = min - 1; }
      const pad = { l: 8, r: 8, t: 18, b: 20 };
      const innerW = w - pad.l - pad.r;
      const innerH = h - pad.t - pad.b;
      const stepX = innerW / Math.max(points.length - 1, 1);
      const yOf = (v) => pad.t + innerH * (1 - (v - min) / (max - min));

      ctx.strokeStyle = '#E5E7EB';
      ctx.lineWidth = 0.5;
      [0, 0.5, 1].forEach((r) => {
        const y = pad.t + innerH * r;
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(w - pad.r, y);
        ctx.stroke();
      });

      const xOf = (i) => pad.l + stepX * i;
      if (validPoints.length >= 2) {
        ctx.strokeStyle = '#0F766E';
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        let drawing = false;
        ctx.beginPath();
        points.forEach((p, i) => {
          if (p.isGap) { drawing = false; return; }
          const x = xOf(i);
          const y = yOf(p.value);
          if (!drawing) { ctx.moveTo(x, y); drawing = true; } else { ctx.lineTo(x, y); }
        });
        ctx.stroke();
      }
      ctx.fillStyle = '#0F766E';
      validPoints.forEach((p) => {
        const i = points.indexOf(p);
        ctx.beginPath();
        ctx.arc(xOf(i), yOf(p.value), validPoints.length === 1 ? 3 : 1.8, 0, Math.PI * 2);
        ctx.fill();
      });

      ctx.fillStyle = '#6B7280';
      ctx.font = '10px sans-serif';
      ctx.textAlign = 'left';
      ctx.fillText(points[0].date.slice(5), pad.l, h - 6);
      ctx.textAlign = 'right';
      ctx.fillText(points[points.length - 1].date.slice(5), w - pad.r, h - 6);
      ctx.textAlign = 'left';
      ctx.fillText(String(max), pad.l, 12);
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
