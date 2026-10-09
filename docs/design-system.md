# 设计系统（C端品牌 · 前端/小程序统一规范）

品牌：**杏临汇**。职业健康科普作品征集网页与健康小程序分别适配浏览器和微信场景，但共用可信、清晰的健康服务表达。旧文学 H5 的视觉规范已下线，不适用于当前作者网页。

## 1. 职业健康征集网页（essay-h5）

以 `essay-h5/src/occupational.css` 为实现依据：暖白纸底 `#F5F7F4`、深青文字 `#17312E`、主操作绿 `#146B58`、深绿 `#10483E`、柔和分割线 `#DCE5DE`、少量杏金 `#D7AD68`。正文使用系统中文无衬线字体，重要展示字可用宋体。

- 首页依次展示活动主题、征集方向、作品格式、评审安排与投稿入口；日期仅标注评审安排，不能推断投稿截止日。
- 投稿页按作品信息、作者联系信息、文件上传与资料使用确认分组；仅服务器返回回执后显示提交成功。
- 页面在手机和桌面上保持明确的层级、可读字号、可见焦点及清楚的错误反馈；关闭投稿时说明状态并禁用提交。
- 当前活动不公开展示投稿作品和作者联系方式；历史文学活动页面及入口已移除。

## 2. 微信小程序（miniprogram）· 杏临汇品牌（2026-09 起）

本端为**杏临汇·全域健康管家**（依据商业计划书），品牌走专业医疗健康风：深青主色 + 杏金点缀（呼应「杏临」），干净、可信、有温度。

### 基础
- 750rpx = 屏宽；8rpx 间距基线；卡片内边距 32rpx×24rpx；圆角 12/16/24rpx；触控目标 ≥88rpx。
- 页面标题 40rpx/700；区块标题 32rpx/600；卡片标题 30rpx/600；正文 28rpx；次要 26rpx；注释 22rpx。

### 色板（杏临汇·健康品牌）
```
--brand:        #0F766E;   /* 深青（主行动/选中/tab 激活） */
--brand-deep:   #0B5A54;   /* 深青按下态/渐变起点 */
--brand-light:  #14B8A6;   /* 亮青（渐变终点/数字强调） */
--brand-bg:     #E6F2F0;   /* 青浅底（标签/选中底） */
--apricot:      #D97706;   /* 杏金点缀（角标/运营位，慎用） */
--apricot-bg:   #FBF3DB;
--text-1:  #1F2937;  --text-2: #6B7280;  --text-3: #9CA3AF;
--border: #E5E7EB;  --divider: #F0F1F2;
--bg-page: #F4F7F6;  --bg-card: #FFFFFF;  --bg-input: #F3F6F5;
--success: #15803D/#EAF6EE;  --info: #1D6FB8/#EAF3FC;  --warn: #B45309/#FBF3DB;  --danger: #B42318/#FDECEA;
```

### 组件规范
```css
/* 卡片：白底 20rpx 圆角，1rpx 极浅边或超淡阴影 */
.card { background:#fff; border-radius:20rpx; padding:32rpx;
        border:1rpx solid #EDF0EF; }

/* 主按钮：品牌深青（可 brand-deep→brand-light 渐变），按压 scale(0.97) */
.btn-primary { background:#0F766E; color:#fff; border-radius:16rpx;
               height:96rpx; line-height:96rpx; font-size:30rpx; font-weight:600; }

/* 标签：小胶囊，语义浅底深字；在线状态用绿点+「在线」 */
.tag { padding:6rpx 18rpx; border-radius:999rpx; font-size:22rpx; font-weight:600; }
.tag-brand { background:#E6F2F0; color:#0F766E; }

/* 九宫格场景入口：3 列，图标 72rpx + 名称 26rpx，白卡承载 */
/* 服务卡片：名称/类型标签/价格（分转元，¥xx）/销量/匹配人数 */

/* 咚咚聊天气泡：user 右侧深青底白字，provider 左侧白底深字，system 居中灰字 22rpx */
```

### 动效
- 列表项进场：300ms ease-out + 80ms stagger；
- 按钮按压 `scale(0.97)` 150ms；
- 发送消息后「对方正在输入…」typing 态（前端 1.2s 模拟）再出 provider 回复。

### 医疗合规呈现（硬要求）
- 首页与「我的」页脚固定免责声明（见 api.md 第二部分·九）。
- 人员卡片/详情展示职称·执业单位·从业年限，附「平台已核验执业资质」徽标。
- AI 医师助手入口标注「健康信息参考，不构成诊疗建议」。

### 状态完备
所有异步页必须具备 loading / 空态（插画+CTA）/ 错误重试 / 下拉刷新；表单按钮有 loading 态防重复提交。

## 3. 管理后台（admin-web）

- shadcn/ui 默认 zinc 中性体系，亮色内容区 + 深色侧边栏（`zinc-950`），主行动按钮朱砂色 `#9F2F2D`（与 C 端品牌呼应）。
- 中文界面；表格用 TanStack Table（列头排序、状态筛选、分页器）；危险操作二次确认 Dialog。
- 状态色：待审 amber、通过 green、驳回 red、下架 zinc，用 Badge 呈现。
