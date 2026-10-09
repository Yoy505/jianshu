"""仅供本地开发/测试的杏临汇演示数据初始化脚本：python -m app.seed。

建表兜底走 Base.metadata.create_all；生产必须用 alembic upgrade head。
初始化管理员、一个健康域演示用户及小程序演示数据，保持幂等。
历史文学活动数据不删除，也不再由本脚本新建。
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models import AdminUser, User
from app.models.mp import (
    MpAppointment,
    MpConsult,
    MpConsultMessage,
    MpDrug,
    MpNotice,
    MpProduct,
    MpProfile,
    MpProvider,
    MpProviderService,
    MpReview,
    MpScenario,
    MpService,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_demo_seed_allowed() -> None:
    if settings.APP_ENV not in {"dev", "test"}:
        raise RuntimeError("演示种子仅可在 dev/test 环境运行，禁止写入生产或预生产数据库")


async def seed() -> None:
    """只初始化后台账号和杏临汇健康域演示数据，不再创建旧文学活动。"""
    _ensure_demo_seed_allowed()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        existing_admins = await db.scalar(select(func.count()).select_from(AdminUser))
        if not existing_admins:
            db.add_all([
                AdminUser(
                    username="admin", name="系统管理员",
                    password_hash=hash_password("admin123"), role="admin",
                ),
                AdminUser(
                    username="editor", name="内容编辑",
                    password_hash=hash_password("editor123"), role="editor",
                ),
            ])
            await db.flush()

        demo_user = (
            await db.execute(select(User).where(User.phone == DEMO_MP_PHONE))
        ).scalar_one_or_none()
        if demo_user is None:
            db.add(User(nickname="杏临汇演示用户", phone=DEMO_MP_PHONE, status="active"))
        await db.commit()
        await seed_mp_data(db, verbose=True)

        print("杏临汇本地演示数据初始化完成")
        print("后台账号：admin / admin123；editor / editor123（仅首次初始化时创建）")
        print(f"健康域演示用户：{DEMO_MP_PHONE}")


# ===================== 健康域（杏临汇小程序）种子 =====================

DEMO_MP_PHONE = "13800000001"

# (key, name, tagline, sort)
MP_SCENARIOS = [
    ("checkup", "体检慢病", "报告逐项讲明白，慢病有人管到底", 1),
    ("consult", "看病问诊", "服务流程演示，真人接诊尚未开放", 2),
    ("medication", "合理用药", "一支药师团队，守好你的药箱子", 3),
    ("rehab", "术后康复", "出院不是终点，康复才是新生", 4),
    ("diet", "饮食调理", "吃对每一餐，把健康慢慢吃回来", 5),
    ("emotion", "失眠情绪", "睡个好觉，从被好好倾听开始", 6),
    ("disable", "失能照护", "专业照护进家门，家人不再独自硬扛", 7),
    ("escort", "就医陪同", "从挂号到取药，全程有人陪", 8),
    ("mother-baby", "母婴·老年", "一老一小，全家的安心管家", 9),
]

# (scenario_key, service_type, name, desc, price分, unit, duration_min, sales)
MP_SERVICES = [
    (
        "consult", "online", "全科医师在线问诊",
        "由三甲全科主任医师在线接诊，覆盖常见病初步判断、慢病复诊咨询与就医路径规划，"
        "20 分钟一对一沟通，出具图文健康建议。",
        3900, "次", 20, 986,
    ),
    (
        "checkup", "online", "体检报告一对一解读",
        "上传体检报告，医师逐项解读异常指标，评估慢病风险，给出复查计划与生活方式建议，"
        "告别「指标看不懂、上网瞎百度」。",
        2900, "次", 25, 743,
    ),
    (
        "medication", "online", "健康问答演示·合理用药",
        "当前仅提供系统自动回执，用于演示咨询流程；尚未接入 AI 医师、真人药师复核或个体化用药建议。",
        1, "次", 15, 1286,
    ),
    (
        "medication", "online", "临床药师用药审方指导",
        "副主任药师一对一审方：多重用药重整、药物相互作用排查、肝肾功能剂量调整建议，"
        "适合长期服用 5 种以上药品的人群。",
        4900, "次", 20, 512,
    ),
    (
        "rehab", "offline", "康复治疗师上门指导",
        "骨科术后、卒中后康复治疗师上门评估并制定居家训练方案，现场指导动作矫正，"
        "附带训练计划图文与家属护理要点。",
        26800, "次·上门", 60, 634,
    ),
    (
        "diet", "online", "注册营养师定制食谱",
        "注册营养师结合体检指标与饮食习惯，输出七天定制食谱与外卖/食堂替代方案，"
        "覆盖三高、减重、术后膳食等场景。",
        5900, "次", 30, 428,
    ),
    (
        "emotion", "online", "心理咨询师线上倾听",
        "国家认证心理咨询师 50 分钟结构化倾听，缓解失眠、焦虑与情绪困扰，"
        "首次咨询附赠睡眠卫生评估报告。",
        9900, "次", 50, 386,
    ),
    (
        "disable", "offline", "执业护师上门护理",
        "主管护师上门提供换药拆线、压疮护理、导管维护、肌注采血等专业护理，"
        "出具护理记录并可同步家属。",
        15800, "次·上门", 60, 872,
    ),
    (
        "disable", "offline", "失能长者全天居家照护",
        "持证照护师提供日间 8 小时生活照护：协助进食翻身、康复期活动陪伴、异常情况预警，"
        "支持按周预订。",
        38800, "天", 480, 291,
    ),
    (
        "escort", "offline", "全程陪诊服务",
        "资深陪诊师全程陪同就医：提前取号、诊室内外协助沟通、检查引导、代取报告与用药交代，"
        "让老人独自就医不再难。",
        19800, "半天", 240, 1120,
    ),
    (
        "mother-baby", "third", "母婴护理机构·产后康复套餐",
        "合作母婴机构产后 42 天康复评估与 10 次康复理疗，含盆底肌评估、腹直肌分离修复"
        "与哺乳期营养指导。",
        129900, "次", 60, 205,
    ),
    (
        "checkup", "third", "知名体检中心尊享套餐",
        "合作三甲体检中心深度体检：肿瘤标志物筛查、颈动脉彩超、低剂量肺部 CT 等升级项目，"
        "报告回传平台由医师二次解读。",
        89900, "次", 240, 368,
    ),
]

# 人员档案（name, gender, title, dept, org, years, skills, intro, rating, service_count, online）
MP_PROVIDERS = [
    (
        "陈景明", "男", "主任医师", "全科医学科", "滨海市第一人民医院", 22,
        ["慢病管理", "老年共病", "家庭医生签约", "就医路径规划"],
        "从医 22 年，擅长高血压、糖尿病等慢病的长期管理与多病共存患者的综合评估。"
        "坚持「先听患者把话说完」，累计服务患者逾 3 万人次，主持社区健康讲座百余场。",
        4.9, 1863, True,
    ),
    (
        "林婉如", "女", "副主任药师", "药学部", "滨海市第一人民医院", 16,
        ["用药审方", "药物重整", "慢病用药", "抗凝管理"],
        "临床药学硕士，16 年三甲医院药学部经验，专注重度用药患者的人文关怀与多重用药重整，"
        "年均审方逾万条，发起「社区药箱安全行动」公益项目。",
        4.9, 1204, True,
    ),
    (
        "周秀兰", "女", "主管护师", "护理部", "滨海市第一人民医院", 12,
        ["伤口造口护理", "居家护理", "静脉导管维护", "压疮防治"],
        "国际造口治疗师认证，12 年外科与社区护理经验，擅长术后伤口、造口与长期卧床患者的"
        "居家护理指导，服务过的家庭都亲切地叫她「周老师」。",
        4.8, 986, True,
    ),
    (
        "吴铁军", "男", "康复治疗师", "康复医学科", "滨海康复医院", 10,
        ["骨科术后康复", "卒中康复", "居家康复训练", "步态矫正"],
        "康复治疗学学士，10 年康复科经验，累计指导术后与神经康复患者两千余人次。"
        "坚信「康复的终点是回归生活」，方案务实、动作讲解细致。",
        4.9, 1357, True,
    ),
    (
        "苏晴", "女", "心理咨询师", "心理科", "杏临心理健康中心", 9,
        ["睡眠困扰", "情绪疏导", "认知行为疗法", "职场减压"],
        "国家二级心理咨询师，认知行为治疗取向，累计个案 3000+ 小时。"
        "风格温和克制，擅长帮助来访者理解失眠与情绪背后的信号。",
        4.8, 873, True,
    ),
    (
        "郑楚楚", "女", "注册营养师", "临床营养科", "滨海市第一人民医院", 8,
        ["慢病营养", "体重管理", "术后膳食", "老年营养"],
        "注册营养师，医学营养学硕士，擅长把营养学翻译成「今天吃什么」的具体清单，"
        "为三高、术后及老年人群定制可坚持的饮食方案。",
        4.9, 651, True,
    ),
    (
        "罗大成", "男", "陪诊师", "客户服务部", "杏临汇陪诊中心", 6,
        ["全程陪诊", "代取报告", "就医规划", "异地就医协助"],
        "6 年陪诊经验，跑遍滨海市 23 家三甲医院，熟悉各院科室布局与放号规律。"
        "话不多但事事有着落，老人们都说「有小罗在，心里踏实」。",
        4.7, 1520, False,
    ),
    (
        "何静", "女", "母婴健康师", "妇幼儿保科", "杏临母婴护理中心", 11,
        ["产后康复", "新生儿护理", "母乳喂养指导", "婴幼儿辅食"],
        "高级母婴护理师与催乳师双认证，11 年母婴护理经验，入户服务超 900 个家庭，"
        "擅长把新手爸妈的焦虑变成可上手的日常照护动作。",
        4.9, 1092, True,
    ),
]

# 人员 -> 服务挂接（人员序号, [服务序号]）
MP_PROVIDER_SERVICE_LINKS = [
    (0, [0, 1, 11]),   # 陈景明：全科问诊 / 报告解读 / 体检套餐顾问
    (1, [2, 3]),       # 林婉如：AI 助手复核 / 药师审方
    (2, [7, 8]),       # 周秀兰：上门护理 / 居家照护
    (3, [4]),          # 吴铁军：康复上门
    (4, [6]),          # 苏晴：心理倾听
    (5, [5]),          # 郑楚楚：营养食谱
    (6, [9]),          # 罗大成：陪诊
    (7, [10]),         # 何静：产后康复套餐
]

MP_DRUGS = [
    {
        "common_name": "阿司匹林肠溶片", "brand_name": "拜阿司匹灵", "form": "肠溶片",
        "manufacturer": "拜耳医药保健有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于降低急性心肌梗死疑似患者的发病风险、预防心肌梗死复发、卒中的二级预防，以及降低短暂性脑缺血发作及其继发卒中的风险。",
        "usage_note": "饭前用适量温开水送服；用于降低心血管风险时一般每日一次，常见维持剂量为每次 75-100mg，具体剂量与疗程务必遵医嘱，不可自行增减或停药。",
        "contraindication": "对阿司匹林或其他水杨酸类药物过敏者、活动性消化道溃疡或出血者、严重肝肾功能不全者、血友病及出血体质者禁用；孕晚期妇女禁用。",
        "science": "阿司匹林是沿用超过百年的老药，小剂量时通过抑制血小板合成血栓素 A2，让血小板不易「抱团」形成血栓，因此被广泛用于心脑血管疾病的二级预防。抑制血小板的同时也会带来出血风险，与布洛芬等非甾体抗炎药同服时风险更高，需间隔用药或遵医嘱更换镇痛方案。长期服用者若出现黑便、牙龈异常出血或皮肤瘀斑，应及时就医排查。它属于处方药，是否需要长期服用应由心内科医生评估，切勿自行当作「保健药」使用。",
        "initial": "A",
    },
    {
        "common_name": "对乙酰氨基酚片", "brand_name": "泰诺林", "form": "片剂",
        "manufacturer": "上海强生制药有限公司", "tags": ["otc", "essential", "insurance"],
        "indication": "用于普通感冒或流行性感冒引起的发热，也可用于缓解轻至中度疼痛，如头痛、关节痛、偏头痛、牙痛、肌肉痛、神经痛及痛经。",
        "usage_note": "成人一次 0.5g，若持续发热或疼痛可间隔 4-6 小时重复用药一次，24 小时内不超过 4 次；用于退热一般不超过 3 天，用于镇痛不超过 5 天，症状不缓解请就医。",
        "contraindication": "对本品过敏者、严重肝肾功能不全者禁用；服药期间禁止饮酒或饮用含酒精饮料。",
        "science": "对乙酰氨基酚是家庭药箱里最常用的解热镇痛药，对胃肠道刺激相对较小，孕产妇与儿童也可在医生指导下选用。它主要通过肝脏代谢，过量服用会造成肝损伤，成人每日总量不宜超过 2 克。需要特别提醒的是，多数复方感冒药中都含有这一成分，同时服用多种感冒药容易在不知不觉中叠加过量。选购感冒药时不妨看清成分表，避免重复用药。",
        "initial": "D",
    },
    {
        "common_name": "阿莫西林胶囊", "brand_name": "阿莫仙", "form": "胶囊剂",
        "manufacturer": "珠海联邦制药股份有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于敏感菌（不产β-内酰胺酶菌株）所致感染，如中耳炎、鼻窦炎、咽炎、扁桃体炎等上呼吸道感染，以及泌尿生殖道感染、皮肤软组织感染等。",
        "usage_note": "成人一次 0.5g，每 6-8 小时一次，一日剂量不超过 4g；须遵医嘱足疗程服用，症状缓解也不宜擅自停药，以免细菌未被彻底清除而导致复发或耐药。",
        "contraindication": "青霉素过敏史及青霉素皮试阳性患者禁用；传染性单核细胞增多症患者易发生皮疹，应避免使用。",
        "science": "阿莫西林属于青霉素类抗生素，通过破坏细菌细胞壁发挥杀菌作用，是治疗常见敏感菌感染的基础药物。必须强调：抗生素只对细菌感染有效，对普通病毒性感冒并无治疗作用，把阿莫西林当「消炎万能药」既无益也催生耐药。用药前应确认青霉素过敏史，服用后出现皮疹、胸闷、呼吸困难等表现须立即停药并尽快就医。",
        "initial": "A",
    },
    {
        "common_name": "硝苯地平缓释片", "brand_name": "倪福达", "form": "缓释片",
        "manufacturer": "地奥集团成都药业股份有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于高血压；也可用于冠心病心绞痛的长期治疗。",
        "usage_note": "通常从小剂量开始，一般一次 10-20mg、一日 1-2 次，需整片吞服，不得嚼碎或掰开，否则会导致药物快速释放、血压骤降；剂量调整遵医嘱。",
        "contraindication": "对硝苯地平过敏者、心源性休克者、重度主动脉瓣狭窄者禁用；妊娠 20 周内的孕妇一般不使用。",
        "science": "硝苯地平是经典的钙通道阻滞剂，通过阻止钙离子进入血管平滑肌细胞使血管舒张、血压下降。缓释剂型让药物在体内缓慢释放，减少了普通片常见的反射性心跳加快与面部潮红。服药期间应坚持家庭血压监测并定期复诊，擅自停药可能引起血压反跳。西柚汁会干扰其代谢，服药期间建议避免大量饮用。",
        "initial": "X",
    },
    {
        "common_name": "盐酸二甲双胍片", "brand_name": "格华止", "form": "片剂",
        "manufacturer": "中美上海施贵宝制药有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于 2 型糖尿病，尤其是单用饮食与运动控制不满意者；也可与胰岛素或其他口服降糖药联合使用。",
        "usage_note": "一般从小剂量（如 0.5g）开始，随餐或餐后服用以减轻胃肠反应，一日 2-3 次，剂量由医生根据血糖逐步调整；进行碘化造影检查前后应按医嘱暂停用药。",
        "contraindication": "严重肾功能不全者、糖尿病急性并发症（如酮症酸中毒）患者、严重感染缺氧状态者及酗酒者禁用。",
        "science": "二甲双胍是 2 型糖尿病治疗的一线用药，已沿用六十余年。它主要通过减少肝糖输出、改善外周组织对胰岛素的敏感性来降糖，单独使用时一般不引起低血糖，还有轻度控制体重的优势。初期常见的腹胀、腹泻等多可随餐服用缓解。长期服用者建议定期检查肾功能与维生素 B12 水平，出现明显乏力、四肢麻木时及时告知医生。",
        "initial": "E",
    },
    {
        "common_name": "硫酸氢氯吡格雷片", "brand_name": "波立维", "form": "片剂",
        "manufacturer": "赛诺菲（杭州）制药有限公司", "tags": ["rx", "insurance"],
        "indication": "用于急性冠脉综合征患者及经皮冠状动脉介入治疗（PCI）术后，预防动脉粥样硬化血栓形成事件。",
        "usage_note": "常与阿司匹林联用，一般每日一次、每次 75mg，可在一天中任意固定时间服用；PCI 术后双联抗血小板疗程须严格遵心内科医嘱执行。",
        "contraindication": "对本品过敏者、活动性病理性出血（如消化性溃疡、颅内出血）患者、严重肝功能损害者禁用。",
        "science": "氯吡格雷是抗血小板药物，需经肝脏代谢活化后才能不可逆地抑制血小板聚集，常用于心脏支架术后防止支架内再堵塞。它与阿司匹林「双联」使用时防栓效果更强，出血风险也随之升高，若出现刷牙出血增多、大片瘀斑或黑便，应及时告知医生。擅自停药可能导致血栓事件，任何剂量调整都必须由心内科医生评估后进行。",
        "initial": "L",
    },
    {
        "common_name": "苯磺酸氨氯地平片", "brand_name": "络活喜", "form": "片剂",
        "manufacturer": "辉瑞制药有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于高血压，以及慢性稳定性心绞痛与变异型心绞痛的治疗。",
        "usage_note": "通常初始剂量为 5mg、每日一次，最大不超过 10mg，饭前饭后均可服用；血压控制目标与疗程遵医嘱，长期规律用药。",
        "contraindication": "对二氢吡啶类钙拮抗剂过敏者、严重低血压及休克患者禁用。",
        "science": "氨氯地平是长效二氢吡啶类降压药，作用平缓持久，一天一次即可平稳控压，偶尔漏服一次对血压的影响也相对较小，因此特别适合老年患者。常见不良反应为踝部水肿、面部潮红和头痛，多与血管扩张有关，通常随用药时间延长逐渐减轻。降压治疗贵在坚持：血压恢复正常是药物起效的表现，而不是可以停药的信号。",
        "initial": "A",
    },
    {
        "common_name": "奥美拉唑肠溶胶囊", "brand_name": "洛赛克", "form": "肠溶胶囊",
        "manufacturer": "阿斯利康制药有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于胃溃疡、十二指肠溃疡、反流性食管炎和卓-艾综合征；也与抗生素联合用于根除幽门螺杆菌。",
        "usage_note": "一般每日晨起一次、一次 20mg，整粒吞服、不可嚼碎；疗程视疾病而定，根除幽门螺杆菌需按医嘱联合用药 10-14 天，不宜长期自行服用。",
        "contraindication": "对本品过敏者禁用；严重肾功能不全者及婴幼儿禁用；孕妇、哺乳期妇女慎用。",
        "science": "奥美拉唑是第一代质子泵抑制剂，作用于胃壁细胞泌酸的「最后关口」，抑酸强而持久，是治疗酸相关疾病的基石药物。很多人把它当成「胃病万能药」，但长期不规范使用可能影响钙、镁及维生素 B12 的吸收，还可能掩盖更严重的胃部疾病。若出现消瘦、黑便、吞咽困难等报警症状，应先做胃镜检查而不是自行长期服药。",
        "initial": "A",
    },
    {
        "common_name": "布洛芬缓释胶囊", "brand_name": "芬必得", "form": "缓释胶囊",
        "manufacturer": "中美天津史克制药有限公司", "tags": ["otc", "essential", "insurance"],
        "indication": "用于缓解轻至中度疼痛如头痛、关节痛、偏头痛、牙痛、肌肉痛、神经痛及痛经；也用于普通感冒或流行性感冒引起的发热。",
        "usage_note": "成人一次 1 粒（0.3g）、一日 2 次（早晚各一次），整粒吞服；解热连续使用不超过 3 天、镇痛不超过 5 天，症状不缓解请咨询医师或药师。",
        "contraindication": "对其他非甾体抗炎药过敏者、活动性消化道溃疡或出血者、严重肝肾功能不全者禁用；孕晚期妇女禁用。",
        "science": "布洛芬通过抑制前列腺素合成发挥解热、镇痛、抗炎作用，是应用最广的非处方止痛药之一。缓释胶囊让药物缓慢释放，适合关节炎、痛经等持续性疼痛的对症处理。需要注意它对胃黏膜有刺激，建议随餐或饭后服用；高血压、心脏病患者以及正在服用抗凝药或糖皮质激素的人群，使用前最好先咨询医师或药师。",
        "initial": "B",
    },
    {
        "common_name": "蒙脱石散", "brand_name": "思密达", "form": "散剂",
        "manufacturer": "博福-益普生（天津）制药有限公司", "tags": ["otc", "essential", "insurance"],
        "indication": "用于成人及儿童的急、慢性腹泻；也可用于食道、胃及十二指肠疾病引起的相关疼痛症状的辅助治疗。",
        "usage_note": "将药粉倒入 50ml 温水中混匀后快速服下；成人一次 1 袋、一日 3 次，急性腹泻首剂加倍，儿童酌减或遵医嘱；与其他口服药物建议间隔 1-2 小时服用。",
        "contraindication": "对本品过敏者禁用；用药同时应注意防治脱水及电解质紊乱，症状加重应及时就医。",
        "science": "蒙脱石散是一种天然矿物质制剂，像「吸水海绵」一样覆盖在消化道黏膜表面，吸附病原体和毒素并保护肠壁，几乎不吸收入血，因此孕妇、哺乳期妇女和儿童都可以使用。服用时若与其他药物同服，可能被一并吸附而影响药效，建议间隔 1-2 小时。腹泻期间最重要的是补充水分和电解质；若出现血便、高热或腹泻持续超过两天，应及时就诊。",
        "initial": "M",
    },
    {
        "common_name": "氯雷他定片", "brand_name": "开瑞坦", "form": "片剂",
        "manufacturer": "拜耳医药（上海）有限公司", "tags": ["otc", "insurance"],
        "indication": "用于缓解过敏性鼻炎相关症状，如打喷嚏、流涕、鼻痒、鼻塞及眼痒、烧灼感；也用于缓解慢性荨麻疹及瘙痒性皮肤病的症状。",
        "usage_note": "成人及 12 岁以上儿童一日 1 次、一次 10mg；2-12 岁儿童按体重遵医嘱减量，症状控制后可在医生指导下按需使用。",
        "contraindication": "对本品过敏者禁用；严重肝功能不全者请在医生指导下使用。",
        "science": "氯雷他定是第二代抗组胺药，通过选择性阻断外周组胺 H1 受体，缓解过敏引起的打喷嚏、流涕和风团。相比第一代的氯苯那敏（扑尔敏），它不易透过血脑屏障，嗜睡感明显更轻，适合白天需要工作学习的人群。极少数人服药后仍有困倦，首次服用后驾驶或高空作业前应先观察自身反应。过敏症状反复出现时，建议查过敏原并做长期管理。",
        "initial": "L",
    },
    {
        "common_name": "门冬胰岛素注射液", "brand_name": "诺和锐", "form": "注射液",
        "manufacturer": "诺和诺德（中国）制药有限公司", "tags": ["rx", "insurance"],
        "indication": "用于治疗糖尿病，尤其适合餐后血糖升高明显、需要更快起效胰岛素的患者，常与中效或长效胰岛素联合使用。",
        "usage_note": "于餐前 5-10 分钟内皮下注射，剂量由医生根据血糖监测结果个体化制定；注射部位应规律轮换，开封后可在室温（不超过 25℃）保存 28 天。",
        "contraindication": "低血糖发作时禁用；对门冬胰岛素或本品辅料过敏者禁用。",
        "science": "门冬胰岛素是速效胰岛素类似物，注射后约 10-15 分钟起效，能更好地匹配进餐后的血糖高峰，让注射与进餐的衔接更从容。它通常需要与中效或长效胰岛素配合，构建全天的血糖控制方案。使用胰岛素不会「成瘾」，它是血糖控制不佳时的重要治疗手段；规范注射、规律监测与定期复诊同样重要。",
        "initial": "M",
    },
    {
        "common_name": "阿托伐他汀钙片", "brand_name": "立普妥", "form": "片剂",
        "manufacturer": "辉瑞制药有限公司", "tags": ["rx", "essential", "insurance"],
        "indication": "用于高胆固醇血症与混合型血脂异常的治疗；也用于冠心病及其等危症（如糖尿病、症状性动脉粥样硬化性疾病）患者的心血管风险预防。",
        "usage_note": "起始剂量一般为 10-20mg、每日一次，可在一天内任意时间服用、不受进餐影响；剂量调整与疗程遵医嘱，并定期复查血脂与肝功能。",
        "contraindication": "对本品过敏者、活动性肝病或不明原因血清转氨酶持续升高者、妊娠及哺乳期妇女禁用。",
        "science": "阿托伐他汀通过抑制胆固醇合成过程中的关键酶，降低血液中的「坏胆固醇」（LDL-C），是动脉粥样硬化防治的基石药物。大量研究证实，LDL-C 每下降一定幅度，心脑血管事件风险随之明显下降。少数人用药后出现肌肉酸痛、乏力，若伴酱油色尿应立即就医查肌酸激酶。服药期间避免大量饮用西柚汁，并遵医嘱定期复查血脂与肝功能。",
        "initial": "A",
    },
    {
        "common_name": "复方甘草片", "brand_name": None, "form": "片剂",
        "manufacturer": "太极集团重庆桐君阁药厂", "tags": ["rx", "essential", "insurance"],
        "indication": "用于镇咳祛痰，缓解上呼吸道感染、支气管炎等引起的咳嗽、咳痰症状。",
        "usage_note": "口服或含化，成人一次 3-4 片、一日 3 次，连续服用一般不超过 7 天；不宜与强力镇咳药同时使用，症状持续应就医。",
        "contraindication": "对本品成分过敏者禁用；醛固酮增多症、低血钾、高血压及肾功能不全者慎用；孕妇、哺乳期妇女及胃炎溃疡患者慎用。",
        "science": "复方甘草片由甘草浸膏粉、阿片粉、樟脑等组成，镇咳效果确切。其中的阿片粉作用于咳嗽中枢，长期连续使用可能产生依赖性，因此必须短期、小剂量、遵医嘱使用。甘草成分还可能引起「假性醛固酮增多」，表现为血压升高、血钾降低和水肿，高血压患者尤需注意。咳嗽只是症状：持续两周以上或伴发热、咯血的咳嗽，应先查明病因再对症用药。",
        "initial": "F",
    },
]

# (category, name, desc, price分)
MP_PRODUCTS = [
    ("food", "药食同源·陈皮普洱养生茶",
     "新会陈皮搭配云南熟普，独立小袋包装，饭后一杯理气消滞，适合久坐与应酬较多的人群。", 7900),
    ("food", "药食同源·芡实茯苓薏米茶",
     "芡实、茯苓、炒薏米经典配伍，低温烘焙不上火，潮湿季节的日常茶饮选择。", 6900),
    ("fsmp", "特医食品·术后康复全营养粉",
     "全营养配方，优质乳清蛋白搭配膳食纤维与多种维生素矿物质，适合术后恢复期营养补充（在医师或临床营养师指导下使用）。", 29800),
    ("fsmp", "特医食品·糖尿病专用营养粉",
     "低升糖指数配方，缓释碳水与单不饱和脂肪酸配比，用于血糖异常人群的营养补充（在医师或临床营养师指导下使用）。", 26800),
    ("package", "健康服务包·全家健康管理年卡",
     "含全年 4 次全科医师在线问诊、2 次体检报告解读与专属健康档案管理，一人建档、全家可用。", 129900),
    ("package", "健康服务包·术后康复 90 天陪跑包",
     "康复治疗师上门评估 2 次 + 全程线上训练指导 + 营养师膳食方案，覆盖骨科术后黄金康复期。", 199900),
    ("device", "器械耗材·上臂式电子血压计",
     "医用级压力传感，双人各 90 组记忆，不规则心跳提示，大屏大字适合长辈使用。", 19900),
    ("device", "器械耗材·腰椎家用牵引理疗仪",
     "气动腰椎牵引与热敷组合，五档力度可调，居家缓解久坐腰肌劳损（使用前请阅读说明书并咨询医师）。", 39900),
]

MP_NOTICES = [
    ("「杏临千行」公益义诊本周六走进滨海新区，全科与康复团队现场免费答疑", 1),
    ("康复上门服务新增骨科术后专项评估，上线首周预约立减 30 元", 2),
    ("健康商城即将开放：药食同源茶饮、特医食品与器械耗材抢先看", 3),
]


async def seed_mp_data(db: AsyncSession, *, verbose: bool = False) -> None:
    """健康域种子：mp_scenarios 非空即整体跳过（幂等，可重跑）。

    演示数据（档案/预约/评价/会话）依赖手机号 13800000001 的演示用户，
    若该用户不存在则跳过这部分，不影响基础数据。
    """
    _ensure_demo_seed_allowed()
    existing = await db.scalar(select(func.count()).select_from(MpScenario))
    if existing:
        if verbose:
            print("健康域（mp_）数据已存在，跳过。")
        return

    scenarios = [
        MpScenario(key=key, name=name, tagline=tagline, sort=sort)
        for key, name, tagline, sort in MP_SCENARIOS
    ]
    db.add_all(scenarios)
    await db.flush()

    services = [
        MpService(
            scenario_key=scenario_key,
            service_type=service_type,
            name=name,
            desc=desc,
            price=price,
            unit=unit,
            duration_min=duration_min,
            sales=sales,
            on_shelf=True,
        )
        for scenario_key, service_type, name, desc, price, unit, duration_min, sales in MP_SERVICES
    ]
    db.add_all(services)
    await db.flush()

    providers = []
    for name, gender, title, dept, org, years, skills, intro, rating, service_count, online in MP_PROVIDERS:
        providers.append(
            MpProvider(
                name=name,
                gender=gender,
                title=title,
                dept=dept,
                org=org,
                years=years,
                skills=skills,
                intro=intro,
                avatar=None,
                rating=rating,
                service_count=service_count,
                online=online,
            )
        )
    db.add_all(providers)
    await db.flush()

    for provider_index, service_indexes in MP_PROVIDER_SERVICE_LINKS:
        for service_index in service_indexes:
            db.add(
                MpProviderService(
                    provider_id=providers[provider_index].id,
                    service_id=services[service_index].id,
                )
            )

    db.add_all([MpDrug(**drug, publication_status="demo") for drug in MP_DRUGS])
    db.add_all(
        [
            MpProduct(category=category, name=name, desc=desc, price=price, status="coming")
            for category, name, desc, price in MP_PRODUCTS
        ]
    )
    db.add_all(
        [MpNotice(content=content, sort=sort) for content, sort in MP_NOTICES]
    )
    await db.flush()

    # ---- 演示用户的健康域数据 ----
    demo_user = (
        await db.execute(select(User).where(User.phone == DEMO_MP_PHONE))
    ).scalar_one_or_none()
    if demo_user is not None:
        await _seed_mp_demo(db, demo_user, services, providers)

    await db.commit()

    if verbose:
        print(
            f"健康域种子：场景 {len(scenarios)} · 服务 {len(services)} · 人员 {len(providers)} · "
            f"药品 {len(MP_DRUGS)} · 商品 {len(MP_PRODUCTS)} · 公告 {len(MP_NOTICES)}"
        )


async def _seed_mp_demo(
    db: AsyncSession, demo_user: User, services: list[MpService], providers: list[MpProvider]
) -> None:
    """演示用户（南屿 13800000001）：档案、2 个预约（1 confirmed 线下 / 1 completed 线上）、
    1 条评价、1 个 open 咨询会话（4 条 user/provider 交替消息）。"""
    now = _now()
    today = date.today()

    db.add(
        MpProfile(
            user_id=demo_user.id,
            name="南屿",
            gender="male",
            birth="1988-05",
            height_cm=176,
            weight_kg=72.5,
            blood="A",
            chronic_tags=["高血压"],
            allergy="青霉素类抗生素",
            surgery_history="2023 年阑尾切除术",
            smoke=False,
            drink=True,
            sport_freq="每周 2-3 次",
        )
    )

    # 预约 1：线下康复上门（已确认，未来日期）
    confirmed = MpAppointment(
        user_id=demo_user.id,
        service_id=services[4].id,
        provider_id=providers[3].id,
        appt_date=today + timedelta(days=2),
        period="上午",
        address="滨海市和平区春华街道澄湖路 12 号 3-502",
        contact_name="南屿",
        contact_phone=DEMO_MP_PHONE,
        status="confirmed",
        remark="右膝术后 6 周，希望评估居家训练动作是否标准。",
        created_at=now - timedelta(days=1, hours=2),
    )
    # 预约 2：线上全科问诊（已完成，过去日期）
    completed = MpAppointment(
        user_id=demo_user.id,
        service_id=services[0].id,
        provider_id=providers[0].id,
        appt_date=today - timedelta(days=3),
        period="晚间",
        address=None,
        contact_name="南屿",
        contact_phone=DEMO_MP_PHONE,
        status="completed",
        remark="近一周血压波动，想请医生看看是否需要调整用药时间。",
        created_at=now - timedelta(days=4),
    )
    db.add_all([confirmed, completed])
    await db.flush()

    db.add(
        MpReview(
            appointment_id=completed.id,
            user_id=demo_user.id,
            provider_id=providers[0].id,
            rating=5,
            content="陈医生问得很细，帮我把降压药调整到睡前服用，一周后血压平稳多了，复诊提醒也很及时。",
            created_at=now - timedelta(days=2, hours=5),
        )
    )

    # 咨询会话：健康问答演示（历史消息保留，旧 provider 来源未核验）
    consult = MpConsult(
        user_id=demo_user.id,
        service_id=services[2].id,
        provider_id=providers[1].id,
        status="open",
        created_at=now - timedelta(days=1, hours=8),
    )
    db.add(consult)
    await db.flush()

    demo_messages = [
        (
            "user",
            "您好，我父亲 72 岁，长期每天服用阿司匹林肠溶片预防血栓。这几天膝盖疼得厉害，"
            "他想自己买布洛芬缓释胶囊吃，这两个药能一起吃吗？",
            now - timedelta(days=1, hours=8),
        ),
        (
            "provider",
            "已收到你的描述。基于合理用药知识库给出参考建议：阿司匹林与布洛芬同属非甾体抗炎药，"
            "同服可能增加消化道出血风险，还可能削弱阿司匹林的抗血小板作用。膝盖疼痛建议先请医师评估原因，"
            "可在医师或药师指导下选择对乙酰氨基酚等对胃肠影响较小的替代镇痛药，并关注肾功能与血压变化。"
            "请注意，以上为健康信息参考，不构成诊疗建议。",
            now - timedelta(days=1, hours=8) + timedelta(minutes=2),
        ),
        (
            "user",
            "明白了。他还有高血压，平时吃硝苯地平缓释片，这个有影响吗？",
            now - timedelta(days=1, hours=6),
        ),
        (
            "provider",
            "已收到你的描述。基于合理用药知识库给出参考建议：硝苯地平缓释片需整片吞服，不可嚼碎或掰开；"
            "布洛芬等止痛药长期使用可能引起水钠潴留、升高血压，与降压药同用时更应加强血压监测。"
            "建议记录家庭自测血压并按期复诊，由医师评估是否需要调整治疗方案。"
            "请注意，以上为健康信息参考，不构成诊疗建议。",
            now - timedelta(days=1, hours=6) + timedelta(minutes=2),
        ),
    ]
    for sender, content, created_at in demo_messages:
        db.add(
            MpConsultMessage(
                consult_id=consult.id, sender=sender, content=content, created_at=created_at
            )
        )
    consult.last_message_at = demo_messages[-1][2]


if __name__ == "__main__":
    asyncio.run(seed())
