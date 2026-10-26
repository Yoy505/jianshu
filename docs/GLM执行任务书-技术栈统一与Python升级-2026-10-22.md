# GLM 技术栈统一与 Python 升级执行任务书

日期：2026年10月22日。项目目录：`/Users/huo/Desktop/jianshu`。核对基线：`main`提交`1f053c9`，以及当前工作区中正在推进的工作一增量修改。本文件交付的是技术整改范围；源码和运行环境尚未按本书升级。

请统一三个Web项目的技术约定，将后端迁移到执行时经官方确认的最新Python稳定版，建立真实PostgreSQL、Redis和Celery验证，并保持微信小程序使用原生技术栈。业务功能继续由工作一、工作二任务书负责，本次不借技术升级改动商品、退款、授权或服务承诺。

## 一 用户指定的技术标准

| 范围 | 统一要求 |
|---|---|
| Web前端 | React、Vite、shadcn/ui、Zustand、TanStack Query、TanStack Table |
| 后端 | FastAPI、PostgreSQL、Redis、Celery、venv、Python最新稳定版 |
| 微信小程序 | 原生JavaScript、WXML、WXSS、JSON，使用App、Page、Component和原生wx API |

Web范围包含`admin-web`、`essay-h5`和`volunteer-h5`。React/Vite为运行与构建基础，shadcn为公共交互组件来源，Zustand负责跨组件或跨页面客户端状态，TanStack Query负责服务端数据。TanStack Table为需要表格的页面提供统一表格状态与行为；无表格的H5不制造演示表格或空引用来证明使用，对该项标明不适用。

已有TypeScript、Tailwind、React Router、SQLAlchemy、Alembic及asyncpg可以继续使用，它们是现有架构的组成部分，与上述技术要求兼容。不得为这次任务引入替代React/Vite或venv的新主框架。

## 二 当前事实与差异

### 1 Web项目

| 项目 | 当前已使用 | 待统一内容 |
|---|---|---|
| admin-web | React、Vite、Zustand登录状态、TanStack Query，以及部分页面的TanStack Table；13个本地UI组件，使用Radix和Tailwind | 补shadcn CLI管理配置，梳理复杂表格，完善状态职责和退出缓存行为 |
| essay-h5 | React、Vite、Tailwind、TanStack Query | 缺shadcn组件体系和Zustand；梳理直接请求与Query管理；表格按实际需求处理 |
| volunteer-h5 | React、Vite、React Router、局部useState及直接fetch、自定义CSS | 补shadcn及其样式基础、Zustand和TanStack Query，保留已有报名流程及视觉效果 |

三项目当前均没有`components.json`。后台已有本地组件源码，不能据此认定没有使用shadcn；该文件在手工复制组件模式下是可选的，使用CLI新增组件时才需要。此次将CLI配置作为维护规范补齐。[shadcn官方说明](https://ui.shadcn.com/docs/components-json)

后台已有六个页面实际使用TanStack Table；交易订单、积分等页面主要使用本地Table显示组件。两者可以组合：shadcn提供表格样式和结构，TanStack Table提供列、排序、筛选、选择及分页行为。不是把Table标签机械替换成另一套外观。

### 2 后端与运行环境

| 要求 | 当前状态 | 差异 |
|---|---|---|
| FastAPI | 本地venv安装0.141.1，有真实FastAPI入口 | 架构符合，升级Python后需重新验证依赖 |
| PostgreSQL | 默认连接为postgresql+asyncpg，Compose为postgres:16-alpine | 架构符合，现有公共测试夹具主要强制使用SQLite |
| Redis | 已有真实Redis客户端；Compose为redis:7-alpine | 架构符合，现有公共测试使用FakeRedis，不能代替真实Redis集成 |
| Celery | 本地venv安装5.6.3，已有worker、beat、导出和统计任务 | 已实现，仍需真实broker/worker验证和统一运行说明 |
| venv | backend/.venv是真实虚拟环境 | 符合，Python跨版本升级需要新建venv |
| Python | backend/.venv实际3.12.8；README和部署文档使用3.13 | 不符合最新稳定版要求，环境和文档也不一致 |
| 依赖可复现 | requirements.txt主要采用版本下限；没有完整Python锁文件 | 补经目标Python验证的精确依赖和安装检查 |

截至本任务书日期，官方发布页列出的Python稳定维护版为3.15.1，发布于2026年10月21日。[Python官方发布页](https://www.python.org/downloads/release/python-3151/)

执行时重新核对官方稳定版，排除alpha、beta、rc等预发布。若执行日期已有新稳定补丁，记录核对日期、官方链接和本次采用的确切版本，再同步版本文件。不能把“3.13+”或任意系统python3当成已满足最新稳定版。

### 3 原生微信小程序

`miniprogram`使用App、Page、Component、WXML/WXSS/JS/JSON及wx.request、wx.login等原生API，符合要求。已有hifi-page包装属于项目内部原生页面封装，不等于跨端框架。保持原生实现，不迁移到Taro、uni-app、React或Vue小程序方案。

## 三 执行边界和顺序

执行前阅读有效`AGENTS.md`、`docs/GLM-开发规范.md`、`README.md`、`docs/api.md`、两份已拆分的工作一/工作二执行任务书，以及相关代码与测试。

当前工作区已有工作一源码、迁移、测试及页面改动。先记录当前提交、git status和相关diff，保留全部无关修改与私有资料。不可通过reset、清理目录、批量格式化、删除lock文件或覆盖旧venv来获得“干净环境”。共享文件先读完整差异，再增量修改。

| 批次 | 目标 | 交付结果 |
|---|---|---|
| T0 | 核对基线、依赖和技术分工 | 实际版本、源码使用点、差异表、逐文件范围及回归计划 |
| T1 | 新Python与依赖验证 | 新建目标版本venv，依赖可安装、导入、pip check和后端回归通过 |
| T2 | 后台shadcn和表格规范 | 工具配置可用，现有组件和复杂表格符合统一约定 |
| T3 | 投稿H5与志愿者H5统一 | 组件、客户端状态、请求和缓存责任清楚，原流程真实可用 |
| T4 | PostgreSQL、Redis和Celery集成 | 独立测试环境真实连接、任务与故障恢复证据 |
| T5 | 统一版本、启动和交付 | 版本声明、锁文件、文档、启动方式及全部回归结果一致 |

每批形成可独立审查的增量。Python兼容验证不通过时记录确切依赖和错误，继续不依赖它的Web任务；不得静默退回旧Python后宣布本项达标。生产发布、线上迁移、真实微信交易/退款和真实消息/外呼须另行确认具体环境与操作范围。

## 四 T1 Python与venv升级

1. 读取现有`backend/.venv/pyvenv.cfg`和实际解释器版本，记录主要依赖；现有venv继续保留用于对照与回退。
2. 安装或使用官方最新稳定版解释器，在独立目录创建venv，例如`backend/.venv-py315`。不能把旧venv复制过去，也不能只修改pyvenv.cfg伪装版本已升级；新目录按项目规则忽略，不提交环境和密钥。
3. 使用新venv的`python -m pip`安装受控依赖，完成`pip check`及关键模块导入。对asyncpg、cryptography、argon2等含本地扩展的依赖，验证目标操作系统/架构上的实际安装和导入；macOS成功不能直接代表生产Linux成功。
4. 对兼容问题优先在现有架构内更新必要依赖，逐项记录原因与变化。若关键依赖暂不支持目标解释器，说明具体阻断和可行选项，不能降低目标版本后隐藏差异。
5. 增加明确的解释器版本声明，例如`backend/.python-version`写本次采用的确切版本；可在`pyproject.toml`中声明兼容范围，但不要求引入Poetry或uv替代venv。版本范围和部署所用确切版本分别记录，不能只写latest。
6. 将Makefile及部署指令改为可显式选择解释器和venv路径，并有版本检查；避免泛用python3意外创建旧版环境。切换实际服务前完成目标venv验证和可执行回退说明。

主要范围：`backend/requirements.txt`、新增依赖锁文件/版本声明、`Makefile`、必要的后端兼容修改、`README.md`、`docs/deploy.md`及相关`deploy`脚本。不要为Python升级无关重写API、数据模型、权限或业务状态机。

验收：新venv报告官方目标稳定版本，sys.prefix与sys.base_prefix证明使用venv，pip check和真实导入无错；后端完整测试通过，PG/Alembic及Celery集成在目标解释器运行。原文3.13与实际3.12.8的冲突在文档中得到统一纠正。

## 五 依赖与前端版本管理

三个Web继续使用现有npm和package-lock管理依赖。按各项目兼容性确定React、Vite、TypeScript、Query、Zustand和Table的共同版本约定；用户没有要求所有前端依赖追最新版，不要为对齐技术名词批量跨大版本升级。

修改package.json时同步正确更新package-lock，使用干净临时目录或可回退方式验证npm ci。禁止删除锁文件后任意重新解析所有版本；不能仅新增依赖而不迁移实际代码。记录manifest范围、lock精确版本和实际运行版本的区别。

后端保留易维护的直接依赖清单，并增加目标Python/平台适用的精确锁定方案，例如生产和测试的锁文件；形式可按项目工具选择，但文档必须能复现安装。生产依赖与pytest/aiosqlite等测试依赖分别处理。锁文件须来自验证后的解析结果，不能手写猜测版本。

验收：本地重新安装与目标部署环境可复现；pip check、npm ci、类型检查和构建正常；没有为了静态通过而忽略依赖错误、关闭类型检查或大面积使用any。

## 六 T2 管理后台统一

### 1 shadcn组件管理

核对现有`src/components/ui/`、Radix、Tailwind 4、Vite插件、路径别名和主题变量，补正确的`components.json`及维护说明。保留现有组件定制和视觉效果，不执行会批量覆盖组件的初始化选项。验证CLI配置能定位现有目录并按当前主题生成所需组件；无需为验收额外添加无用页面或演示组件。

### 2 状态与服务端数据

保留Zustand认证store，审查其职责和持久化策略。客户端跨页面状态可放store，组件局部开关/输入可继续useState，服务端订单/积分/名单等由Query管理，避免把同一份服务端数据再复制为第二套Zustand账本。

Query key包含影响返回结果的身份、岗位、筛选和分页；mutation成功按业务失效相关查询。退出、换号、封禁或权限变化清理私有Query缓存和相关store；旧响应不得回写新账号。保留现有登录与刷新契约，不借此重建认证流程。

### 3 TanStack Table

梳理已用TanStack Table的六个页面和交易列表，定义共同列、排序、筛选、分页、行选择及空/错/加载展示约定。复杂管理列表使用TanStack Table管理行为，复用shadcn本地Table结构和样式；保留简单详情表的合理实现，记录适用范围。

服务端分页页面使用手动分页/筛选/排序及正确总数，不得只排序当前页却向用户表示已排序全量数据。批量操作只处理当前允许的数据范围，权限仍由后端控制。

范围：`admin-web/package*.json`、新增`components.json`、`vite.config.ts`、`src/index.css`、`src/components/ui/`、公共表格组件、`src/stores/auth.ts`、`src/main.tsx`及受影响页面。交易页面正在修改，必须先对照已有diff。

## 七 T3 两套H5统一

### 1 投稿H5

沿用现有React/Vite/Tailwind/Query，在不改变投稿业务规则与页面内容的前提下，将公共按钮、输入、弹窗、提示等逐步统一到本项目shadcn组件。补路径别名、主题与CLI配置，兼容当前Tailwind/Vite设置。

将确有跨组件/页面共享的投稿流程或草稿状态放Zustand；文件对象、上传进度和只在当前组件使用的输入可按职责保留局部状态。敏感稿件、手机号及附件内容不因技术统一而新增持久化。远程元数据、能力、回执/进度等采用Query/Mutation统一错误和失效策略，上传进度保留真实传输依据。

范围：`essay-h5/package*.json`、`components.json`、必要的`vite.config.ts`及样式、`src/components/`、新增`src/stores/`、`src/App.tsx`、相关投稿/回执页面及API层。

验收：投稿、重复提交保护、错误恢复、文件上传进度、回执查询及刷新恢复行为不变；移动端和桌面浏览器均可操作，无横向溢出。无真实表格需求时Table标为不适用，不生成空表格。

### 2 志愿者H5

新增并实际接入QueryClientProvider，将能力加载与报名提交纳入Query/Mutation，统一请求错误及缓存策略；明确副作用mutation不自动重试，保留既有服务端幂等和重复提交保护。不能因数据层迁移扩大手机号等采集字段或改变同意逻辑。

用Zustand组织确有共享需求的说明页到报名页状态、草稿与回执流程；保留现有必要的刷新恢复，明确敏感字段与本地持久化边界。局部校验、弹窗和输入焦点可继续useState。补shadcn及所需Tailwind/Vite基础，避免样式预设或reset破坏现有表单与品牌视觉。

范围：`volunteer-h5/package*.json`、`components.json`、`vite.config.ts`、`src/main.tsx`、`src/useApplication.ts`、`src/application.ts`、`src/ApplyPage.tsx`、`src/InfoPage.tsx`、必要的store/组件及样式、现有tests。

验收：说明页到填报、必填/格式校验、取消同意、重复点击、断网重试、失败不伪成功、回执及子路径部署均有回归；保留报名数据最小化和当前授权边界。无表格需求时Table标为不适用。

## 八 T4 真实PostgreSQL Redis和Celery验证

### 1 PostgreSQL

保持PostgreSQL为目标业务数据库，SQLAlchemy/asyncpg/Alembic继续使用。用户未指定PG必须追最新版，本次不强制升级现有PostgreSQL 16；数据库大版本升级单独评估。

现有`backend/tests/conftest.py`在导入前强制写SQLite连接，不能只在命令行传PG地址就声称已测PG。建立明确的PG测试夹具或独立集成测试入口，确保不会被SQLite覆盖，并有防误连生产的保护。

在隔离临时库上验证新增迁移、存量兼容、外键/唯一约束、行锁、多连接事务与并发扣分/退款/批次；只用本次独立测试数据，不修改正在开发的数据或生产库。SQLite快速测试可以保留，不能替代PG集成结论。

### 2 Redis

保留当前真实客户端和Redis 7配置。新增隔离真实Redis验证：鉴权/验证码或限流涉及的命令、TTL、并发原子性、连接恢复以及Celery broker交互。使用独立测试实例或受控命名空间，只清理本次测试键，不对共享实例执行全库清空。FakeRedis仍可用于快速单元测试。

### 3 Celery

使用真实Redis broker/result backend启动worker与必要beat，验证至少一个真实任务的提交、执行、结果读取和失败状态，覆盖任务重复、worker重启或超时后的受控恢复。选择不会发送短信、付款或外呼的测试任务，不能把mock或always_eager结果写成跨进程验证。

依现有架构提供可复现的API、worker、beat、PG和Redis启动说明。当前Compose仅含PG/Redis，可以通过增量Compose、Makefile或已有进程管理方案补统一入口；不要为技术栈核对引入全新的生产平台。工作二尚未实现的履约任务仍在工作二范围内，本任务验证基础设施及已有任务，不宣称其已完成。

范围：`backend/tests/conftest.py`及新增隔离集成夹具/测试、`app/db/session.py`必要兼容修改、`app/core/config.py`、`app/core/deps.py`、`app/workers/`、`docker-compose.yml`、`Makefile`、`backend/.env.example`、`docs/deploy.md`及相关部署清单。

## 九 原生微信小程序约束

保留当前原生页面、组件及API封装；本次不将Web的shadcn、React、Zustand或TanStack库强行接入小程序。小程序状态、请求及UI继续按原生方式和既有接口实现。

后端升级后验证微信登录、手机号核验、返回入口、订单/积分/权益及授权页面的API兼容；不自动重放支付、预约或发消息等用户副作用。运行相关JS/JSON/WXML与既有hifi/access检查，并在可用开发者工具/真机验证。保持原生技术栈与业务兼容分别记录，静态检查不能替代真机结论。

## 十 验收矩阵

| 项目 | 通过条件 |
|---|---|
| Python版本 | 执行时官方最新稳定版，有来源与日期；venv实际解释器和版本声明一致 |
| Python依赖 | 新venv可复现安装、pip check和关键导入通过；目标部署系统兼容；完整后端回归有记录 |
| 文档和启动 | README、部署、Makefile及版本文件一致，不再用系统python3误建旧环境 |
| shadcn | 三Web的组件目录、主题、别名和CLI配置准确；现有组件定制保留；真实交互已使用 |
| Zustand | 有明确客户端共享状态职责；局部状态合理；无服务端账本双份维护或新增敏感持久化 |
| TanStack Query | 三Web实际接入；身份及筛选缓存键准确，mutation失效正确，退出/换号不泄露旧数据 |
| TanStack Table | 有需求的复杂表格统一行为；服务端分页排序语义正确；无需求页面明确不适用 |
| H5业务回归 | 投稿上传/进度/回执、志愿者两级报名/同意/失败恢复、子路径及移动适配正常 |
| PostgreSQL | 隔离真实库迁移、兼容、约束及多连接并发通过；SQLite结果单列 |
| Redis | 真实实例命令/TTL/原子性/连接验证通过，测试键隔离；FakeRedis结果单列 |
| Celery | 真实broker和跨进程worker完成任务及失败恢复验证；beat运行方式明确 |
| 小程序 | 原生架构保留，关键接口兼容，静态及可用真机检查如实记录 |

Web分别运行现有类型检查/构建命令，后台执行`npm run typecheck`及`npm run build`，两套H5执行`npm run build`，志愿者H5执行已有`npm test`；增加针对缓存隔离、重复提交和状态迁移的必要回归。使用目标新venv运行后端受影响测试和完整pytest，再执行独立PG/Redis/Celery集成入口。

涉及UI的变化在浏览器检查加载、空、错误、无权限、重试、长文本及窄屏，留下脱敏证据；不要仅凭构建成功宣布页面可用。不得为了通过而关闭检查、吞掉异常或伪造任务成功。

## 十一 交付文件和启动指令

每批将实际记录追加至`docs/GLM交付记录-技术栈统一-2026-10-22.md`，包括：

1. 执行时Git状态、起止提交、保留的已有改动与本批逐文件清单。
2. 标准、当前差异、实际迁移范围和三个Web的组件/状态/请求/表格使用位置。
3. 官方Python版本核对日期与链接、采用版本、新旧venv路径、依赖兼容结果和锁定方式。
4. 实际命令及输出摘要、目标系统、PG/Redis/Celery真实运行证据和页面证据；失败或未运行项准确说明。
5. 环境变量、启动与部署变化、数据兼容、回退办法、未解决风险及下一独立任务。

本任务书是公共技术标准；工作一和工作二的业务范围保持独立。共享文件由执行者协调增量修改，各自交付记录引用本任务的技术验证结果，不能互相覆盖未提交改动。

**给GLM的启动指令：请在当前jianshu仓库按本任务书执行技术栈统一。先核对工作区和现有增量、依赖及官方Python稳定版，报告文件范围与验证方案后依次实施T1至T5。后端使用新建venv完成目标Python兼容验证；三Web统一React/Vite、shadcn、Zustand和Query，Table按真实表格需求接入；完成真实PG/Redis/Celery集成，保持原生小程序。保留现有业务、设计与用户修改，不以mock、空依赖或文档改字代替实现；每批提交实际证据与回退说明。**
