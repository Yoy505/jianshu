// 健康档案：姓名/性别/出生年月/身高体重/血型/慢病标签/过敏史/手术史/吸烟饮酒/运动频率
const api = require('../../utils/api');
const config = require('../../utils/config');

const BLOOD_TYPES = ['A', 'B', 'AB', 'O', '不详'];
const SPORT_FREQ = ['每天', '每周3-5次', '每周1-2次', '偶尔', '基本不运动'];
const STATUS_UNAVAILABLE = '暂无法确认健康档案状态，请稍后重试。当前不接受填写或修改。';

function readOnlyRows(p) {
  const text = (value) => value === null || value === undefined || value === '' ? '未填写' : String(value);
  return [
    { label: '姓名', value: text(p.name) },
    { label: '性别', value: p.gender === 'female' ? '女' : (p.gender === 'male' ? '男' : '未填写') },
    { label: '出生年月', value: text(p.birth) },
    { label: '身高', value: p.height_cm == null ? '未填写' : p.height_cm + ' cm' },
    { label: '体重', value: p.weight_kg == null ? '未填写' : p.weight_kg + ' kg' },
    { label: '血型', value: text(p.blood) },
    { label: '慢病标签', value: (p.chronic_tags || []).join('、') || '未填写' },
    { label: '过敏史', value: text(p.allergy) },
    { label: '手术史', value: text(p.surgery_history) },
    { label: '吸烟', value: p.smoke ? '是' : '否' },
    { label: '饮酒', value: p.drink ? '是' : '否' },
    { label: '运动频率', value: text(p.sport_freq) }
  ];
}

Page(require('../../utils/hifi-page')({
  openPrivacy() {
    require('../../utils/access').navigate({ url: '/pages/legal/legal?doc=privacy' });
  },

  data: {
    loading: true,
    saving: false,
    profileMode: 'closed',
    statusMessage: STATUS_UNAVAILABLE,
    statusUnavailable: false,
    profileLoadFailed: false,
    readOnlyRows: [],
    hasExistingProfile: false,
    bloods: BLOOD_TYPES,
    sports: SPORT_FREQ,
    chronicOptions: config.CHRONIC_TAGS,
    maxBirth: '',
    form: {
      name: '',
      gender: 'male',
      birth: '',
      height: '',
      weight: '',
      blood: '',
      chronicTags: [],
      allergy: '',
      surgery: '',
      smoke: false,
      drink: false,
      sport: ''
    },
    bloodIndex: -1,
    sportIndex: -1
  },

  onLoad() {
    const now = new Date();
    this.setData({
      maxBirth: now.getFullYear() + '-' + (now.getMonth() + 1 < 10 ? '0' + (now.getMonth() + 1) : now.getMonth() + 1)
    });
    if (!getApp().ensureLogin()) {
      wx.navigateBack();
      return;
    }
    this.fetch();
  },

  onUnload() {
    if (this.backTimer) {
      clearTimeout(this.backTimer);
      this.backTimer = null;
    }
  },

  fetch() {
    this.setData({ loading: true });
    Promise.all([
      api.getProfileCapabilities().then((value) => ({ ok: true, value }), () => ({ ok: false })),
      api.getProfile().then((value) => ({ ok: true, value }), () => ({ ok: false }))
    ])
      .then(([capabilityResult, profileResult]) => {
        const capability = capabilityResult.ok ? capabilityResult.value : null;
        const p = profileResult.ok ? (profileResult.value || null) : null;
        const mode = capability && capability.write_mode === 'demo' && profileResult.ok ? 'demo' : 'closed';
        const unavailable = !capabilityResult.ok || !profileResult.ok;
        this.setData({
          profileMode: mode,
          statusMessage: unavailable ? STATUS_UNAVAILABLE : (capability.message || STATUS_UNAVAILABLE),
          statusUnavailable: unavailable,
          profileLoadFailed: !profileResult.ok,
          hasExistingProfile: !!p,
          readOnlyRows: p ? readOnlyRows(p) : []
        });
        if (p) {
          const bloodIndex = p.blood ? BLOOD_TYPES.indexOf(p.blood) : -1;
          const sportIndex = p.sport_freq ? SPORT_FREQ.indexOf(p.sport_freq) : -1;
          this.setData({
            loading: false,
            bloodIndex: bloodIndex,
            sportIndex: sportIndex,
            form: {
              name: p.name || '',
              gender: p.gender === 'female' ? 'female' : 'male',
              birth: p.birth || '',
              height: p.height_cm != null ? String(p.height_cm) : '',
              weight: p.weight_kg != null ? String(p.weight_kg) : '',
              blood: p.blood || '',
              chronicTags: p.chronic_tags || [],
              allergy: p.allergy || '',
              surgery: p.surgery_history || '',
              smoke: !!p.smoke,
              drink: !!p.drink,
              sport: p.sport_freq || ''
            }
          });
        } else {
          this.setData({ loading: false });
        }
      })
      .catch(() => {
        this.setData({
          loading: false,
          profileMode: 'closed',
          statusMessage: STATUS_UNAVAILABLE,
          statusUnavailable: true,
          profileLoadFailed: true
        });
      });
  },

  onField(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ ['form.' + field]: e.detail.value });
  },

  selectGender(e) {
    this.setData({ 'form.gender': e.currentTarget.dataset.v });
  },

  onBirth(e) {
    this.setData({ 'form.birth': e.detail.value });
  },

  onBlood(e) {
    const i = Number(e.detail.value);
    this.setData({ bloodIndex: i, 'form.blood': BLOOD_TYPES[i] || '' });
  },

  onSport(e) {
    const i = Number(e.detail.value);
    this.setData({ sportIndex: i, 'form.sport': SPORT_FREQ[i] || '' });
  },

  toggleChronic(e) {
    const tag = e.currentTarget.dataset.tag;
    const cur = this.data.form.chronicTags.slice();
    const idx = cur.indexOf(tag);
    if (idx > -1) {
      cur.splice(idx, 1);
    } else {
      cur.push(tag);
    }
    this.setData({ 'form.chronicTags': cur });
  },

  onSmoke(e) {
    this.setData({ 'form.smoke': e.detail.value });
  },

  onDrink(e) {
    this.setData({ 'form.drink': e.detail.value });
  },

  save() {
    if (this.data.profileMode !== 'demo') {
      wx.showToast({ title: '健康档案暂不可填写', icon: 'none' });
      return;
    }
    if (this.data.saving) return;
    const f = this.data.form;
    if (!f.name.trim()) {
      wx.showToast({ title: '请填写姓名', icon: 'none' });
      return;
    }
    if (!f.birth) {
      wx.showToast({ title: '请选择出生年月', icon: 'none' });
      return;
    }
    if (f.height && (!/^\d{1,3}(\.\d)?$/.test(f.height) || Number(f.height) <= 0 || Number(f.height) > 300)) {
      wx.showToast({ title: '请输入正确的身高（cm）', icon: 'none' });
      return;
    }
    if (f.weight && (!/^\d{1,3}(\.\d)?$/.test(f.weight) || Number(f.weight) <= 0 || Number(f.weight) > 500)) {
      wx.showToast({ title: '请输入正确的体重（kg）', icon: 'none' });
      return;
    }
    this.setData({ saving: true });
    api
      .saveProfile({
        name: f.name.trim(),
        gender: f.gender,
        birth: f.birth,
        height_cm: f.height ? Number(f.height) : null,
        weight_kg: f.weight ? Number(f.weight) : null,
        blood: f.blood || null,
        chronic_tags: f.chronicTags,
        allergy: f.allergy.trim() || null,
        surgery_history: f.surgery.trim() || null,
        smoke: f.smoke,
        drink: f.drink,
        sport_freq: f.sport || null
      })
      .then(() => {
        this.setData({ saving: false });
        wx.showToast({ title: '演示档案已保存', icon: 'success' });
        this.backTimer = setTimeout(() => {
          wx.navigateBack();
        }, 1200);
      })
      .catch(() => {
        this.setData({ saving: false });
      });
  }
}, 'profile-edit'));
