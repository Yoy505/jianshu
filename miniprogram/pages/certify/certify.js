// 入驻申请接口、隐私告知与资质审核流程尚未接通，不收集无法提交的个人资料。
Page(require('../../utils/hifi-page')({
  backHome() {
    wx.switchTab({ url: '/pages/index/index' });
  }
}, 'certify'));
