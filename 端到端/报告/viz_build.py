# -*- coding: utf-8 -*-
"""由事件数据包生成仿真推演报告（自包含 HTML）。事件无关，2026-10-13 第十八轮改写。

用法：`python viz_build.py [--config PATH]`；默认读同目录 `报告配置.json`（见 _report_config.py）。

结构按命题「报告五大模块」：传播路径图谱 / 情绪演化曲线 / 关键影响节点 / 风险等级 / 应对建议。
以【报告正文】为主体，图表为辅助佐证。不改任何 .md 文件。
版式已冻结：模块编号、`#rNN` 锚点、图表登记、模块总结约定一律不动，换事件只改配置。"""
import json, io, sys, os, datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ★ 2026-10-13 第十八轮：路径与事件身份串一律走**事件配置**（报告配置.json）。
#   代码里不再出现事件目录 / 事件 ID / 事件标题 —— 换事件只改那一份 JSON。
#   存量调用 `python viz_build.py` 行为不变；`--config PATH` 换事件。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _report_config import load as _load_cfg

CFG = _load_cfg(argv=sys.argv[1:])
D = CFG.run_dir
OUT = CFG.out_html
print(CFG.describe())

payload = json.load(open(CFG.data_path, encoding='utf-8'))

# ★ 2026-10-21 第三十二轮（模块五改造）：**事件类型与当事人注入载荷**。
#   此前 `event_type` 只作为模板串 `__EVENT_TYPE__` 出现在模块五抬头那一句话里，
#   内联 JS 的 `D.meta` 里根本没有 ⇒ 想按类型选建议库，JS 侧读不到。
#   这里补进 `payload["meta"]`，只影响本次渲染的 HTML，**不回写 `_viz_data.json`**
#   （那是 `viz_extract` 的产物，回写会让逐位复现的判据失效）。
payload.setdefault("meta", {})
payload["meta"]["event_type"] = (CFG.get("event_type") or "").strip()
payload["meta"]["event_type_source"] = (CFG.get("event_type_source") or "").strip()
payload["meta"]["party"] = [str(x).strip() for x in (CFG.get("party_entities") or []) if str(x).strip()]
#   ★ `subject` 与 `party` 是**两个概念**，别混：
#     party   = 被侵害/投诉/被罚的一方（`party_entities.json`，EVT-001 为空）
#     subject = 涉事主体（众安保险），模块五主线卡里的「涉事主体」读这个
payload["meta"]["subject"] = (CFG.get("subject") or "").strip()

# ★ 2026-10-20 第二十七轮：LLM 正文与数据包**分文件**存放（`_llm_text.json`）。
#   存在就挂到 payload['llm']，不存在则报告完全走规则文案 —— 离线跑、陌生人拿包跑都一样。
#   凭据不在这里：本文件只读生成好的文本，不读 .env、不发请求。
# ★ 2026-10-21：**位置契约 = 跟报告 HTML 同目录**（`llm_fill` 也写在那里）。
#   五层归档里 `04_仿真处理` 放快照、`05_报告` 放报告，`_llm_text.json` 属于报告层产物，
#   跟着 HTML 走才不会出现「报告在 05、它的输入在 04」。旧布局（html 与快照同目录）行为不变：
#   那时 dirname(OUT) == D，两个候选本来就是同一个路径。
_llm_path = os.path.join(os.path.dirname(os.path.abspath(OUT)), "_llm_text.json")
if not os.path.exists(_llm_path):
    _llm_alt = os.path.join(D, "_llm_text.json")      # 兼容：与快照同目录的旧位置
    if os.path.exists(_llm_alt):
        _llm_path = _llm_alt
if os.path.exists(_llm_path):
    try:
        payload["llm"] = json.load(io.open(_llm_path, encoding="utf-8"))
        print("  LLM 文本：", sorted((payload["llm"].get("items") or {}).keys()))
    except Exception as _e:
        print("  !! _llm_text.json 读取失败，回退规则文案：", _e)

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__DOC_TITLE__</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
/* ★ 2026-10-12 第五轮：整页改纯白（原 #eef1f6 灰底是仪表盘观感的一部分）。 */
:root{--serif:Georgia,"Times New Roman","Songti SC","SimSun",serif}
*{box-sizing:border-box}
body{margin:0;background:#ffffff;color:#1f2733;
  font:15.5px/1.95 "Microsoft YaHei","PingFang SC",system-ui,-apple-system,sans-serif;}
a{color:#1d6fd4;text-decoration:none}
.wrap{max-width:1360px;margin:0 auto;padding:0 24px 70px}

/* ---- 报告头部（2026-10-12 第五轮：SaaS 仪表盘 → 智库研报风） ----
   原为深蓝渐变通栏 + 白色圆角胶囊标签行，现改为白底、特大衬线主标题、无标签。 */
header{background:#ffffff;padding:58px 0 46px;border-bottom:1px solid #e6e6e6}
header .wrap{padding-bottom:0}
.eyebrow{font-size:12px;letter-spacing:.22em;color:#999;text-transform:uppercase;
  margin-bottom:16px;font-family:var(--serif)}
h1.title{font-family:var(--serif);font-size:40px;font-weight:700;color:#1a1a1a;
  line-height:1.2;margin:0;letter-spacing:.01em}
.subtitle{font-size:19px;color:#555;margin-top:10px;line-height:1.5}

/* （2026-10-12 第五轮：粘性导航条的样式按仿真侧要求整块删除，HTML 里的导航条元素同步删除。
   此后全文再无页内导航，章节靠 h2.sec 的小节线分隔。） */

/* ★ 2026-10-12 第五轮连带：整页转纯白后，白卡 + 淡阴影会与底色糊在一起，
   故去掉阴影、改 1px 细边框、圆角收到 3px（研报的分栏观感）。 */
.card{background:#fff;border:1px solid #e8e8e8;border-radius:3px;
  padding:28px 32px;margin-top:22px}
h2.sec{font-family:var(--serif);font-size:23px;margin:40px 0 4px;padding-left:15px;border-left:6px solid #1d6fd4;line-height:1.3}
h2.sec .en{font-size:12px;color:#8a97a8;font-weight:400;margin-left:12px;letter-spacing:1px}
h3.sub{font-family:var(--serif);font-size:18.5px;margin:32px 0 12px;color:#16283f;
  padding-bottom:9px;border-bottom:2px solid #e6ebf3}
h3.sub:first-child{margin-top:2px}
h4.mini{font-family:var(--serif);font-size:16px;margin:22px 0 9px;color:#2b3a4e}
.desc{color:#5a6a7e;font-size:14.5px;margin:8px 0 14px}

/* 报告正文 */
.rep p{margin:12px 0;text-indent:2em;font-size:15.5px;line-height:2.05;color:#22303f}
.rep p.noind{text-indent:0}
.rep .lead{background:#f4f8ff;border-left:5px solid #1d6fd4;border-radius:0 9px 9px 0;
  padding:14px 18px;text-indent:2em;margin:16px 0}
/* ★ 2026-10-12 第五轮：头部摘要段用 id 覆盖（特异性高于 .rep .lead），
   只改顶部这一处；模块四 4.1 与模块五 #r50 的两块 .lead 保持原样。 */
#abstract{background:#f9f9f9;border-left:4px solid #2c3e50;border-radius:0;
  padding:20px 24px;text-indent:0;margin:30px 0 0;font-size:16px;line-height:1.6;
  color:#3a3a3a}
.rep b{color:#12406f}
.rep .k{color:#c0392b;font-weight:700}
.rep .grp{background:#fafcff;border:1px solid #e2eaf5;border-radius:9px;
  padding:13px 18px;margin:14px 0}
.rep .grp p{text-indent:0;margin:7px 0}
.rep ol,.rep ul{margin:10px 0 10px 6px;padding-left:22px}
.rep li{margin:7px 0;line-height:1.95}
.hl{background:#fff6e0;padding:1px 5px;border-radius:4px;font-weight:600;color:#8a5a00}
.ev{border-left:4px solid #8ab6f0;background:#f8fbff;border-radius:0 8px 8px 0;
  padding:10px 16px;margin:9px 0}
.ev b.n{color:#1d6fd4}

/* （2026-10-12 第五轮：KPI 卡片网格 .kpis / .kpi 样式按仿真侧要求整块删除，HTML 里的 #kpis 与生成它的 JS 同步删除。原 10 个指标：轮数 / 智能体 / 帖子 / 引用边 /跨渠道 / 单日互动峰值 / 情绪最低点 / 关键时段 / 红色持续轮数 / 效价均值 —— 其中时间跨度与轮次已并入头部副标题与摘要段，其余数字在正文各模块内均有出处。） */

.chart{width:100%;height:430px}
.chart.tall{height:620px}
.chart.mid{height:520px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media(max-width:1000px){.row2{grid-template-columns:1fr}}
.cap{font-size:13.5px;color:#7c8a9c;margin:8px 0 0;text-align:center;line-height:1.8}
/* 拐点说明框（2026-10-12 第九轮）：替代原先压在曲线上的常驻标签，改为点击圆点后在此展开 */
.pivbox{font-size:13.8px;color:#33455c;line-height:1.75;background:#fafbfc;
  border:1px solid #e6eaef;border-left:3px solid #d4380d;border-radius:3px;
  padding:10px 14px;margin:10px 0 0;min-height:78px}
.pivbox .pivhint{color:#8a97a8}
.pivbox .pivdv{color:#7c8a9c;margin-left:10px;font-size:13px}
.pivbox .pivrow{margin-top:5px}
.pivbox .pivk{display:inline-block;min-width:38px;color:#a82a06;font-weight:600;margin-right:6px}
/* 表 1-3 末列的「N 起 ▸」事件格（★ 2026-10-12 第十一轮）：点开在 #crossDetail 展开该轮细节。
   ★ 第十三轮起，该计数为「转载 + 跨渠道引用」之和 */
.xev{cursor:pointer;color:#a82a06}

/* （2026-10-12 第四轮：1.3 时间轴的 .timeline / .tl 系列样式已随该块删除，
   留档见 `_版本留痕_模块一_第四轮_20261012\_del_1.3_时间轴_删除内容.txt`） */

.optlabel{display:inline-block;font-size:12px;font-weight:600;color:#fff;background:#8a97a8;
  border-radius:4px;padding:1px 8px;margin-right:8px;vertical-align:1px}

table{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:12px}
th{background:#eef3fa;color:#22344c;font-weight:600;padding:9px 10px;text-align:left;
  border-bottom:2px solid #cfdcec;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #eef1f6;vertical-align:top}
tbody tr:nth-child(even){background:#fafcff}
tbody tr:hover{background:#eef6ff}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:Consolas,"Courier New",monospace;font-size:12.5px}
.chip{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;
  color:#fff;white-space:nowrap;line-height:1.7}
.tbwrap{overflow-x:auto}
.seg1{box-shadow:inset 3px 0 0 #8ab6f0}
.seg2{box-shadow:inset 3px 0 0 #f0c48a}
.seg3{box-shadow:inset 3px 0 0 #b8a8e8}
tr.hot td{background:#fff2ef !important}

.adv{border:1px solid #e2e9f3;border-radius:10px;padding:16px 20px;margin:13px 0;background:#fff}
.adv .h{font-weight:700;font-size:16px;color:#12406f;margin-bottom:7px;line-height:1.7}
.adv .h .no{display:inline-block;width:25px;height:25px;line-height:25px;text-align:center;
  background:#1d6fd4;color:#fff;border-radius:50%;font-size:13.5px;margin-right:10px}
.adv .basis{font-size:14px;color:#4a5a70;background:#f7faff;border-radius:7px;
  padding:10px 13px;margin-top:8px;line-height:1.9}
.adv .basis b{color:#c0392b}
/* ★ 2026-10-21 第三十二轮（模块五改造）：落地动作从「一句话」改成四要素小表
   （主体 / 时点 / 动作 / 验收）。仿真侧原话：「一线人员拿到后可以直接照着执行」。 */
.adv .plan{width:100%;border-collapse:collapse;margin-top:2px}
.adv .plan th{width:50px;text-align:left;vertical-align:top;color:#0f7a45;
  font-weight:700;padding:3px 8px 3px 0;white-space:nowrap;font-size:13.5px}
.adv .plan td{padding:3px 0;vertical-align:top;font-size:14px;color:#3a4a60}
.adv .plan ol{margin:0;padding-left:1.15em}
.adv .plan li{margin:1px 0}
.adv .tag{display:inline-block;padding:0 6px;border-radius:4px;font-size:11.5px;
  background:#eaf3ff;color:#1d6fd4;margin-right:6px;vertical-align:1px}
.adv.mainline{border-color:#f0c48a;box-shadow:inset 3px 0 0 #f0c48a}
.foot{font-size:13.5px;color:#7c8a9c;margin-top:28px;line-height:2}
/* ★ 2026-10-20 第二十七轮：每个模块左下角的「灰字公式」。
   仿真侧原话：「你可以在报告里就每个模块的左下角，因为你用了公式嘛，
   你可以用灰色的小字去把这个公式是什么东西说一下 —— 显示可信度与透明性」。
   放到卡片底部、左对齐（不居中，区别于图注），字号 11px、颜色 #98a2b3。 */
.fn{margin-top:14px;padding-top:9px;border-top:1px dashed #e3e8ee;
  font-size:11px;line-height:1.85;color:#98a2b3;text-align:left}
.fn b{color:#7c8a9c;font-weight:600}
.fn .fx{font-family:ui-monospace,Consolas,monospace;color:#8d99a8}
/* ★ 2026-10-20 第二十七轮：LLM 补充解读块（可选，数据包没有 _llm_text.json 时不出现） */
.llmnote{margin-top:14px;padding:11px 14px;background:#f7faff;
  border-left:3px solid #1d6fd4;border-radius:0 7px 7px 0}
.llmnote .hd{font-size:12.5px;color:#1d6fd4;font-weight:600;margin-bottom:5px;text-align:left}
.llmnote .hd .mt{color:#8d99a8;font-weight:400;margin-left:8px;font-size:11.5px}
.llmnote .bd{font-size:14.5px;color:#33465e;line-height:1.95;text-align:left}
#warn{display:none;background:#fff3f0;color:#a82a06;padding:11px 24px;font-size:14px;
  border-bottom:1px solid #f5c6b8}
</style>
</head>
<body>
<div id="warn">⚠ echarts 未能加载（CDN 不可达）。页面文字与表格内容完整，图形需联网后刷新查看。</div>
<div id="secErr" style="display:none">⚠ 本报告有部分段落未能渲染，其余段落正常。</div>

<header>
  <div class="wrap">
    <div class="eyebrow">__EYEBROW__</div>
    <h1 class="title">__REPORT_TITLE__</h1>
    <div class="subtitle">多智能体仿真推演报告 · __NROUNDS__ 轮 / __HOURS__ 小时全时段回溯推演</div>
    <div class="rep"><div class="lead" id="abstract"></div></div>
  </div>
</header>

<div class="wrap">

<!-- ================= 模块一 ================= -->
<h2 class="sec" id="s1">一 · 传播路径图谱<span class="en">PROPAGATION GRAPH</span></h2>

<div class="card">
  <h3 class="sub">模块总结</h3>
  <div class="rep" id="r11"></div>
</div>

<div class="card">
  <h3 class="sub">1.1 每日发帖量与互动趋势</h3>
  <div class="rep" id="r12"></div>
  <div id="chDaily" class="chart mid"></div>
  <p class="cap">图 1-1　每日发帖量与互动趋势。<b>柱</b>为发帖量，按帖子的实际发布时间归属自然日（整数）；<b>折线</b>为互动量，即当日评论＋转发＋点赞的动作数。互动量的交互记录为逐轮汇总、无逐条时间戳，故按各轮窗口小时数分摊到自然日，为加权值（会出现 .5），全期合计 __TOTALFLOW__ 次。二者同轴，悬停可一并读出该日的负面情绪占比、效价均值、在线人数与渠道构成。<b>首日与末日通常是残缺日</b>（推演窗口按整轮切分，不按自然日对齐），横轴上已直接标出当天实际覆盖的小时数 —— 这两天不宜与整日柱直接比高低。</p>
  <h4 class="mini">每日明细</h4>
  <div class="tbwrap"><table id="tbDaily"></table></div>
  <p class="cap">表 1-1　逐日发帖量、三类互动量、效价均值、负面占比与在线人数。评论 / 转发 / 点赞 / 互动量四列取一位小数——分摊值含 .5，若四舍五入成整数，各行相加会与合计差 1。t0 落在 __CAP_T0__，首日仅覆盖 __CAP_FIRSTH__ 小时，故该日数值偏小。<b>浅红底</b>的一行为<span id="hotDayNote"></span>。</p>
</div>

<div class="card">
  <h3 class="sub">1.2 每日渠道分布</h3>
  <div class="rep" id="r13"></div>
  <div id="chChanDay" class="chart mid"></div>
  <p class="cap">图 1-2　每日各渠道发帖量（堆叠柱）。按帖子的原发渠道与实际发布时间统计，纵轴为绝对条数，堆叠自下而上依次为__CAP_CHORDER__。柱高＝当日发帖量，各色段的厚度＝该渠道当日产出。__CAP_Z12__</p>

  <h4 class="mini">渠道 × 自然日 发帖量</h4>
  <div class="tbwrap"><table id="tbChanDay"></table></div>
  <p class="cap">表 1-2　各渠道逐日发帖量、合计与占比。</p>

  <h4 class="mini">渠道覆盖里程碑与跨渠道传播</h4>
  <div class="tbwrap"><table id="tbCross"></table></div>
  <p class="cap">表 1-3　只列出<b>渠道数发生变化</b>或<b>当轮发生跨渠道传播</b>的轮次，其余轮次与上一行相同，不重复列出。一条渠道被记为<b>已触达</b>，当且仅当有帖子落在它上面、或有某次跨渠道传播把它列为到达方，二者各按自己发生的轮次计入；全程累计已触达的渠道个数即「渠道数」一列。全渠道池共 <span id="chPoolN"></span> 个，__CAP_Z13__末列为当轮跨渠道传播的起数，由<b>转载</b>与<b>跨渠道引用</b>两种机制合计而成，点「N 起 ▸」可在下方展开明细。图 1-2 呈现的是帖子的原发渠道，内容被带到别的平台后的落点由本表末列展开。</p>
  <div id="crossDetail" class="pivbox"></div>
  <div class="fn" id="fnM1"></div>
</div>

<!-- ================= 模块二 ================= -->
<h2 class="sec" id="s2">二 · 情绪演化曲线<span class="en">EMOTION CURVE</span></h2>

<div class="card">
  <h3 class="sub">模块总结</h3>
  <div class="rep" id="r20"></div>
</div>

<div class="card">
  <h3 class="sub">2.1 全场情绪走势</h3>
  <div class="rep" id="r21"></div>
  <div id="chEmo" class="chart mid"></div>
  <p class="cap">图 2-1　全场情绪演化曲线。左轴为效价（−1 至 +1），右轴为负面智能体占比；实线为全场人均效价，虚线为当轮在线智能体均值。横轴下方为仿真日编号，自 t0 起每 24 小时为一日、每 6 轮为一日，共 __CAP_EMODAYS__ 日。<b>蓝色圆点</b>标出全程效价的最低点 —— 只对全场均值判定；虚线是在线智能体的当轮均值，样本每轮更换，其读数可以更低，不参与最低点判定。<span id="segNote"></span></p>
</div>

<div class="card">
  <h3 class="sub">2.2 情绪在各角色、各渠道上的分布</h3>
  <div class="rep" id="r22"></div>
  <h4 class="mini">角色维度：五类主体的逐轮情绪</h4>
  <div id="chRoleLine" class="chart" style="height:470px"></div>
  <h4 class="mini">渠道维度：六个渠道的逐轮情绪</h4>
  <div id="chChanLine" class="chart" style="height:500px"></div>
  <p class="cap">图 2-2 / 2-3　角色与渠道的逐轮情绪折线。纵轴为效价，横轴为轮次与仿真日。线条以实线、虚线、点线及不同线宽区分；某群体在某轮无数据时不连线，避免把空缺读作零值。</p>
  <div class="fn" id="fnM2"></div>
</div>

<!-- ================= 模块三 ================= -->
<!-- ★ 2026-10-12 第九轮：按仿真侧提供的 prompt 重写为「模块总结 + 3.1 关键时段 + 3.2 渠道影响力」
     三部分（原 5 卡 → 3 卡）。原 3.2 / 3.4 / 3.5 三节整块删除，随之一并去掉 9 张图与 2 张表。
     ★ 2026-10-12 第十一轮：在「关键时段」前补 3.1「关键节点：该盯谁」（按账号统计被引 / 被转 /
     必经路径数），原两节顺延为 3.2 / 3.3。
     ★ 2026-10-12 第十二轮：仿真侧**重新定义了关键节点** —— 关键影响节点 = **关键事件**，
     回答「哪些轮次发生了哪些关键事件、这些事件如何改变了走向」。新 3.1 按该定义重做；原按账号统计的
     3.1 **降为 3.2**（它答的是「该盯谁」），**该节已于第十五轮整节删除**；关键时段与渠道影响力顺延为 3.3 / 3.4，第十五轮后再上移为 3.2 / 3.3。
     ★ 2026-10-13 第十四轮：仿真侧「我觉得目前只需要关键事件清单，其他动作过分冗余」⇒ 删掉 3.1 原有的
     **图 3-1 关键事件时间线**与**图 3-2 影响评估**，只留**表 3-1 关键事件清单**
     （图 3-2 的每一个数都已在表 3-1 的各列里；图 3-1 那种「事件与情绪同轴直读」的读法，
     改由表 3-1 的时点列与模块二图 2-1 分头承担）。`#evtDetail` 详情框保留，
     位置从表上方移到表下方 —— 唯一消费方由「点图上的圆点」改为「点表内的事件名」。
     图号随之上移：原 图 3-3 / 3-4 / 3-5 → **图 3-1 / 3-2 / 3-3**（表号不动，仍为 3-1 / 3-2 / 3-3）。
     ★ 2026-10-13 第十五轮：仿真侧「3.2 也删除吧」⇒ **3.2 关键节点（该盯谁）整节删除** ——
     表 3-2 关键节点明细 ＋ 图 3-1 节点双指标散点 ＋ `#r32` 正文 ＋ 其全部派生量
     （CITED / KN / KN_* / REP_* / CUTN / TOPPOST_*）。节号、图号、表号再上移一轮：
     **3.2 关键时段 / 3.3 渠道影响力**；**图 3-1 #chWin / 图 3-2 #chChanInt**；**表 3-2 渠道维度明细**。
     并改写三处依赖它的交叉引用：模块四「节点清单见模块三 3.2」那句（其指向已不存在）、
     模块五两条 `pos`、附录 B `#tbSrc` 的「关键节点识别」整行删除（13 行 → 12 行）。
     ★ 2026-10-13 第十六轮：仿真侧「3.1 关键影响节点 目前文本内容过多」⇒ **只压正文，不动结构**。
     `#r31` 由 2,877 字六段压到 785 字三段（节点逐条一行，`<br>` 分行；因果与按强度分档合到第三段）。
     按他的保留清单：每个节点只留「谁在什么渠道做了什么 → 命中哪些维度 → 此后三轮互动量」，
     并按一级催化剂 / 二级放大器 / 前哨信号分档；**不再列出未命中关键节点标准的轮次**，
     也**不再展开基线计算方法**（表 3-1 图注里那句「只命中一个维度的轮次在正文中作为
     单一维度信号说明」随之失效，本轮一并删除）。结构、图表、表 3-1 的 7 行一字未动。 -->
<h2 class="sec" id="s3">三 · 关键影响节点<span class="en">KEY INFLUENCERS</span></h2>

<div class="card">
  <h3 class="sub">模块总结</h3>
  <div class="rep" id="r30"></div>
</div>

<div class="card">
  <h3 class="sub">3.1 关键影响节点：为什么那时候危险</h3>
  <div class="rep" id="r31"></div>
  <h4 class="mini">关键事件清单</h4>
  <div class="tbwrap"><table id="tbEvt"></table></div>
  <p class="cap">表 3-1　按时间顺序列出全部 <span id="evtN"></span> 个关键影响节点，即同时命中<b>两个及以上</b>识别维度的轮次。「影响倍数」为事件后 3 轮互动量均值与事件前 3 轮均值之比；<b>事件前窗口__PREWIN_NOTE__点事件名可展开该轮的触发指标与后果。</p>
  <div id="evtDetail" class="pivbox"></div>
</div>

<div class="card">
  <h3 class="sub">3.2 关键时段：什么时候最危险</h3>
  <div class="rep" id="r33"></div>
  <div id="chWin" class="chart mid"></div>
  <p class="cap">图 3-1　逐轮动作总量与负面占比。柱为当轮仿真窗口的动作总量（左轴，＝发帖＋评论＋转发＋点赞），折线为当轮负面占比（右轴），阴影区为识别出的关键时段 —— 全程连续 4 轮中动作总量最大的一段。横轴下方为仿真日编号，自 t0 起每 24 小时为一日、每 6 轮为一日。</p>
</div>

<div class="card">
  <h3 class="sub">3.3 渠道影响力：哪个平台最危险</h3>
  <div class="rep" id="r34"></div>
  <div id="chChanInt" class="chart mid"></div>
  <p class="cap">图 3-2　各渠道的每帖平均被引次数，按数值从高到低排列。口径为该渠道帖子被其他帖子引用的累计次数（跨轮、跨渠道均计入）除以该渠道发帖数。</p>
  <h4 class="mini">渠道维度明细</h4>
  <div class="tbwrap"><table id="tbChan"></table></div>
  <p class="cap">表 3-2　「活跃账号」指作出过任意动作的智能体，「发帖账号」指实际发过帖的智能体，两者口径不同。__CAP_Z32__</p>
  <div class="fn" id="fnM3"></div>
</div>

<!-- ================= 模块四 ================= -->
<!-- ★ 2026-10-13 第十七轮：按仿真侧【关于预警的优化方向】重做预警模块。
     ① 三维度观测量由**累计量**改为**5 轮滑动窗口**口径 —— 原三项观测量全单调不减，
        S 只升不降，自轮 15 起连续 27 轮红色、一次没退过；改窗口后 S 有升有降、等级能回落。
     ② **渠道权重**与**动作强度排行**成为规则级常量并进入评分（仿真侧：「渠道权重不是仅仅为了
        本轮，而是基础的预警规则」）：渠道权重六档全定义 —— 监管通报 3.0 / 黑猫投诉 2.5 /
        新闻门户 2.0 / 微博 1.5 / 小红书 1.5 / 抖音 1.5；动作强度 —— 通报 5 / 投诉引用 4 /
        引用 4 / 转发 3 / 评论 2 / 点赞 1。本跑通报与投诉引用 0 次、监管通报与黑猫投诉 0 帖，
        权重已定义未触发。
     ③ 阈值改为 红 ≥0.80、橙 0.65–0.80、黄 0.50–0.65、蓝 <0.50。
     ④ **删「前 5 帖被引份额」**（仿真侧：前 5 帖被引份额可以不看），维度三由「节点集中度」
        改为「参与强度」＝发声人数 + 动作强度加权互动 + 新生帖介数峰值。
     ⑤ **新增模块总结 #r40**（本模块原先没有），格式与模块一/二/三一致。
     ⑥ 新增表 4-1 预警规则常量；原表 4-1 / 表 4-2 顺延为表 4-2 / 表 4-3。
     改前版本存于 _版本留痕_第十七轮_20261013\ 。 -->
<h2 class="sec" id="s4">四 · 风险等级<span class="en">RISK LEVEL</span></h2>

<div class="card">
  <h3 class="sub">模块总结</h3>
  <div class="rep" id="r40"></div>
</div>

<div class="card">
  <div class="rep"><p class="lead">风险等级由<b>传播路径图谱、情绪演化曲线、关键影响节点</b>三项结果合成四级预警（红 / 橙 / 黄 / 蓝）。三个维度共八项观测量：<b>六项</b>（渠道加权帖量、发帖渠道种数、新增跨渠道边、发声人数、动作强度加权互动、新生帖介数峰值）在<b>5 轮滑动窗口</b>（约 20 小时，末尾对齐当前轮）上聚合，各自按本跑全程的窗口最大值归一；<b>另两项</b>（全场效价强度、负面人数占比）取<b>该轮即时值</b>，不跨轮。全部归一后按项内权重加权求和。渠道权重与动作强度排行是<b>规则级常量</b>：六个渠道、六类动作全部有定义，与某一次跑是否真有内容无关。合成公式固定、权重固定、阈值固定，逐轮可复算，不含人工判断。</p></div>

  <h3 class="sub">4.1 预警规则与三维度证据</h3>
  <div class="tbwrap"><table id="tbRule"></table></div>
  <p class="cap">表 4-1　预警规则常量。渠道权重与动作强度是<b>基础预警规则</b>，不随某一次跑的结果变化 —— __CAP_41__</p>

  <div class="tbwrap"><table id="tbDim"></table></div>
  <p class="cap">表 4-2　八项观测量中，<b>六项取截止轮所在窗口</b>的值（末尾对齐当前轮），<b>另两项取该轮即时值</b>。「观测量」列给出该窗口内的原始读数与全程窗口最大值，「归一化」一列为前者除以后者；右侧三列给出维度分、维度权重与加权贡献。窗口口径下总分 S 有升有降，等级可回落。</p>

  <h3 class="sub">4.2 预警等级时序</h3>
  <div id="chAlert" class="chart mid"></div>
  <p class="cap">图 4-1　逐轮风险总分与三维度分量。柱为总分 S（颜色＝该轮等级），折线为三个维度归一化分量，虚线为四级阈值线。每一轮的 S 与分量都取该轮自己的 5 轮窗口，因此逐轮有升有降。</p>

  <h3 class="sub">4.3 分级结论</h3>
  <div class="tbwrap"><table id="tbLevel"></table></div>
  <p class="cap">表 4-3　四级预警首次出现时点与持续情况。时点为该轮窗口起始时刻；「持续轮数」为该等级全程出现的轮数，不要求连续。</p>
  <div class="rep" id="r43"></div>
  <div class="fn" id="fnM4"></div>
</div>

<!-- ================= 模块五 ================= -->
<h2 class="sec" id="s5">五 · 应对建议<span class="en">RECOMMENDATIONS</span></h2>
<div class="card">
  <div class="rep"><div class="lead" id="r50"></div></div>
  <div id="advBox"></div>
  <div class="fn" id="fnM5"></div>
</div>

<!-- ================= 附录 A ================= -->
<h2 class="sec" id="sA">附录 A · 逐轮明细<span class="en">ROUND BY ROUND</span></h2>
<div class="card">
  <div class="desc">全部 __NROUNDS__ 轮推演结果。轮 0 为 t0 注入轮（无交互动作）；轮 __FIRSTROUND__—__LASTROUND__ 为推演轮。
    <span id="appALegend"></span>
  </div>
  <div class="tbwrap"><table id="tbRounds"></table></div>
</div>

<!-- ================= 附录 B ================= -->
<h2 class="sec" id="sB">附录 B · 溯源与口径说明<span class="en">TRACEABILITY</span></h2>
<div class="card">
  <h3 class="sub">B.1 结论溯源表</h3>
  <div class="rep"><p class="noind" style="text-indent:0">本报告每条结论均可回溯到具体数据来源与字段。</p></div>
  <div class="tbwrap"><table id="tbSrc"></table></div>

  <h3 class="sub">B.2 口径说明</h3>
  <div class="rep"><ol id="calib"></ol></div>
  <div class="foot">
    本报告由仿真推演结果自动生成，所有数值均由状态快照与逐轮记录现算得出，未经人工调整。<br>
    事件输入 → 结构化推演报告：五大模块（传播路径图谱 / 情绪演化曲线 / 关键影响节点 / 风险等级 / 应对建议）齐全。
  </div>
</div>

</div><!-- /wrap -->

<script>
var D = __PAYLOAD__;

/* ★ 段落级容错（2026-10-14 第二十轮）：任一段落抛异常只影响它自己，
   不再把后面所有段落一起清空（此前 PIV_CE 为 null 就曾把模块二之后的全部模块清空）。 */
var SEC_ERR = [];
function SEC(ln, fn){
  try { fn(); }
  catch(e){
    SEC_ERR.push('脚本第 ' + ln + ' 行：' + (e && e.message ? e.message : e));
    try { console.error('[报告渲染] 段落异常（脚本第 ' + ln + ' 行）', e); } catch(_){}
  }
}

/* ---------------- 常量与工具 ---------------- */
/* 六个平台是平台口径、颜色固定；出现**新渠道**时按调色板补色，不再 undefined。
   （`chChip()` 本就有 #8a97a8 兜底，这里补的是图例/堆叠柱的颜色。） */
var CH_PALETTE = ['#ff9500','#ff6b9d','#00c4cc','#5b8ff9','#13c2c2','#722ed1',
                  '#f6bd16','#1890ff','#f5222d','#52c41a','#eb2f96','#a0d911'];
var CH_COLOR = {'微博':'#ff9500','小红书':'#ff6b9d','抖音':'#00c4cc','新闻门户':'#5b8ff9',
                '监管通报':'#13c2c2','黑猫投诉':'#722ed1'};
D.chans.forEach(function(c){
  if(CH_COLOR[c]) return;
  CH_COLOR[c] = CH_PALETTE[Object.keys(CH_COLOR).length % CH_PALETTE.length]; });
/* ★ 2026-10-13 第十八轮：堆叠顺序由**配置 + 载荷**现算（原先写死 4 个平台）。
   顺序 = 配置 `channel_order` 里有帖的那些（保持配置次序）＋ 其余有帖渠道按载荷顺序追加；
   0 帖渠道不进图（图注会另行说明）。`__CH_ORDER__` 由 viz_build.py 注入。 */
var CH_ORDER = __CH_ORDER__;
var ROLE_COLOR = {'网民':'#f6bd16','客户':'#1890ff','KOL':'#722ed1','媒体':'#f5222d','监管':'#13c2c2'};
D.roles.forEach(function(r, i){                       /* 新角色按调色板补色 */
  if(!ROLE_COLOR[r]) ROLE_COLOR[r] = CH_PALETTE[(i + 6) % CH_PALETTE.length]; });
var LV_COLOR = {'红色':'#d4380d','橙色':'#fa8c16','黄色':'#e8b800','蓝色':'#1890ff'};
var CHS = D.chans, ROLES = D.roles, S = D.series, N = S.length;
var POSTS = D.posts, ND = D.nodes, CE = D.cross_events, AL = D.alert;
var META = D.meta;

/* ★ 2026-10-21 轮次基址（第 29 轮）：**轮号不一定等于下标**。
   老跑一轮 = 一轮，`series` 从轮 0 起、42 行，轮号 == 下标；
   新跑（满规则、t0 = 初始态）轮次记 1…42，`series` 仍是 42 行，但首行的 `round` 是 1。
   本文件里「拿轮号当下标」的地方原先全靠上面这个巧合成立 —— 首行偏移一格，
   `S[42]` 就是 undefined（`spanOf` 当场抛错，整段脚本崩掉、页面只剩骨架）。
   ⇒ 顶部只此一处定义 R0，凡「轮号 → 行」一律走 SROW / AROW；
      R0 = 0 时 SROW(r) 就是 S[r]、AROW(r) 就是 AL.rows[r] ⇒ 老跑输出**逐位不变**。
   注意 `D.gbar` / `D.ch_mat` / `D.rc_mat` 是 **按轮号索引**的数组（Python 侧长 NR，下标就是轮号）；
   而 `D.role_mat` 是 **按 series 行对齐**的（长 N，行 i ⇒ 轮 i+R0）。★ 2026-10-21 第三十轮实测：
   老跑两者都是 42 长、下标恰好等于轮号，这条差别一直藏着；新跑里 role_mat 长 42、ch_mat 长 43，
   混用就会整体错一格（r22 把网民的谷底印成「第 16 轮」，真值是轮 17）。 */
var R0 = S.length ? S[0].round : 0;
function SROW(r){ return S[r - R0]; }
function AROW(r){ return AL.rows[r - R0]; }
/* 渠道矩阵的**行对齐**副本：`CH_MAT_S[c][i] == D.ch_mat[c][S[i].round]`。
   凡「逐行与 S 比对」的算子（corrV / seriesMin / rankFinal / 折线）一律走它；
   仍要按下标取轮的地方（t0 = 轮 0、跨轮扫描）继续用 `D.ch_mat`。老跑 R0 = 0 ⇒ 两者相同。 */
var CH_MAT_S = {};
CHS.forEach(function(c){
  var col = D.ch_mat[c] || [];
  CH_MAT_S[c] = S.map(function(x){ return col[x.round]; }); });

/* ★ 2026-10-14 修（EVT-002 首次踩到）：
   `AL.first_round` **只含本跑真正到达过的等级**。事件温和时（EVT-002 峰值 S = 0.5726，
   等级只到黄色）它长这样：`{'蓝色':0,'黄色':3}` —— **没有 `橙色`、也没有 `红色` 键**。
   而模块四/五与摘要里原先满篇写 `AL.rows[AL.first_round['红色']]`，取到 undefined 后
   一解引用 `.round` / `.score` 就抛 TypeError；整个内联脚本是一个大 IIFE，一崩则
   **全部** DOM 注入丢失，页面只剩静态骨架（用户看到「只有框架、没有内容」就是这个）。
   下面这组量把「本跑实际到达的最高等级」抽出来，供模块四/五与摘要复用：
     HAS_RED  —— 本跑有没有到过红色（**只决定走哪套叙述，不改变任何数值**）
     TOP_LV   —— 实际到达的最高等级；TOP_ROW = 该等级首次出现的轮；TOP_N = 该等级轮数
     REACHED  —— 到达过的等级，按高→低（红色 → 橙色 → 黄色）
   EVT-001 恒为「红色」且 HAS_RED = true ⇒ 一律走原分支，输出**逐位不变**。 */
var REACHED = ['红色','橙色','黄色','蓝色'].filter(function(L){
                return AL.first_round[L] !== undefined; });
var HAS_RED = REACHED.indexOf('红色') >= 0;
var TOP_LV  = REACHED[0] || AL.rows[0].level;
/* TOP_I 是**轮号**（`first_round` 存的就是轮号），取行要过 AROW；见顶部 R0 注释。 */
var TOP_I   = AL.first_round[TOP_LV];
var TOP_ROW = TOP_I === undefined ? AL.rows[0] : AROW(TOP_I);
var TOP_N   = AL.rows.filter(function(a){ return a.level === TOP_LV; }).length;
/* t0 后多少小时才到达最高等级（两个分支共用，红色分支仍另算 hToRed 以保逐位一致） */
var H_TO_TOP = (wsec(TOP_ROW.window[0]) - wsec(META.t0)) / 3600e3;
/* ★ 2026-10-14 第二十二轮：摘要（exec summary）原先无条件写「此后等级回落，并未一路维持到推演结束」。
   等级是否回落是数据决定的 —— 峰值落在末轮时（EVT-004 峰值轮 41 = 末轮）该句为假。这里在顶层
   算一次等级回落次数，供摘要与模块四共用（模块四的那份 `lvDrops` 是自己的局部量）。 */
var LV_ORDER0 = ['蓝色','黄色','橙色','红色'];
var LV_DROPS  = AL.rows.reduce(function(acc, a, i){
  return acc + ((i && LV_ORDER0.indexOf(a.level) < LV_ORDER0.indexOf(AL.rows[i-1].level)) ? 1 : 0);
}, 0);

function chip(t, c){ return '<span class="chip" style="background:'+c+'">'+t+'</span>'; }
function chChip(c){ return chip(c, CH_COLOR[c] || '#8a97a8'); }
function rlChip(r){ return chip(r, ROLE_COLOR[r] || '#8a97a8'); }
function lvChip(l){ return chip(l, LV_COLOR[l] || '#8a97a8'); }
function f3(v){ return (v===null||v===undefined) ? '—' : (+v).toFixed(3); }
function f2(v){ return (v===null||v===undefined) ? '—' : (+v).toFixed(2); }
function f0(v){ return (v===null||v===undefined) ? '—' : String(Math.round(v)); }
function n0(v){ return Math.round(v); }
function n1(v){ return (+v).toFixed(1); }
function pct(v, d){ return (v===null||v===undefined) ? '—' : (100*v).toFixed(d===undefined?1:d)+'%'; }
function setHTML(id, h){ var e = document.getElementById(id); if(e) e.innerHTML = h; }
/* ★ 2026-10-12 模块一改版新增：按 id 取元素。
   给**变量拼出来的** id（拓扑时代的 'chGd'+i / 'tab'+i / 'gdHd'+i）统一走这里，
   而不是把前缀字面量直接写进按 id 取元素的调用里 —— 一来可读，
   二来 `_mkchk.py` 是用正则扫**单引号字面量**的，
   动态拼出来的前缀会被它误报成「JS 引用但 HTML 缺失的 id」。
   拓扑删除后调用方只剩 mkEl，保留是为了恢复拓扑时不必再铺线。 */
function el(s){ return document.getElementById(s); }
/* ★ 2026-10-20 第二十七轮：LLM 补充解读块。
   数据包里没有 `llm`（或该键缺失）⇒ 返回空串，段落与从前**逐字相同**。
   文本只做解读：数字与结论一律取自本报告已印出的正文；模型名与提示词指纹一并印在标题右侧，
   便于溯源 —— 读者一眼能看出哪一段是模型写的、哪一段是公式写死的。 */
/* ★ 2026-10-20 第二十八轮：模型写的中文常把数字连写（「轮6」「t0后约12小时」「S接近0.80」），
   而本报告其余部分一律是「轮 6」「t0 后约 12 小时」的中西文间空格。
   ⇒ **只在渲染时补空格**，不改 `_llm_text.json` 里的原文（留痕仍是模型原样输出）。 */
function spCJK(s){
  return String(s)
    .replace(/([一-鿿])([0-9A-Za-z])/g, '$1 $2')
    .replace(/([0-9A-Za-z%])([一-鿿])/g, '$1 $2');
}
function llmBlock(key, label){
  var L = (D && D.llm && D.llm.items) ? D.llm.items[key] : null;
  if(!L || !L.text) return '';
  return '<div class="llmnote"><div class="hd">'+label+
         '<span class="mt">模型撰写 · '+(D.llm.model || '')+
         (L.prompt_sha256 ? ' · 提示词指纹 ' + L.prompt_sha256 : '')+'</span></div>'+
         '<div class="bd">'+spCJK(L.text)+'</div></div>';
}
function sum(a, f){ return a.reduce(function(s,x){ return s + f(x); }, 0); }
function p(t){ return '<p>'+t+'</p>'; }
function pn(t){ return '<p class="noind" style="text-indent:0">'+t+'</p>'; }

/* ---------------- 派生统计 ----------------
   ★ 2026-10-12 口径修正：凡是「互动量」一律用 `flow`（= 评论 + 转发 + 点赞的**动作数**，
   是流量、可加总），**不再用 `inter`** —— `inter` 是记录员的 `interaction_total`，
   即「当前在视野内的帖子累计被赞+评论」的**存量快照**（recorder_a.py:132），
   帖子离开视野时会下降（本跑 42 轮里 12 轮下降），按轮/按日求和 = 把同一批互动反复重计。
   本跑 Σflow = 496 次，而 Σinter = 5224 次（虚增 10.5×）。`inter` 仅留作审计。 */
var TOTAL_INTER = sum(S, function(x){ return x.flow; });
/* 审计用：存量的求和与下降轮数 —— 只在附录的「口径修正留痕」里引用，不参与任何图表。 */
var TOTAL_INTER_STOCK = sum(S, function(x){ return x.inter; });
/* ★ 2026-10-12 第九轮：**动作总量** = 发帖 + 评论 + 转发 + 点赞 = 互动量 + 发帖量。
   命题把「互动总量」定义为这四者之和（见仿真侧的 3.1 图表说明），关键时段即按它取 argmax；
   `flow` 只含评论+转发+点赞，两者差的就是发帖量。本跑 622 = 126 + 496。 */
var TOTAL_ACT = sum(S, function(x){ return x.post + x.comment + x.repost + x.like; });
var N_STOCK_DROP = S.filter(function(x, i){ return i > 0 && x.inter < S[i-1].inter; }).length;
var V_MEAN = sum(S, function(x){ return x.valence; }) / N;

/* 精确按发布日的帖子量 / 渠道 / 角色 */
var DAYS = [];
POSTS.forEach(function(x){ var k = x.t.slice(0,10); if(DAYS.indexOf(k)<0) DAYS.push(k); });
/* ★ 2026-10-14 修：把「有轮次覆盖、但当天没有新帖」的日期也补进 DAYS/BD。
   原先 DAYS/BD 只按**帖子发布日期**建键，而 `DAILY`（= D.daily，来自 viz_extract）按
   **轮次窗口**覆盖全部日期 ⇒ 尾日 0 帖时 `BD[那天的日期]` 是 undefined，下游
   `BD[x.date].n`（如本块下方的 `PEAK_DAY_POST` / `dPost` / `pMin` / `pMax`）直接抛
   TypeError；整个内联脚本是一个大 IIFE，一崩则**全部** DOM 注入丢失，页面只剩静态骨架。
   EVT-002 首次踩到（末段 8.8h 无新帖）。EVT-001 每天都有帖、两集合恒等 ⇒ 本改动对
   其输出**逐位无影响**（0 帖日补进来后 `BD[k].n = 0`，那是真实值）。 */
(D.daily || []).forEach(function(x){ if(DAYS.indexOf(x.date) < 0) DAYS.push(x.date); });
DAYS.sort();
var BD = {};
DAYS.forEach(function(k){ BD[k] = {n:0, ch:{}, role:{}}; });
POSTS.forEach(function(x){
  var k = x.t.slice(0,10), o = BD[k];
  o.n++; o.ch[x.ch] = (o.ch[x.ch]||0)+1; o.role[x.role] = (o.role[x.role]||0)+1;
});

/* ★ 2026-10-12 模块一改版：每日传播拓扑需要「当天新增的引用边」。
   载荷里**没有边表**（引用关系只有父指针 posts[].q），故在此现推。
   边的时刻取**引用者（子帖）自己**的发布时间 —— 即「这条引用发生在哪一天」，
   而不是被引父帖的发布时间。
   ★ 2026-10-12 第三轮改版：拓扑已删，本块的消费方（`BD[].edges/.dmax/.newch`）
   目前**没有任何图表或表格在读**，保留见下方模块一的说明。 */
var byId = {}; POSTS.forEach(function(x){ byId[x.id] = x; });
var DAY_EDGES = {};
DAYS.forEach(function(k){ DAY_EDGES[k] = []; });
POSTS.forEach(function(x){
  var k = x.t.slice(0,10);
  if(x.q && byId[x.q] && DAY_EDGES[k]) DAY_EDGES[k].push({source:x.q, target:x.id});
});
DAYS.forEach(function(k){
  var ag = {}, dm = 0;
  POSTS.forEach(function(x){
    if(x.t.slice(0,10) !== k) return;
    ag[x.agent] = 1; if(x.depth > dm) dm = x.depth;
  });
  BD[k].edges  = DAY_EDGES[k].length;   /* 当天新增的引用边数 */
  BD[k].agents = Object.keys(ag).length;/* 当天发声的账号数 */
  BD[k].dmax   = dm;                    /* 当天新帖达到的最深引用层 */
  BD[k].newch  = CE.filter(function(e){ return e.window[0].slice(0,10) === k; }).length;
});
/* ---------------- 轮长 / 关键时段（R-50：一律现推，不写死轮号）----------------
   ★ 原先这里写着 `var WIN = {lo:20, hi:23}; var WIN_HOURS = 16;`，附录 A 的斑马纹
     写着 `round<=11 / round<=23` 与 '2h' / '4h' / '8h'，情绪图的 markArea 也写死了
     同样的轮号。**轮号是轮次表的函数**：换表后同一个号落到完全不同的钟点，
     报告不会报错，只会静默换掉「关键时段」并错标每一行的轮长。
     R-50 全部改为从快照自身的 `window` 现推。
   ⚠ 遗留（重跑后必须重审）：关键时段的**锚点钟点** 05-25 06:00（t0 + 56h）是当初
     人工挑的，不是任何一个判据的 argmax —— 实测（42 轮 / 39 个 16 小时窗口，互动量已改
     流量口径）按互动量排第 11、按负面占比排第 16、按「负面 × 互动」排第 10；该窗口的
     互动密度只有全程平均的 1.10 倍（见 `_probe_win2.py`）。改成按钟点锚定后，换表仍落回
     同一段真实时间，但**这个钟点该不该挪**要重新判一次。 */
function wsec(s){ return Date.parse(s.replace(' ','T') + ':00Z'); }
function spanOf(x){
  var h0 = +x.window[0].slice(11,13), h1 = +x.window[1].slice(11,13);
  /* ⚠ 必须 `(+24)%24` 而不是 `%24`：**JS 的 `%` 跟符号走**，
     `(0-22) % 24 === -22`（Python 里是 +2）。本表有 7 个窗口起点在 22:00、
     跨零点，用错会把每一段都切成单轮。 */
  return ((h1 - h0 + 24) % 24) || 24;       /* 跨零点的窗口：22:00→02:00 记 4h */
}

/* 轮长「段」：把连续等长的轮归成一段。等长轮次表下只有一段 ⇒ 退化为按自然日分带，
   否则 42 行的表格没有任何分带。 */
var SEG_SET = {};
S.forEach(function(x){ SEG_SET[spanOf(x)] = 1; });
var SEG_UNIFORM = (Object.keys(SEG_SET).length <= 1);
var SEG_COL = [{seg:'seg1', chip:'#8ab6f0', area:'rgba(90,140,220,.07)'},
               {seg:'seg2', chip:'#f0c48a', area:'rgba(240,170,60,.09)'},
               {seg:'seg3', chip:'#b8a8e8', area:'rgba(150,120,220,.09)'}];
var BANDS = (function(){
  var runs = [], cur = null;
  S.forEach(function(x){
    var p = spanOf(x);
    if(cur && cur.span === p){ cur.hi = x.round; }
    else { cur = {lo:x.round, hi:x.round, span:p}; runs.push(cur); }
  });
  var uniform = SEG_UNIFORM, list;
  if(uniform){                                /* 等长：按自然日分带 */
    list = []; var d = null;
    S.forEach(function(x){
      var k = x.window[0].slice(0,10);
      if(d && d.date === k){ d.hi = x.round; }
      else { d = {lo:x.round, hi:x.round, date:k}; list.push(d); }
    });
  } else { list = runs; }
  return list.map(function(b, i){
    var c = SEG_COL[i % 3], sp = spanOf(SROW(b.lo));   /* b.lo 是轮号 → 行 */
    return {lo:b.lo, hi:b.hi, seg:c.seg, chip:c.chip, area:c.area, cell: sp + 'h',
            chipText: uniform
              ? (b.date.slice(5) + '（轮 ' + b.lo + '—' + b.hi + '）')
              : (sp + ' 小时/轮（轮 ' + b.lo + '—' + b.hi + '）')};
  });
})();
var BAND_OF = {};
BANDS.forEach(function(b, i){ for(var r = b.lo; r <= b.hi; r++) BAND_OF[r] = i; });

/* 附录 A 的行底色图例 + 情绪图 2-1 的阴影说明（两处都随轮长表现推，不写死段界） */
setHTML('appALegend', '行底色' + (SEG_UNIFORM ? '按自然日分带' : '对应轮长区间') + '：' +
  BANDS.map(function(b){
    return '<span class="chip" style="background:'+b.chip+'">'+b.chipText+'</span>';
  }).join(' ') +
  (SEG_UNIFORM ? '；本表轮长等长（' + spanOf(S[0]) + ' 小时/轮，共 ' + N + ' 轮）。' : '。'));
/* 阴影说明**两种情形都写**（不靠 HTML 里的静态兜底文本）：一来等长表下要说的话不一样，
   二来无头冒烟测试的 DOM 桩不解析静态 HTML，只写一半会让它把 `segNote` 报成「未填充」。*/
setHTML('segNote', SEG_UNIFORM
  ? '本表轮长等长（' + spanOf(S[0]) + ' 小时/轮），故不设轮长阴影区。'
  : '阴影区为' + (BANDS.length === 3 ? '三段' : BANDS.length + ' 段') + '轮长区间（' +
    BANDS.map(function(b){ return spanOf(SROW(b.lo)) + 'h'; }).join(' / ') + '）。');

/* ★ 2026-10-12 第九轮改版：关键时段改由**数据**定义 —— 长度固定 4 轮的滑动窗口中，
   「动作总量」（发帖＋评论＋转发＋点赞）之和最大的那一段。
   原先是按**钟点**锚定的「t0 + 56 小时 = 05-25 06:00」：那是一个**人挑的**锚点，
   不是任何判据的 argmax（R50run README §5 待定第 2 条已挂账）。
   本轮 3.1 的文字要求是「识别互动量最高的时间窗」，人挑的锚点回答不了这个问句，
   故改为 argmax。窗口长度仍固定 4 轮（= 16 小时），密度比较与旧口径可直接对照。 */
var RACT = S.map(function(x){ return x.post + x.comment + x.repost + x.like; });  /* 逐轮动作总量 */
var WIN_N = 4;
var WIN_LO = 0, WIN_ACT = -1;
for(var _i = 0; _i + WIN_N <= N; _i++){
  var _s = 0;
  for(var _j = _i; _j < _i + WIN_N; _j++) _s += RACT[_j];
  if(_s > WIN_ACT){ WIN_ACT = _s; WIN_LO = _i; }
}
var WIN = {lo: WIN_LO, hi: WIN_LO + WIN_N - 1};
var WIN_HOURS = 0;
for(var _w = WIN.lo; _w <= WIN.hi; _w++) WIN_HOURS += spanOf(S[_w]);
var WIN_INTER = sum(S.filter(function(x){ return x.round>=WIN.lo+R0 && x.round<=WIN.hi+R0; }),
                    function(x){ return x.flow; });   /* 流量口径，见 TOTAL_INTER 注释 */
/* ★ R-50：关键时段的**成分**（负面区间、发声主体、媒体发帖数）原先在模块五里写死
   （「负面占比稳定在 98% 以上」「媒体发帖 0 条」「发声主体全为客户」）—— 改为现算。 */
var WIN_ROWS = S.slice(WIN.lo, WIN.hi + 1);
var WIN_NEG_MIN = Math.min.apply(null, WIN_ROWS.map(function(x){ return x.neg; }));
var WIN_NEG_MAX = Math.max.apply(null, WIN_ROWS.map(function(x){ return x.neg; }));
var WIN_POSTS = POSTS.filter(function(p){ return p.r >= WIN.lo+R0 && p.r <= WIN.hi+R0; });
/* ★ 2026-10-14 第二十二轮：`WIN_MED_POST`（窗口内**媒体**帖数）已撤 —— 建议 7 原先写
   「发声主体为 WIN_BODY（媒体发帖 N 条）」，角色名写死为媒体；现直接由 `WIN_BODY` 现算该角色的窗口内帖数。 */
/* ★ 2026-10-14 第二十二轮：改为返回 {label, n}。原先只返回标签，调用处再拿标签去 filter 帖数 ——
   「多个角色（媒体与KOL并列）」这种描述串当角色键用，会数出 0 条并印成
   「发声主体为多个角色（媒体与KOL并列）（多个角色（媒体与KOL并列）在窗口内发帖 0 条）」。 */
var WIN_BODY = (function(){
  var c = {}; WIN_POSTS.forEach(function(p){ c[p.role] = (c[p.role]||0) + 1; });
  var ks = Object.keys(c); if(!ks.length) return {label:'无', n:0};
  ks.sort(function(a,b){ return c[b] - c[a]; });
  if(ks.length > 1 && c[ks[0]] === c[ks[1]]){
    return {label:'多个角色（'+ks[0]+'与'+ks[1]+'并列）', n:c[ks[0]]};
  }
  return {label:ks[0], n:c[ks[0]]};
})();
var PEAK_INT = S.reduce(function(a,x){ return x.flow>a.flow?x:a; }, S[0]);   /* 流量口径 */
/* ★ 2026-10-20 第二十九轮：`PEAK_INT` 取的是**首个**最大值，并列时原句仍说「峰值在轮 N」。
   EVT-002 的轮 15 与轮 39 都是 11 次，只印轮 15 会把另一个峰值隐掉。存下全部并列轮，
   叙述句按有没有并列补一句。 */
var PEAK_INT_R  = S.filter(function(x){ return Math.abs(x.flow - PEAK_INT.flow) < 1e-9; })
                   .map(function(x){ return x.round; });
var PEAK_INT_TIE = PEAK_INT_R.length > 1
  ? '，与'+PEAK_INT_R.filter(function(r2){ return r2 !== PEAK_INT.round; })
       .map(function(r2){ return '轮 '+r2; }).join('、')+' 并列'
  : '';
/* ★ R-50：r31 原先写死「轮 '+WIN.lo+' 以 X 次次之」——那是上一跑的次高轮。
   改由数据现取"互动量第二高的轮"。 */
var SEC_INT = S.slice().sort(function(a,b){ return b.flow - a.flow; })[1];
var PEAK_NEG = S.reduce(function(a,x){ return x.neg>a.neg?x:a; }, S[0]);
/* ★ 2026-10-14 第二十二轮：负面占比峰值恰为 100% 时，r30 / r33 原先无条件写「全程唯一一次
   全场智能体均处于负面」——「唯一」二字从未核对过。改为按全程实际达到 100% 的轮数现算。 */
var NEG_FULL   = S.filter(function(x){ return x.neg >= 0.9999; });
var NEG_FULL_N = NEG_FULL.length;
/* ★ 2026-10-14 第二十二轮：r40 原先写死「负面情绪自轮 2 起就长期贴在负区间」——那是 EVT-001
   的数据（轮 0—1 的效价尚为正）。EVT-005 自 t0 起效价即为负（-0.007），这一句当场为假。
   改为取「最后一个非负轮之后的那一轮」；全程没有非负轮时退化为「自 t0 起」。 */
var V_NEG_FROM = (function(){
  var last = -1, anyNeg = false;
  for(var i = 0; i < N; i++){
    if(S[i].valence < 0) anyNeg = true;
    if(S[i].valence >= 0) last = i;
  }
  /* anyNeg=false ⇒ 全程效价从未跌破 0（EVT-002 全程 +0.035~+0.042）；
     allNeg=true  ⇒ 自 t0 起就一直为负（EVT-001/003/004/005 都是这一种）；
     ok=false     ⇒ 最后一个非负轮就是末轮，负区间没有延续到结尾。 */
  return {r:last + 1, anyNeg:anyNeg, allNeg:(anyNeg && last === -1), ok:(last + 1 < N)};
})();
var LOW_V    = S.reduce(function(a,x){ return x.valence<a.valence?x:a; }, S[0]);
/* ★ 2026-10-14 第二十二轮：2.1 正文原先无条件写「全程维持在负值区间」「深度负面已经固化为
   全场基调」——那是「效价全程为负」的那一类事件（EVT-001/003/004/005）的形状。EVT-002 全程
   效价在 +0.035~+0.048 之间、从未跌破 0，这两句当场为假。这里把效价区间现算出来，
   连同上面 `V_NEG_FROM.anyNeg`，供 2.1 的三处叙述分档。 */
var V_HI     = S.reduce(function(a,x){ return x.valence>a.valence?x:a; }, S[0]).valence;
var V_LO     = LOW_V.valence;
var V_SPAN   = V_HI - V_LO;
/* ---- 自然平息：两个必要条件 + 一个分级（★ 2026-10-20 第二十七轮新增） ----
   仿真侧 2026-10-20 的口径：「发帖量是要的，最后一天变得特别少可以是平息的表现；效价上升确实是
   平息的信号，其次是没有那么的低。」⇒ 拆成两条必要条件与一条分级：
     ① 量能回落  H = clamp((1 − 末6轮发帖合计 / 全程最大6轮发帖合计) / 0.50, 0, 1)
        H ≥ 0.50 ⇔ 末 24 小时发帖量已降到全程最旺 24 小时的 75% 以下
     ② 情绪转好  V = 0.6·V_up + 0.4·V_lvl
        V_up  = clamp((末6轮效价均值 − 全程最低效价) / max(全程极差, 0.10), 0, 1)
                —— 「回升了多少」；分母取 0.10 兜底，极差比 0.10 还小时按 0.10 算，
                   免得一次极小的抬升被放大成满分
        V_lvl = 末 6 轮效价均值所处档：强负(<−0.30)=0 / 负[−0.30,−0.10)=0.25 /
                中[−0.10,0.10)=0.50 / 正[0.10,0.30)=0.75 / 强正(≥0.30)=1.00
                —— 「现在还低不低」；只看回升不看绝对水平，一条从 −0.60 回到 −0.45 的曲线
                   会被误判成回暖，所以必须再挂一档
        V ≥ 0.35
     ③ 不满面收窄 N = clamp((负面占比的6轮均值峰值 − 末6轮均值) / 0.10, 0, 1)
        仅在 ①② 都过时才用于分级：N ≥ 0.50 ⇒ 已平息，否则 ⇒ 趋于平息
   ⇒ 判定三档：已平息 / 趋于平息 / 未平息；未平息时 `CALM.why` 说明卡在哪一条。
   ★ 这里只算数、不拼句子；三个指数的文字在模块二 2.1 与模块总结里各写一次。 */
var CALM = (function(){
  var W = 6, i, k, t;
  var w6 = [];
  for(i = 0; i + W <= N; i++){ t = 0; for(k = 0; k < W; k++) t += S[i + k].post; w6.push(t); }
  var wLast = w6[w6.length - 1], wMax = Math.max.apply(null, w6);
  var H = (wMax > 0) ? Math.max(0, Math.min(1, (1 - wLast / wMax) / 0.50)) : 0;

  var vTail = 0;
  for(k = 0; k < W; k++) vTail += S[N - W + k].valence;
  vTail /= W;
  /* ★ 2026-10-20：分母原先写死 `max(V_SPAN, 0.10)`。全程极差本来就小的跑（EVT-002 只有
     0.0176、EVT-005 只有 0.0172）要拿到 0.35 分，得回升 0.035 —— 比它**整条曲线的全部
     起伏**还大，这在结构上永远不可能 ⇒ 这类跑无论怎么走都判「情绪没转好」。
     回升量本来就该按**本跑自身尺度**衡量，故下限压到 0.02（只在极差近乎退化时兜底）。 */
  var vUp = Math.max(0, Math.min(1, (vTail - V_LO) / Math.max(V_SPAN, 0.02)));
  var vLvl = (vTail < -0.30) ? 0.00 : (vTail < -0.10 ? 0.25 : (vTail < 0.10 ? 0.50 : (vTail < 0.30 ? 0.75 : 1.00)));
  var Vc = 0.6 * vUp + 0.4 * vLvl;
  var vBand = (vTail < -0.30) ? '强负' : (vTail < -0.10 ? '负' : (vTail < 0.10 ? '中' : (vTail < 0.30 ? '正' : '强正')));

  var n6 = [];
  for(i = 0; i + W <= N; i++){ t = 0; for(k = 0; k < W; k++) t += S[i + k].neg; n6.push(t / W); }
  var nPeak = Math.max.apply(null, n6), nLast = n6[n6.length - 1];
  var Nq = Math.max(0, Math.min(1, (nPeak - nLast) / 0.10));

  var needH = H >= 0.50, needV = Vc >= 0.35;
  var level = (!needH || !needV) ? '未平息' : (Nq >= 0.50 ? '已平息' : '趋于平息');
  var title = (level === '已平息') ? '<b>已自然平息</b>'
            : (level === '趋于平息' ? '<b>趋于平息、但尚未完全平息</b>' : '<b>未出现自然平息</b>');
  var why = [];
  if(!needH) why.push('量能没有回落');
  if(!needV) why.push('情绪没有转好');
  return {w:wLast, wMax:wMax, H:H, vTail:vTail, vMin:V_LO, vUp:vUp, vLvl:vLvl, vBand:vBand,
          V:Vc, nPeak:nPeak, nLast:nLast, Nq:Nq, needH:needH, needV:needV,
          level:level, title:title, why:why.join('、')};
})();
/* ---- 修复通道：连续 6 轮（24 小时）窗口的三条联合判据（★ 2026-10-20 第二十七轮新增） ----
   为什么是 6 轮：本引擎每轮 4 小时、每 6 轮正好一个自然日 ⇒「6 轮窗口」＝「一天之内」，
     与仿真侧认可的「24 小时」口径一致（轮长变化时 `spanOf()` 会跟着变，这里用轮数不用小时）。
   为什么要三条一起看：
     ① 效价净回升 Δv = val[i+6] − val[i] ≥ max(0.02, 0.10 × 全程极差)
        单看回升会被噪声骗，故门槛取「绝对 0.02」与「极差的 10%」的较大者 —— 事件本身起伏大时门槛跟着抬高；
     ② 负面占比净下降 Δneg = neg[i+6] − neg[i] ≤ −0.02
        效价回升可能只是少数账号变温和；人数面没动就不算修复，故同时要求负面人数收窄 2 个百分点；
     ③ 窗口内新增跨渠道渠道边 = 0（`newcross` 合计）
        这 24 小时里内容若正跨平台扩散，效价回升更可能来自新受众的稀释，而不是修复。
   ⇒ 三条都满足才算「进入修复通道」。`REPAIR.best` 记下**效价回升最多**的那个窗口，
     即使一个都没进，也能说出「最接近的是哪个、差在哪一条」。 */
var REPAIR = (function(){
  var W = 6, i, k, hits = [], best = null;
  var floor = Math.max(0.02, 0.10 * V_SPAN);
  for(i = 0; i + W < N; i++){
    var dv = S[i + W].valence - S[i].valence;
    var dn = S[i + W].neg - S[i].neg;
    var eg = 0;
    for(k = 1; k <= W; k++) eg += (S[i + k].newcross || 0);
    var rec = {lo:i, hi:i + W, dv:dv, dn:dn, eg:eg,
               pass:(dv >= floor ? 1 : 0) + (dn <= -0.02 ? 1 : 0) + (eg === 0 ? 1 : 0)};
    rec.ok = (rec.pass === 3);
    rec.unmet = [];
    if(dv < floor) rec.unmet.push('效价回升幅度');
    if(dn > -0.02) rec.unmet.push('不满面是否收窄');
    if(eg !== 0) rec.unmet.push('期间仍在跨渠道扩散');
    if(rec.ok) hits.push(rec);
    /* 「最接近」按**满足条数**优先、条数相同时按效价回升量 —— 只按回升量会挑出一个
       两条不满足的更大跌幅窗口，读起来像「最接近」，其实不是。 */
    if(!best || rec.pass > best.pass || (rec.pass === best.pass && rec.dv > best.dv)) best = rec;
  }
  return {w:W, floor:floor, hits:hits, best:best, n:N - W, has:(hits.length > 0)};
})();
var PEAK_POST= S.reduce(function(a,x){ return x.post>a.post?x:a; }, S[0]);
var DAILY    = D.daily;
var PEAK_DAY = DAILY.reduce(function(a,x){ return x.flow>a.flow?x:a; }, DAILY[0]);   /* 流量口径 */
var PEAK_DAY_POST = DAILY.reduce(function(a,x){ return BD[x.date].n > BD[a.date].n ? x : a; }, DAILY[0]);

var RO = D.ro_stat, CO_AG = D.ch_stat;
/* ★ 2026-10-14 第二十一轮 · 渠道口径统一：`CO` 一律按**帖的原发渠道**（`posts[].ch`）聚合，
   不再用载荷里的 `ch_stat`（那是按**发帖人所属渠道**聚合的账号口径）。
   为什么要换：报告里凡是写「N 条帖」的地方读的都是 `CO[..].posts`，而 1.2 的图与表 1-2 的日列
   （`BD`）以及 3.3 自己写下的定义（「该渠道帖子被其他帖子引用的累计次数 ÷ 该渠道发帖数」）
   用的都是**帖**口径。两个口径在 EVT-001 上逐字段完全一致（故该交付报告逐字节不变），
   但换事件就分叉（EVT-004：监管通报 4 → 0 条、新闻门户 28 → 33 条、小红书 10 → 9 条），
   混用会让同一句里出现「监管通报 4 条（0.0%，覆盖 0 天）」这类自相矛盾的表述。
   仍取账号口径的字段只有两个：`agents`（「某渠道无发帖、但仍有 N 个智能体在关注」那句要用在册数）
   与 `zero`（表 3-2「零被引账号」列，与同排的「活跃账号」成对读，图 3-2 的提示框也写作 `zero / agents`）。
   这两个字段是**账号普查量**，第 3-2 表下方图注本来就写明「两者口径不同」，故与帖口径字段同排并不矛盾；
   若把 `zero` 也改成「发帖账号中零被引的个数」，同一个数就会从 11 掉到 1（抖音），是**改语义**而非改口径。 */
var CO = (function(){
  var agg = {};
  function slot(c){
    return agg[c] || (agg[c] = {agents:0, posters:0, posts:0, out:0, betw:0, inter:0,
                                zero:0, out_per_post:0, _byAgent:{}});
  }
  POSTS.forEach(function(x){
    var s = slot(x.ch);
    s.posts += 1; s.out += x.indeg; s.betw += x.betw; s.inter += x.inter;
    s._byAgent[x.agent] = (s._byAgent[x.agent] || 0) + x.indeg;
  });
  Object.keys(CO_AG).forEach(function(c){
    var t = slot(c); t.agents = CO_AG[c].agents || 0; t.zero = CO_AG[c].zero || 0;
  });
  Object.keys(agg).forEach(function(c){
    var s = agg[c];
    s.posters = Object.keys(s._byAgent).length;
    delete s._byAgent;
    s.out_per_post = +(s.out / Math.max(1, s.posts)).toFixed(3);
  });
  return agg;
})();

/* ★ 2026-10-13 第十八轮：分类法**安全取值器**。
   载荷本身是事件无关的（角色 5 个 / 渠道 6 个是平台口径），但**本跑实际产出**只覆盖其中
   一部分 —— 有的跑里「监管」角色与「监管通报」「黑猫投诉」两个渠道全程 0 帖。
   报告层多处按名字直取（`RO` / `CO` 后面跟中文字面量下标），缺位时是 undefined，再取 `.posts` 当场抛错，
   整块文案连同后面的内容一起消失。故一律走取值器：
     在册 ⇒ 返回**真对象**（数值逐位不变）；不在册 ⇒ 返回零值中性对象。
   `RM(n)` 是「角色 × 轮次 情绪矩阵」的同款兜底（缺位 → 全 null 序列）。 */
var BLANK_STAT = {agents:0, posters:0, posts:0, out:0, betw:0, inter:0, zero:0, out_per_post:0};
function ROof(n){ return RO[n] || BLANK_STAT; }
function COof(n){ return CO[n] || BLANK_STAT; }
var _RM_EMPTY = null;
function RM(n){
  if(_RM_EMPTY === null){ _RM_EMPTY = []; for(var _i = 0; _i < N; _i++) _RM_EMPTY.push(null); }
  return (D.role_mat && D.role_mat[n]) || _RM_EMPTY; }
function CN_NUM(n){ var a = ['零','一','两','三','四','五','六','七','八','九','十'];
  return (typeof n === 'number' && n >= 0 && n < a.length) ? a[n] : String(n); }
/* ★ 2026-10-20 第二十八轮：序数「第 N」不能直接用 CN_NUM —— 它给 2 的是「两」，
   拼出来是「第两轮」（EVT-008 实测），序数要单独一套。
   另加 clsJoin：角色/渠道并列三个以上时，一路「与」下去会变成
   「KOL类账号与网民类账号与媒体类账号」（EVT-008 实测），改为「A、B与C三类账号」。 */
var ORD_NUM = ['零','一','二','三','四','五','六','七','八','九','十'];
function ORDINO(n){ return (typeof n === 'number' && n >= 0 && n < ORD_NUM.length)
                             ? ORD_NUM[n] : String(n); }
function clsJoin(arr, unit){
  var m = arr.length;
  if(!m) return '';
  if(m === 1) return arr[0]+'类'+unit;
  if(m === 2) return arr[0]+'与'+arr[1]+'类'+unit;
  return arr.slice(0, m-1).join('、')+'与'+arr[m-1]+
         (m <= 10 ? CN_NUM(m)+'类' : ' '+m+' 类')+unit;
}

/* 引用深度分层（1.1 与 3.5 共用） */
var LAYER = {}; POSTS.forEach(function(x){ LAYER[x.depth] = (LAYER[x.depth]||0)+1; });
var TOP2_SHARE = ((LAYER[1]||0) + (LAYER[2]||0)) / META.posts;
/* 帖子数最多的那一层（深度 > 0，即除 t0 根帖外）—— 供 2.2/3.5 交叉引用 1.1 的结论用。
   ★ 2026-10-12：原先 3.5 引用的是上一跑的「第 3 层起收窄」，本跑最大层其实是第 4 层。 */
var MAXLAYER = (function(){
  var best = -1, bn = -1;
  Object.keys(LAYER).forEach(function(d){
    if(+d > 0 && LAYER[d] > bn){ bn = LAYER[d]; best = +d; }
  });
  return best;
})();
/* 跨渠道事件的发起角色串（按次数降序）—— 摘要与 3.5 的交叉引用共用，避免两处写死不一致。 */
var CE_ROLE_STR = (function(){
  var c = {}; CE.forEach(function(e){ c[e.role] = (c[e.role]||0)+1; });
  /* ★ 第二十八轮：三个角色以上改走 `clsJoin` —— 原先一路「与」下去，
     EVT-008 印出「KOL类账号与网民类账号与媒体类账号」。 */
  return clsJoin(Object.keys(c).sort(function(a,b){ return c[b] - c[a]; }), '账号');
})();

/* 跨渠道传播的「来源 × 到达」组合数。
   ★ R-50：原先图注与附录里写死「15 条弧线」，改由 Python 侧现推（__ARCN__）。
   ★ 2026-10-12 第四轮：桑基图撤掉后，「去重通道数」不再有呈现（__ARCLINK__ 已删），
     附录 B 的口径段只说组合数。 */
var ARC_N = __ARCN__;      /* 来源 × 到达 的组合数 */

/* 跨渠道传播的发起账号（去重） */
var CE_AG = {}; CE.forEach(function(e){ CE_AG[e.name] = e.role; });
var CE_AG_N = Object.keys(CE_AG).length;
/* ★ 2026-10-12：原先这里写死 `CE_MED_N` / `CE_KOL_N` 两个**只数「媒体、KOL」**的变量，
   模块五的建议 2 再拼成「都由媒体类或 KOL 类账号发起」——角色名是写死的字符串，
   若某跑出现客户 / 网民发起的跨渠道事件，这句话即为假且不会报错。改为按 CE 现算角色构成，
   并把「客户与网民从未成为载体」这半句降为**条件断言**（确认没有才写）。 */
var CE_ROLE_EV = {};                       /* 事件次口径：每个角色发起了几次 */
CE.forEach(function(e){ CE_ROLE_EV[e.role] = (CE_ROLE_EV[e.role]||0)+1; });
var CE_ROLE_AG = {};                       /* 账号数口径：每个角色涉及几个账号 */
Object.keys(CE_AG).forEach(function(n){ CE_ROLE_AG[CE_AG[n]] = (CE_ROLE_AG[CE_AG[n]]||0)+1; });
function roleStr(cnt, unit){               /* 按次数降序，如「媒体 5 次、KOL 0 次」 */
  return Object.keys(cnt).sort(function(a,b){ return cnt[b]-cnt[a]; })
           .map(function(r){ return r+' '+cnt[r]+' '+unit; }).join('、');
}
var CE_ROLE_EV_STR = roleStr(CE_ROLE_EV, '次');
var CE_ROLE_AG_STR = roleStr(CE_ROLE_AG, '个');
/* 客户 / 网民是否真的从未成为跨渠道载体 —— 决定要不要写「从未」那半句 */
var CE_NO_CN = !CE_ROLE_EV['客户'] && !CE_ROLE_EV['网民'];
/* 一次跨渠道事件的「原发渠道 → 到达渠道」串；r11 / r20 / r21 三处共用，避免各写一遍 */
function CE_LINE(q){
  return (q && q.ce && q.ce.length)
    ? q.ce.map(function(e){ return e.origin + '→' + e.to.join('/'); }).join('、') : ''; }

/* ★ 2026-10-15 第二十六轮：把 `P_BY_ID` / `QX` / `QX_BY_R` **提前到这里**。
   原先它们定义在 1.2 的派生量块里（原第 1078—1085 行），而**模块总结** `SEC(676, …)`
   在本文件第 764 行**就执行** —— 那时它们还是 undefined，模块总结取不到。
   模块总结必须同时看**转载**（`CE`）与**跨渠道引用**（`QX`）才能说清「这一跑到底有没有
   跨过平台」。值不变（都是 `POSTS` 的纯函数），只是算得早。 */
var P_BY_ID = {}; POSTS.forEach(function(x){ P_BY_ID[x.id] = x; });
var QX = [];                                    /* 逐条跨渠道引用 */
POSTS.forEach(function(x){
  var src = x.q ? P_BY_ID[x.q] : null;
  if(src && src.ch !== x.ch) QX.push(x);
});
var QX_BY_R = {};                               /* 轮 -> 该轮的跨渠道引用 */
QX.forEach(function(x){ (QX_BY_R[x.r] = QX_BY_R[x.r] || []).push(x); });
/* ★ 2026-10-20 第二十九轮：原先按 `x.name` 去重，印成「N 个…发起账号」——
   但账号是 agent_id，同一个人在两个渠道各有一个账号。EVT-002 实测：13 个账号
   分属 11 个名字（凤凰网财经、谭先生各占两个渠道），印「11 个账号」少算 2 个。
   改成账号按 agent_id 去重、另存名字数，两个数都写出。 */
var QX_AG = {};                                 /* 跨渠道引用的发起账号（按 agent_id 去重） */
QX.forEach(function(x){ QX_AG[x.agent || x.name] = x.role; });
var QX_AG_N = Object.keys(QX_AG).length;
var QX_NM = {};                                 /* 发起主体（按姓名去重，一人多号只算一个） */
QX.forEach(function(x){ QX_NM[x.name || x.agent] = 1; });
var QX_NM_N = Object.keys(QX_NM).length;
var QX_AG_TXT = QX_AG_N + ' 个跨渠道引用发起账号' +
  (QX_NM_N < QX_AG_N ? '（分属 '+QX_NM_N+' 个发声主体，同一主体在多个渠道各有一个账号）' : '');

/* ★ 2026-10-15 第二十六轮：**「零转载」事件** —— 本跑一条帖都没跨过渠道。
   实测 EVT-011：47 条帖的 `chs` 长度**全部为 1**；记录员每轮自记的
   `new_cross_channel_edges` 42 轮合计 **0**（同期 EVT-010 为 21，EVT-003 只有 10 条帖
   也有 2）⇒ `cross_events` 为空是**引擎算出来的真结果**，不是数据丢了。
   EVT-001…010 每场都有转载跨渠道，所以「首次发生在轮 `CE[0].round`」这类写法从没露过；
   空列表上 `CE[0]` 是 undefined ⇒ 建议框 / 口径说明 / 摘要三段整块渲染失败（SEC_ERR 3），
   另有多处断言当场为假。
   ★ 关键：`CE` 为空 **不等于**「内容没跨过平台」—— 另起一帖、落到引用者自己渠道的
     **跨渠道引用**（`QX`）同样是跨平台，只是不产生渠道边。EVT-011 的 `QX` 是 **26 条**。
     报告里「渠道边」与「跨平台流动」从第十三轮起就是两个量，本次按三个层次分明写：
     有转载 / 只有引用 / 两者皆无。 */
var NO_CE = (CE.length === 0);              /* 没有由「转载」产生的跨渠道事件（渠道边 0 条） */
var NO_XC = NO_CE && (QX.length === 0);     /* 连跨渠道引用也没有 ⇒ 内容真的只在原发平台内 */

/* 媒体帖的渠道分布 */
var MED_BY_CH = {}; POSTS.forEach(function(x){ if(x.role==='媒体') MED_BY_CH[x.ch] = (MED_BY_CH[x.ch]||0)+1; });
var MED_TOP_CH = Object.keys(MED_BY_CH).sort(function(a,b){ return MED_BY_CH[b]-MED_BY_CH[a]; })[0];

/* ---------------- 图表 ---------------- */
var charts = [];
/* mkEl：拿到**元素**再挂图。原先逐日小图网格的容器 id 是拼出来的（'chGd'+i），
   走的就是 mkEl(el('chGd'+i), opt) 这条路径；拓扑删除后页面上的容器 id 全部是字面量，
   mk() 已够用，这一层保留只为恢复拓扑时不必再铺一次线。 */
function mkEl(dom, opt){
  if(!dom || typeof echarts === 'undefined') return null;
  var c = echarts.init(dom); c.setOption(opt); charts.push(c); return c;
}
function mk(sel, opt){ return mkEl(document.querySelector(sel), opt); }
window.addEventListener('resize', function(){ charts.forEach(function(c){ c.resize(); }); });

/* ================= 模块一 ================= */

/* --- 模块总结（原 1.1 位置） ---
   ★ 2026-10-12 第二轮改版：按仿真侧提供的报告撰写 prompt，模块一以「模块总结」开篇 ——
     300—500 字、结论先行，依次交代 ①整体模式与路径特征 ②关键驱动因素 ③最该干预的转折点
     ④对品牌的影响程度判断，再由 1.1 / 1.2 两部分用数据支撑。
   ★ 文风：加粗只用于核心结论（本节加粗字数约占总字数 3%），不使用颜色标签与套话。 */
SEC(676, function(){
  var roots = POSTS.filter(function(x){ return !x.q; });
  /* 模块总结用到的现算量 */
  var NEG_MIN  = Math.min.apply(null, S.map(function(x){ return x.neg; }));
  var redN     = AL.rows.filter(function(a){ return a.level === '红色'; }).length;
  var winRatio = WIN_INTER / TOTAL_INTER / (WIN_HOURS / __HOURS__);
  /* 发帖最多的自然日（按帖子实际发布日计数，与 BD[date].n 同源） */
  var dPost  = DAILY.reduce(function(a,x){ return BD[x.date].n > BD[a.date].n ? x : a; }, DAILY[0]);
  /* ★ 2026-10-14 第二十二轮：本段原先三处写死角色名 ——
     ① 开场语「客户群体高频发声铺底、媒体类账号定调并完成跨平台接力」；
     ②「发声量最大的是客户群体」；③ 结尾「客户提供的是持续的负面底色，媒体提供的是量级跃升与渠道扩展」；
        以及 ④ 里的「收敛于少数媒体账号」。
     EVT-003 上客户 0 帖（全池 8 条里一条没有）、EVT-005 上客户只有 2 条（全部角色里最少）而媒体 17 条最多、
     EVT-003 的 2 起跨渠道全部由网民发起，四处同时为假、且不会报错。
     改为：发声量最大的角色 / 每帖被引最高的角色 / 末轮位次 / 跨渠道发起角色各自现取。 */
  var RO_FIN = ROLES.map(function(r){ return {r:r, v:RM(r)[N-1]}; })
                    .filter(function(x){ return x.v !== null && x.v !== undefined; })
                    .sort(function(a,b){ return a.v - b.v; });
  var TOP_POSTER = ROLES.map(function(r){ return {r:r, n:ROof(r).posts}; })
                        .sort(function(a,b){ return b.n - a.n; })[0];
  var TOP_CITER = ROLES.map(function(r){ return {r:r, v:ROof(r).out_per_post, n:ROof(r).posts}; })
                       .filter(function(x){ return x.n > 0; })
                       .sort(function(a,b){ return b.v - a.v; })[0] || null;

  setHTML('r11',
    /* ① 传播模式与路径特征 */
    /* ★ 2026-10-15 第二十六轮：原先无条件写「内容经跨渠道转载扩散」—— 零转载事件上为假
       （EVT-011 一次转载跨渠道都没有，跨平台靠 26 条引用完成）。分三支写。 */
    p('本次事件的传播形态可概括为「'+TOP_POSTER.r+'群体是主要发声方、'+
      (NO_XC ? '内容在原发平台内流转' : (NO_CE ? '内容经跨渠道引用扩散' : '内容经跨渠道转载扩散'))+'」。'+
      't0（'+META.t0+'）由 '+roots.length+' 条原帖同时注入，此后 '+META.rounds+
      ' 轮、__HOURS__ 小时内共生成 '+META.posts+' 条帖子、'+META.edges+' 条引用边，最长引用链 '+
      /* ★ 2026-10-20 第三十一轮：原句「N 个智能体参与发声」把**名册人数**说成了发声人数 ——
         EVT-001 名册 262 人、实际只生成 30 条帖，读者会以为 262 人都在发帖。改为「名册」口径。 */
      META.max_depth+' 层；名册共 '+META.agents+' 个智能体。'+
      (TOP_POSTER.n
        ? '发声量最大的是'+TOP_POSTER.r+'群体（'+TOP_POSTER.n+' 条，占全池 '+
          pct(TOP_POSTER.n/META.posts)+'），其帖子每帖被引用 '+
          ROof(TOP_POSTER.r).out_per_post.toFixed(2)+' 次，'+ROof(TOP_POSTER.r).zero+' 个'+
          TOP_POSTER.r+'类账号全程未被引用；'+
          (TOP_CITER && TOP_CITER.r !== TOP_POSTER.r
            ? '每帖被引最高的是'+TOP_CITER.r+'群体（'+TOP_CITER.v.toFixed(2)+
              ' 次/帖），与发声量最大的角色并不是同一个。'
            : '发声量最大的角色同时也是每帖被引最高的角色，既是内容源头也是放大器。')
        : '本跑没有产生任何帖子，全部智能体均处于观察状态。')+
      /* ★ 2026-10-15 第二十六轮：原先只分「有转载 / 没有转载」两支，零转载的 EVT-011 落到
         第二支后印出「内容全部在原发平台内流转」—— 本轮实测跨渠道引用有 26 条，当场为假。 */
      (NO_XC
        ? '全程没有发生跨渠道传播，内容全部在原发平台内流转。'
        : (NO_CE
            ? '全程没有发生跨渠道<b>转载</b>（新增渠道边 0 条），但有 '+QX.length+
              ' 条跨渠道引用把内容带到了引用者自己的平台 —— 跨平台流动并未停止，只是换了形态。'
            : '跨渠道传播共 '+CE.length+' 次，全部由'+CE_ROLE_STR+'发起，涉及 '+CE_AG_N+
              ' 个账号，首次发生在轮 '+CE[0].round+'（'+CE[0].window[0]+'）。'))) +

    /* ② 关键驱动因素：热度与情绪的错位 */
    p('热度与情绪并不同步。发帖量峰值出现在 '+dPost.date.slice(5)+'（'+BD[dPost.date].n+
      ' 条），互动量峰值'+
      (PEAK_DAY.date === dPost.date
        ? '与发帖峰值同日（'+n0(PEAK_DAY.flow)+' 次）——讨论热度与新增内容同步见顶。'
        : (PEAK_DAY.date > dPost.date ? '滞后至 ' : '提前至 ')+PEAK_DAY.date.slice(5)+'（'+
          n0(PEAK_DAY.flow)+' 次）——'+
          (PEAK_DAY.date > dPost.date ? '内容产出见顶之后，讨论仍在延续。'
                                      : '讨论热度先于内容产出见顶。'))+
      '负面占比自 t0 即处于 '+pct(S[0].neg)+
      '，全程未低于 '+pct(NEG_MIN)+'，峰值 '+pct(PEAK_NEG.neg)+' 出现在轮 '+
      /* ★ 2026-10-12 第九轮顺带修：原为 window[1]（轮末），与同段 ① 的 window[0]（轮首）
         及全报告其余窗口标注不一致 —— 同一事实在相邻两段里印出两个时间。统一取 window[0]。
         区间的写法不受影响：`S[a].window[0] 至 S[b].window[1]` 仍是首轮起、末轮止。 */
      PEAK_NEG.round+'（'+PEAK_NEG.window[0]+'）；情绪最低点在轮 '+LOW_V.round+'（'+
      LOW_V.window[0]+'，'+f3(LOW_V.valence)+'）。'+
      /* ★ 2026-10-14 第二十二轮：「讨论量的回落」原本无条件写，若互动峰值就落在最后一日则为假。 */
      (DAILY[DAILY.length-1].flow < PEAK_DAY.flow
        ? '讨论量的回落没有带来情绪的修复。'
        : '讨论量至推演结束时仍处高位，情绪亦未修复。')) +

    /* ③ 最该干预的转折点
       ★ 2026-10-12 第九轮：关键时段由「t0 + 56 小时的钟点锚点」改为「连续 4 轮动作总量 argmax」
         （见脚本上方的 WIN 块），原句的三处论断随之失效 —— 新窗口落在 05-27，不再是发帖最集中
         的 05-25，也不再包含预警首次升红的轮次，且其密度是全程平均的 1.32 倍（不再「尚未冲顶」）。
         改为按新口径现算的叙述：这个窗口是热度与情绪同时处在高位的一段。 */
    p('传播路径上最该干预的是<b>轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+'</b>（'+S[WIN.lo].window[0]+
      ' 至 '+S[WIN.hi].window[1]+'）。这是全程动作最密集的一段：'+WIN_HOURS+' 小时内发生 '+
      n0(WIN_ACT)+' 次动作，占全程 '+n0(TOTAL_ACT)+' 次的 '+pct(WIN_ACT/TOTAL_ACT)+
      '，而时长只占推演区间的 '+pct(WIN_HOURS/__HOURS__)+'；'+
      /* ★ 2026-10-14 第二十二轮：「该时段的负面占比也是全程最高」原为无条件断言 ——
         窗口由**动作总量** argmax 选出，与情绪峰值是否同段并无因果关系。
         EVT-002/003/004 上负面峰值（轮 21 / 36 / 18）都不在这个窗口里，句子当场为假。 */
      (WIN_NEG_MAX >= PEAK_NEG.neg - 1e-9
        ? '该时段的负面占比也是全程最高（'+pct(WIN_NEG_MIN)+'—'+pct(WIN_NEG_MAX)+
          '，峰值出现在轮 '+PEAK_NEG.round+'），热度与情绪在此重合。'
        : '该时段的负面占比为 '+pct(WIN_NEG_MIN)+'—'+pct(WIN_NEG_MAX)+'，低于全程峰值 '+
          pct(PEAK_NEG.neg)+'（轮 '+PEAK_NEG.round+'）—— 热度峰值与情绪峰值并不在同一段。')) +

    /* ④ 影响程度判断 */
    /* ★ 2026-10-20 第二十九轮：「判断为高」原先写死 —— 蓝/黄级的跑次也印「高」。
       按本跑最高等级分档：红/橙 → 高，黄 → 中，蓝 → 低（红仍为「高」，不改既有口径）。 */
    p('对企业的影响程度判断为<b>'+(TOP_LV === '红色' || TOP_LV === '橙色' ? '高'
                                 : (TOP_LV === '黄色' ? '中' : '低'))+'</b>。'+
      META.rounds+' 轮中 '+redN+' 轮处于红色预警'+
      (redN ? '' : '（本跑未触及红色，最高等级为'+TOP_LV+'，共 '+TOP_N+' 轮）')+
      '，负面占比末轮仍达 '+pct(S[N-1].neg)+'；'+
      /* ★ 2026-10-14 第二十二轮：原句写死「客户群体末轮效价 X、媒体 Y，均处于深度负面」——
         EVT-005 上排第二的媒体末轮是 +0.029（正区间），「均处于深度负面」为假；且该跑最负的
         其实是网民，不是客户。改为按末轮实际位次取最负的两个在场角色。 */
      (RO_FIN.length
        ? RO_FIN[0].r+'群体末轮效价 '+f3(RO_FIN[0].v)+
          (RO_FIN.length > 1 ? '、'+RO_FIN[1].r+' '+f3(RO_FIN[1].v) : '')+
          /* ★ 2026-10-20 第二十九轮：末轮取了两个角色却统称「处在最低位」——
             EVT-002 的媒体 -0.024 是**次低**，不是最低。改按个数分档。 */
          (RO_FIN.length > 1 ? '，分列全部在场角色的最低与次低位；'
                             : '，处在全部在场角色的最低位；')
        : '')+
      /* ★ 2026-10-15 第二十六轮：同上一处，第四段结尾也把「没有转载」写成了「没有跨渠道」。 */
      (NO_XC
        ? '全程没有跨渠道传播，影响局限在原发平台之内，管控面明确。'
        : (NO_CE
            ? '全程没有跨渠道转载，跨平台流动全部由 '+QX.length+
              ' 条引用完成；引用落在引用者自己的平台上，管控面要按<b>账号</b>找，'+
              '按渠道边布点会整场落空。'
            : '跨渠道传播共 '+CE.length+' 起、只由 '+CE_AG_N+' 个账号发起（'+CE_ROLE_AG_STR+
              '），传播面窄、管控面明确。'))));
});

/* ★ 2026-10-12 模块一改版（第二轮，按仿真侧提供的报告撰写 prompt）：**每日传播拓扑**
   （原图 1-1A 日期切换单日拓扑 + 图 1-1B 逐日并列网格）与配套的 `topoOption()/dayGraph()/
   onNodeClick()/#nodeDetail` 整块删除 —— 该 prompt 把模块一定义为「模块总结 + 1.1 每日发帖量与
   互动趋势 + 1.2 每日渠道分布 + 1.3 传播关键事件时间轴」三部分，拓扑不在其列。
   ★ 2026-10-12 第四轮：1.3 时间轴随后也被仿真侧整块删除，模块一现在只剩
   「模块总结 + 1.1 + 1.2」；「通过谁、怎么传的」回到 1.2 的表 1-3（跨渠道逐次明细）。
   删掉的代码留在 `_版本留痕_模块一prompt版_20261012\\` 里，需要恢复可整段贴回。
   ★ 逐日派生量 `DAY_EDGES` / `BD[].edges / .dmax / .agents / .newch` 目前**已无消费方**
   （拓扑时代的产物），保留是因为各占一行、且恢复拓扑时要一并恢复；1.3 时间轴的「渠道标识」
   字段读的是 `BD[].ch`，（1.3 时间轴删除后连它也无消费方了，只剩 1.2 的渠道统计在用）。 */

/* --- 1.2 每日 --- */
SEC(742, function(){
  var dPost = DAILY.reduce(function(a,x){ return BD[x.date].n > BD[a.date].n ? x : a; }, DAILY[0]);
  /* ★ 2026-10-12 口径修正：本块原先一律用 x.inter（记录员的存量），现改 x.flow（流量）。
     随之改写的两处论断：①「相当于发帖峰值日的 N 倍」—— 流量口径下这个倍数只有 1.2，
     不再是 1.3；②长尾句原文是「回落至 X—X 的区间」，tail 只剩一天时读起来是废话，
     改为单日直接陈述。 */
  var ratio = PEAK_DAY.flow / dPost.flow;
  /* ★ R-50：原先写死「hi3 = 05-24…05-26」「tail = 05-27 起」「晚约 48 小时」。
     换轮次表后峰值日会挪（本跑互动峰值在 05-28），故改为按数据自身的形状取：
     互动最高的三天 / 互动峰值日之后的各日 / 两个峰值日的真实时差。 */
  var lagH = (Date.parse(PEAK_DAY.date + 'T00:00:00Z') - Date.parse(dPost.date + 'T00:00:00Z')) / 3600e3;
  var hi3 = DAILY.slice().sort(function(a,b){ return b.flow - a.flow; }).slice(0, 3)
                 .sort(function(a,b){ return a.date < b.date ? -1 : 1; });
  var hi3s = sum(hi3, function(x){ return x.flow; });
  var tail = DAILY.filter(function(x){ return x.date > PEAK_DAY.date; });
  var tl = tail.map(function(x){ return x.flow; }).sort(function(a,b){ return a-b; });
  /* ★ t0 落在 05-22 22:00 ⇒ 首日只覆盖 2 小时（0 条互动），末日前一天也仅部分覆盖。
     算「波动幅度」时必须剔除**残缺日**：否则 05-22 的 0 次会让倍数变成 Infinity
     （除法炸掉）、3 条帖子会把发帖量的极差虚报成 8.3 倍。
     这里的门槛取「当日覆盖 ≥ 12 小时」，与 4 小时一轮的轮长无关，只用于剔除首尾残缺日。 */
  /* ★ 2026-10-20 第二十九轮：原先门槛是「≥12 小时」，而正文写的是「剔除仅覆盖 15.2 小时的
     首日」—— EVT-002 的首日 15.2 小时恰好过闸，被算进了区间，句子与算式当场对不上
     （正文声称剔除首日，区间却由首日的 11 条定上限）。改为只收**整日**，并把被剔除的日子
     按数据写进句子。 */
  var FULLDAYS = DAILY.filter(function(x){ return x.hours >= 23.9; });
  if(!FULLDAYS.length) FULLDAYS = DAILY.slice();
  var PARTIAL = DAILY.filter(function(x){ return x.hours < 23.9; });
  var partTxt = (PARTIAL.length === 0 ? ''
    : (PARTIAL.length === 1
        ? '剔除仅覆盖 '+PARTIAL[0].hours.toFixed(1)+' 小时的一天（'+PARTIAL[0].date.slice(5)+'），'
        : '剔除覆盖不足整日的'+CN_NUM(PARTIAL.length)+'天（'+
          PARTIAL.map(function(x){ return x.date.slice(5)+' 仅覆盖 '+x.hours.toFixed(1)+' 小时'; })
                 .join('、')+'），'));
  var fMin = FULLDAYS.reduce(function(a,x){ return x.flow < a.flow ? x : a; }, FULLDAYS[0]);
  var fMax = FULLDAYS.reduce(function(a,x){ return x.flow > a.flow ? x : a; }, FULLDAYS[0]);
  var pMin = FULLDAYS.reduce(function(a,x){ return BD[x.date].n < BD[a.date].n ? x : a; }, FULLDAYS[0]);
  var pMax = FULLDAYS.reduce(function(a,x){ return BD[x.date].n > BD[a.date].n ? x : a; }, FULLDAYS[0]);

  /* 负面占比的「最高日」用全部日（极值不受残缺影响），只是措辞必须落在「日均」上，
     不能再与 t0 的单轮读数（同一个峰值、不同精度）当成两件事。 */
  var nMax = DAILY.reduce(function(a,x){ return x.neg > a.neg ? x : a; }, DAILY[0]);
  var nAfter = DAILY.filter(function(x){ return x.date > nMax.date; });
  var nAfterMin = nAfter.length
    ? nAfter.reduce(function(a,x){ return x.neg < a.neg ? x : a; }, nAfter[0]) : nMax;
  var riseRow = DAILY.reduce(function(a,x,i){
    var dd = i > 0 ? x.neg - DAILY[i-1].neg : -9;
    return dd > a.d ? {d:dd, date:x.date} : a; }, {d:-9, date:DAILY[0].date});
  /* ★ 2026-10-20 第二十九轮：「相差一个量级」原为无条件断言。整日口径下 EVT-002 的
     两端倍比是 1.5 倍 / 1.13 倍，差不到一个量级 ⇒ 改按两个倍比现判。 */
  var pRatio = BD[pMin.date].n > 0 ? BD[pMax.date].n / BD[pMin.date].n : null;
  var fRatio = fMin.flow > 0 ? fMax.flow / fMin.flow : null;
  var magTxt = (pRatio === null || fRatio === null) ? '难以直接比较'
             : (pRatio >= 2 * fRatio ? '相差一个量级'
             : (pRatio > fRatio * 1.2 ? '是发帖端更大' : '都不大'));

  setHTML('r12',
    p('发帖量与互动量不在同一时间见顶。发帖量峰值出现在 '+dPost.date.slice(5)+'（<b>'+BD[dPost.date].n+
      ' 条</b>），互动量峰值'+(lagH >= 0 ? '滞后 '+n0(lagH/24)+' 天' : '超前 '+n0(-lagH/24)+' 天')+
      '，出现在 '+PEAK_DAY.date.slice(5)+
      '（<b>'+n0(PEAK_DAY.flow)+' 次</b>）。互动峰值当日新增帖只有 '+BD[PEAK_DAY.date].n+
      ' 条，相当于发帖峰值日的 '+pct(BD[PEAK_DAY.date].n/BD[dPost.date].n, 0)+
      '，热度并非由新帖推动，而是存量帖子仍在被持续评论与转发。') +
    /* ★ 2026-10-14 第二十二轮：原先写死「仅覆盖 2 小时的首日」（EVT-001 的 t0 落在 22:00）。
       首日覆盖时长随 t0 变化，改为读该日自己的 hours（与表 1-1、图注里的同一字段）。 */
    p('两者在整日样本上的波动幅度'+magTxt+'。'+partTxt+'发帖量在 '+BD[pMin.date].n+
      '—'+BD[pMax.date].n+' 条之间逐日起落（'+
      /* ★ 2026-10-14 第二十二轮：最低日发帖为 0 时（EVT-003），倍数算出 Infinity。改为按最低日是否为零分两支。 */
      (BD[pMin.date].n > 0
        ? '约 '+(BD[pMax.date].n/BD[pMin.date].n).toFixed(1)+' 倍'
        : '最低的一天为 0 条，倍比无从计算')+
      '）；互动量始终落在 '+n0(fMin.flow)+'—'+n0(fMax.flow)+' 次之间（'+
      /* ★ 2026-10-14 第二十二轮：同上，最低日互动为 0 时倍比为 Infinity。 */
      (fMin.flow > 0 ? '约 '+(fMax.flow/fMin.flow).toFixed(2)+' 倍' : '最低的一天为 0 次')+
      '），接近一条平线。互动量最高的'+CN_NUM(hi3.length)+'天是 '+
      hi3.map(function(x){ return x.date.slice(5)+'（'+n0(x.flow)+' 次）'; }).join('、')+
      '，合计 '+n0(hi3s)+' 次，占全期互动量的 '+pct(hi3s/TOTAL_INTER)+'；'+
      (tail.length
        ? (tail.length === 1
            ? PEAK_DAY.date.slice(5)+' 之后只剩 '+tail[0].date.slice(5)+' 一天，为 '+n0(tl[0])+' 次。'
            : PEAK_DAY.date.slice(5)+' 之后回落至 '+n0(tl[0])+'—'+n0(tl[tl.length-1])+' 次的区间。')
        : '互动峰值落在最后一个自然日，区间内没有留下长尾。')) +
    /* prompt 要求：叠加负面情绪占比的变化趋势，说明热度上升时情绪是否同步恶化。
       ★ 2026-10-20 第二十七轮：原先无条件写「与热度同向恶化，并且在热度见顶之后没有回落」
         「讨论规模的收缩没有带来情绪的修复」「不具备自然平息的条件」—— 三句都是 EVT-001 的形状。
         改为：负面占比的走向、以及自然平息判定，全部按本跑数据分档（判据见模块二 2.1）。 */
    p('负面情绪占比'+
      (S[N-1].neg < nMax.neg - 0.02 ? '在热度见顶之后确有回落' : '与热度同向恶化，在热度见顶之后没有回落')+
      '。占比自 t0 的 '+pct(S[0].neg)+' 起步，'+riseRow.date.slice(5)+' 单日抬升 '+
      ((riseRow.d)*100).toFixed(1)+' 个百分点（全程最大单日升幅），'+nMax.date.slice(5)+
      ' 的日均达到各日最高的 <b>'+pct(nMax.neg)+'</b>；此后最低维持在 '+pct(nAfterMin.neg)+
      ' 以上，末轮为 '+pct(S[N-1].neg)+'。发帖量自 '+dPost.date.slice(5)+' 见顶后回落，'+
      (S[N-1].neg >= nMax.neg - 0.02
        ? '负面占比并未随之下降 —— 讨论规模的收缩没有带来情绪的修复，'
        : '负面占比同步收窄 —— 讨论规模的收缩与情绪的修复方向一致，')+
      '按本报告的自然平息判据（模块二 2.1），本次推演'+CALM.title+'。'));
});

/* ★ 2026-10-12 第九轮改版（仿真侧指令）：「图1.1你还是把它变成双柱状图，发帖量、互动量都是
   一个柱状的。然后你再把发帖量和互动量加总在一起。给我一张折线图，因为我觉得发帖和互动
   加在一起是整体的一个动作的总量。」
   ⇒ **两根柱 + 一条合计折线**，仍是**单轴**：
     柱①＝发帖量（帖数，整数）
     柱②＝互动量（评+转+赞，含 .5 的加权分摊值）
     折线＝动作总量 = 发帖量 + 互动量，即该日「发帖 / 评论 / 转发 / 点赞」四类动作之和
   量级同阶（本跑柱 3—25、折线 21—103），单轴可直读；
   折线与两柱同轴还有一层好处：柱顶到折线的落差就是另一根柱本身，一眼看出「动作里有多少不是发帖」。
   第四轮曾把「负面情绪占比」折线与右轴整条去掉，本轮保持去掉：负面占比的分析
   留在 1.1 正文第三段与表 1-1 的「负面占比」列，悬停提示里也仍给出该日负面占比与效价均值。 */
var DAY_FLOW = {};                                      /* 日期 → 互动量（评+转+赞，未取整） */
DAILY.forEach(function(d){ DAY_FLOW[d.date] = d.flow; });
function dayAct(k){ return BD[k].n + DAY_FLOW[k]; }      /* 动作总量 = 发帖 + 互动 */
/* ★ 2026-10-21：首日与末日往往是**残缺日**（本跑 05-22 只有 20h、05-29 只有 4h），
   横轴若只写 MM-DD，读者会拿 4 小时的柱子和 24 小时的柱子直接比高低。
   这里按日期查覆盖小时数，非整天的那几天在横轴第二行标出来。**不写死哪一天**。 */
var H_BY_MD = {};
DAILY.forEach(function(d){ H_BY_MD[d.date.slice(5)] = d.hours; });
/* 标签数值：整数不带小数位，含 .5 的分摊值保留一位（与表 1-1 的取整口径错开，同第五轮约定） */
function labNum(v){ return Math.abs(v - Math.round(v)) < 0.05 ? n0(v) : n1(v); }
mk('#chDaily', {
  grid:{left:68, right:44, top:62, bottom:50},
  tooltip:{trigger:'axis', axisPointer:{type:'cross'}, formatter:function(ps){
    var k = DAYS[ps[0].dataIndex], o = BD[k];
    var dd = DAILY.find(function(x){ return x.date === k; });
    return '<b>'+k+'</b>（'+n1(dd.hours)+' 小时）<br>发帖量：<b>'+o.n+
      '</b><br>互动量：<b>'+n1(dd.flow)+'</b>（= 评论 '+n1(dd.comment)+
      ' + 转发 '+n1(dd.repost)+' + 点赞 '+n1(dd.like)+'）'+
      '<br>动作总量：<b>'+n1(dayAct(k))+'</b>（= 发帖 '+o.n+' + 互动 '+n1(dd.flow)+'）'+
      '<br>负面情绪占比：<b>'+pct(dd.neg)+'</b>　效价均值：'+f3(dd.valence)+
      '<br>在线均值：'+n1(dd.online)+' 人　渠道：'+
      CH_ORDER.filter(function(c){ return o.ch[c]; }).map(function(c){
        return c+' '+o.ch[c]; }).join(' / '); }},
  legend:{top:6, textStyle:{fontSize:13}, itemWidth:16, itemHeight:9},
  xAxis:{type:'category', data:DAYS.map(function(k){ return k.slice(5); }),
    axisLabel:{fontSize:13, interval:0, formatter:function(v){
      var hh = H_BY_MD[v];
      return (hh !== undefined && Math.abs(hh - 24) > 0.01) ? (v + '\n仅 ' + labNum(hh) + ' 小时') : v;
    }}},
  yAxis:{type:'value', name:'次数', nameTextStyle:{fontSize:12.5},
    nameGap:14, splitLine:{lineStyle:{color:'#eef1f6'}}},
  series:[
    {name:'发帖量', type:'bar', barMaxWidth:26, barGap:'10%',
      data:DAYS.map(function(k){ return BD[k].n; }),
      itemStyle:{color:'#9cc3ea', borderRadius:[4,4,0,0]},
      label:{show:true, position:'top', fontSize:12, color:'#3c5b7d'}},
    {name:'互动量（评+转+赞）', type:'bar', barMaxWidth:26,
      data:DAYS.map(function(k){ return DAY_FLOW[k]; }),
      itemStyle:{color:'#5b93cf', borderRadius:[4,4,0,0]},
      label:{show:true, position:'top', fontSize:11.5, color:'#2f6fb5',
        formatter:function(pp){ return pp.value > 0 ? labNum(pp.value) : ''; }}},
    /* 折线画**未取整**的分摊值（68.5 / 74.5 等），点位置与表 1-1 逐位一致；标签取整。
       05-22 是 t0 残缺日（仅 2 小时），动作总量 3，标签会压在柱顶 ⇒ 该日不标数值，
       读数走 tooltip 与表 1-1。 */
    {name:'动作总量（发帖+互动）', type:'line', smooth:true, symbol:'circle', symbolSize:8,
      data:DAYS.map(function(k){ return dayAct(k); }),
      itemStyle:{color:'#e07b39'}, lineStyle:{width:3, color:'#e07b39'},
      label:{show:true, position:'top', fontSize:11.5, color:'#a8551c', distance:9,
        formatter:function(pp){ return pp.value > 3.5 ? labNum(pp.value) : ''; }}}
  ]
});

SEC(858, function(){
  var h = '<thead><tr><th>日期</th><th class="num">覆盖小时</th><th class="num">发帖量</th>'+
    '<th class="num">评论</th><th class="num">转发</th><th class="num">点赞</th>'+
    '<th class="num">互动量</th><th class="num">效价均值</th><th class="num">负面占比</th>'+
    '<th class="num">在线均值</th></tr></thead><tbody>';
  DAILY.forEach(function(d){
    var hot = d.date === PEAK_DAY.date;
    h += '<tr'+(hot?' class="hot"':'')+'><td class="mono"><b>'+d.date+'</b></td>'+
      /* ★ 2026-10-12：四列取一位小数 —— 分摊值含 .5，取整后逐行相加会与合计差 1（496→497）。 */
      '<td class="num">'+n1(d.hours)+'</td><td class="num"><b>'+BD[d.date].n+'</b></td>'+
      '<td class="num">'+n1(d.comment)+'</td><td class="num">'+n1(d.repost)+'</td>'+
      '<td class="num">'+n1(d.like)+'</td><td class="num"><b>'+n1(d.flow)+'</b></td>'+
      '<td class="num">'+f3(d.valence)+'</td><td class="num">'+pct(d.neg)+'</td>'+
      '<td class="num">'+n1(d.online)+'</td></tr>';
  });
  h += '<tr class="hot"><td><b>合计</b></td><td class="num">'+n1(__HOURS__)+'</td>'+
    '<td class="num"><b>'+META.posts+'</b></td>'+
    '<td class="num">'+n1(sum(DAILY,function(x){return x.comment;}))+'</td>'+
    '<td class="num">'+n1(sum(DAILY,function(x){return x.repost;}))+'</td>'+
    '<td class="num">'+n1(sum(DAILY,function(x){return x.like;}))+'</td>'+
    '<td class="num"><b>'+n1(TOTAL_INTER)+'</b></td><td class="num">'+f3(V_MEAN)+
    '</td><td class="num">—</td><td class="num">—</td></tr>';
  setHTML('tbDaily', h+'</tbody>');
  /* 2026-10-12 问「05-28 为什么标红」——规则一直是 `PEAK_DAY`（互动量最高的自然日），
     但此前**只在代码里、没写进图注**，读者无从判断。这里把规则与数值一并现算写出。
     口径说明：这一列在表里是「互动量」列，故按互动量取峰；图 1-1 的折线是动作总量（发帖+互动），
     两者的峰值日可能不在同一天（本跑动作总量峰在 05-26，只比 05-28 高 1.0），图注里如实点出。 */
  var actPeak = DAILY.reduce(function(a,x){
    return (BD[x.date].n + x.flow) > (BD[a.date].n + a.flow) ? x : a; }, DAILY[0]);
  setHTML('hotDayNote',
    '互动量最高的一天（'+PEAK_DAY.date.slice(5)+'，'+n1(PEAK_DAY.flow)+' 次）'+
    (actPeak.date === PEAK_DAY.date ? '' :
      /* ★ 2026-10-20 第三十轮：原句「其峰值日另在 04-15（49.0），比本行高 1.0」——
         读者看到的是「45.0」与「49.0」，差 4.0 却说高 1.0；那个 1.0 是拿两天的
         **动作总量**（发帖＋互动）相减得来的，而屏幕上只印了互动列。改成把两天的
         分项都写出来，口径自证。 */
      '。图 1-1 的折线是动作总量（发帖＋互动），其峰值日另在 '+actPeak.date.slice(5)+
      '（合计 '+n1(BD[actPeak.date].n + actPeak.flow)+' = 发帖 '+n1(BD[actPeak.date].n)+
      ' + 互动 '+n1(actPeak.flow)+'）；本日合计 '+n1(BD[PEAK_DAY.date].n + PEAK_DAY.flow)+
      ' = 发帖 '+n1(BD[PEAK_DAY.date].n)+' + 互动 '+n1(PEAK_DAY.flow)+'，相差 '+
      n1((BD[actPeak.date].n + actPeak.flow) - (BD[PEAK_DAY.date].n + PEAK_DAY.flow))+
      (((BD[actPeak.date].n + actPeak.flow) - (BD[PEAK_DAY.date].n + PEAK_DAY.flow)) <= 2
        ? '，两天基本持平' : '')));
});

/* ---- 渠道覆盖里程碑：累计已触达渠道数的逐轮推进（★ 2026-10-12 第十一轮） ----
   仿真侧要的表形态不是「每次跨渠道各自发生了什么」，而是「**哪一轮渠道从几个扩展到几个**」，
   并把跨渠道的点标出来、点开才看细节。故派生量在这里一次算好，供 1.2 正文、图 1-3、表 1-3 共用。
   ⚠ **不能**直接用 `posts[].chs` —— 那是**终态**集合：某条帖的 `chs` 里已经含微博，
     但把微博带进来的那次跨渠道传播未必发生在它自己的轮次。故按时间口径现推：
       一条渠道被记为「已触达」 = 有帖子落在它上面（取该帖自己的轮次 `posts[].r`）
                              或某次跨渠道传播把它列为到达方（取该事件的轮次 `cross_events[].round`）。
   本跑实测：轮 0 = 2 个（小红书、抖音）→ 轮 1 = 3 个（+微博）→ 轮 8 = 4 个（+新闻门户），
   此后 34 轮渠道数不再变化，但跨渠道传播仍在发生（轮 8 / 18 / 21 / 32 共 5 起）——
   「渠道数不再增长」与「不再跨渠道」是两件事，报告里必须分开写。
   ★ 2026-10-13 第十三轮：上面那 5 起是**转载**驱动的（`cross_events` 定义在原帖 `channels`
     的跨轮增长上）；另有 51 条**跨渠道引用**（另起新帖落到引用者自己的渠道）不产生渠道边。
     表 1-3 末列现按两种机制合计，见下方 QX / QX_BY_R。 */
var REACH_AT = {};                                   /* 渠道 -> 首次被触达的轮 */
POSTS.forEach(function(x){
  if(!(x.ch in REACH_AT) || x.r < REACH_AT[x.ch]) REACH_AT[x.ch] = x.r;
});
CE.forEach(function(e){
  e.to.forEach(function(c){
    if(!(c in REACH_AT) || e.round < REACH_AT[c]) REACH_AT[c] = e.round;
  });
});
var MISS_CH = CHS.filter(function(c){ return !(c in REACH_AT); });
var REACH_SET = [], REACH_N = [];
S.forEach(function(x){
  var set = Object.keys(REACH_AT).filter(function(c){ return REACH_AT[c] <= x.round; });
  set.sort(function(a, b){ return CHS.indexOf(a) - CHS.indexOf(b); });  /* 固定顺序，逐轮不抖动 */
  REACH_SET.push(set); REACH_N.push(set.length);
});
var CE_BY_R = {};                                    /* 轮 -> 该轮的跨渠道事件 */
CE.forEach(function(e){ (CE_BY_R[e.round] = CE_BY_R[e.round] || []).push(e); });
var CE_N = S.map(function(x){ return (CE_BY_R[x.round] || []).length; });
/* ★ 2026-10-13 第十三轮：补上「跨渠道**引用**」。原先末列只数 `cross_events`，而它定义在
   原帖 `channels` 集合的跨轮增长上 ⇒ **按构造只有转载能触发**；引用是另起一条新帖、落在
   引用者自己的渠道（`landing_channel = ch`，`t1_runner.py:1614`），内容同样跨了平台。
   实测：轮 1 那三条微博帖全是引用帖（引轮 0 的小红书帖），只数 cross_events 时轮 1 末列是
   空的，与「轮 1 就出现了微博」自相矛盾 ⇒ 末列改为两种机制合计（行集合不变）。 */
/* ★ 2026-10-15 第二十六轮：`P_BY_ID` / `QX` / `QX_BY_R` 三个定义**已上移到 1.1 之前的
   CE 派生量块**（见上方的「零转载」注释）—— 模块总结先于本块执行，取不到这里算的值。
   值本身没动，这里不再重复定义。 */
/* 里程碑行 = 渠道数发生变化的轮 ∪ 发生跨渠道传播的轮（其余轮与上一行相同，不重复列） */
var MILE = [], GROW = [];
S.forEach(function(x, i){
  var before = i > 0 ? REACH_SET[i - 1] : [];
  var add = REACH_SET[i].filter(function(c){ return before.indexOf(c) < 0; });
  var grew = add.length > 0, evs = CE_BY_R[x.round] || [], qxs = QX_BY_R[x.round] || [];
  if(!grew && !evs.length) return;   /* ★ 行集合只由「渠道数变化」与「转载」决定，引用不单独增行 */
  var m = {i:i, round:x.round, grew:grew, add:add, set:REACH_SET[i], n:REACH_N[i],
           prev:(i > 0 ? REACH_N[i - 1] : 0), evs:evs, qx:qxs};
  MILE.push(m); if(grew) GROW.push(m);
});

/* --- 1.2 渠道分布 --- */
SEC(954, function(){
  var tot = META.posts;
  var rank = CH_ORDER.map(function(c){ return {c:c, n:CO[c] ? CO[c].posts : 0}; })
    .sort(function(a,b){ return b.n - a.n; });
  /* ★ R-50 叙述修正：本块原先写死了 '2026-05-23' / '2026-05-24' 两个日期，以及
     「被到达次数最多的渠道（5 次）」等上一跑的观测值 —— 一律改为现算。 */
  var coverDays = {}, peakDayOf = {};
  CH_ORDER.forEach(function(c){
    var best = null;
    DAYS.forEach(function(k){
      var n = BD[k].ch[c] || 0;
      if(n > 0){ coverDays[c] = (coverDays[c] || 0) + 1; }
      if(n > 0 && (!best || n > best.n)) best = {k:k, n:n};
    });
    peakDayOf[c] = best;
  });
  var lastDayN  = BD[DAYS[DAYS.length - 1]].ch[rank[0].c] || 0;
  var peakDayN  = (peakDayOf[rank[0].c] || {}).n || 0;
  /* ★ 2026-10-20 第三十轮：主阵地那段原写「自 <首个完整日> 起成为主阵地…此后逐日回落」，
     EVT-002 实测两句都假：峰值 3 条出现在 04-14（早于"成为主阵地"的 04-15），
     而 04-16 掉到 0 之后 04-17 又回到 3 条 —— 不是"逐日回落"。改成三件现算事实：
     单日峰值及并列日、首次在单日发帖数上**严格**居首的那天、末日条数。 */
  var peakDays = DAYS.filter(function(k){
    return peakDayN > 0 && (BD[k].ch[rank[0].c] || 0) === peakDayN; });
  var firstTopDay = (function(){
    for(var i = 0; i < DAYS.length; i++){
      var k = DAYS[i], n = BD[k].ch[rank[0].c] || 0;
      if(n <= 0) continue;
      var mx = Math.max.apply(null, CH_ORDER.filter(function(x){ return x !== rank[0].c; })
                                                 .map(function(x){ return BD[k].ch[x] || 0; }));
      if(n > mx) return k;
    }
    return null;
  })();
  /* 长尾主导：互动峰值日之后，是否有哪一天小红书超过微博 */
  var tailLead = null;
  DAYS.forEach(function(k){
    if(k <= PEAK_DAY.date) return;
    if((BD[k].ch['小红书'] || 0) > (BD[k].ch['微博'] || 0)) tailLead = k;
  });
  /* ★ 2026-10-12 模块一改版：原 1.4（跨渠道）整块并入本块 —— 两者回答的是同一个问题
     （「内容传到了哪个平台」），只是呈现 A 看构成、呈现 B 看流向。
     以下派生量原先定义在 1.4 里，一并搬到这里。 */
  var arrive = {};
  CE.forEach(function(e){ e.to.forEach(function(t){ arrive[t] = (arrive[t] || 0) + 1; }); });
  var arrRank = Object.keys(arrive).sort(function(a,b){ return arrive[b] - arrive[a]; });
  var arrTopN = arrive[arrRank[0]] || 0;
  var nwArr = arrive['新闻门户'] || 0;
  /* ★ R-50 叙述修正：起点渠道按帖子的**原平台**（`origin`）计。
     原先按 `from`（该帖已有的渠道集合）计，并在文案里写死「抖音是 N 次事件的起点渠道…占一半以上」
     —— 本跑抖音起点为 0，那句话既与数据相反、也与模块五建议 5 自相矛盾。 */
  var src = {};
  CE.forEach(function(e){ src[e.origin] = (src[e.origin] || 0) + 1; });
  var srcRank = Object.keys(src).sort(function(a,b){ return src[b] - src[a]; });
  var pairs = {};
  CE.forEach(function(e){ e.from.forEach(function(f){ e.to.forEach(function(t){
    pairs[f+' → '+t] = (pairs[f+' → '+t] || 0) + 1; }); }); });
  var pairRank = Object.keys(pairs).sort(function(a,b){ return pairs[b] - pairs[a]; });
  var byRole = {};
  CE.forEach(function(e){ byRole[e.role] = (byRole[e.role] || 0) + 1; });
  var multi = CE.filter(function(e){ return e.to.length > 1; });
  var ceDay = {};
  CE.forEach(function(e){ var d = e.window[0].slice(0,10); (ceDay[d] = ceDay[d] || []).push(e); });
  var ceDays = Object.keys(ceDay).sort();
  var zyPosts = ROof('客户').posts + ROof('网民').posts;
  /* ★ 2026-10-12：本跑跨渠道发起方**全部是媒体**（KOL 0 次），
     故「由机构账号与意见领袖承载」这类写死措辞改为按 byRole 现推。 */
  var rolePhrase = clsJoin(Object.keys(byRole).sort(function(a,b){ return byRole[b] - byRole[a]; }), '账号');
  var arrTied = arrRank.filter(function(c){ return arrive[c] === arrTopN; });

  var indegBy = {}, cntBy = {}, coverBy = {};
  POSTS.forEach(function(x){
    cntBy[x.ch] = (cntBy[x.ch] || 0) + 1;
    indegBy[x.ch] = (indegBy[x.ch] || 0) + x.indeg;
    (coverBy[x.ch] = coverBy[x.ch] || {})[x.t.slice(0, 10)] = 1;
  });
  function perPost(c){ return cntBy[c] ? (indegBy[c] / cntBy[c]).toFixed(2) : '—'; }
  function coverN(c){ return coverBy[c] ? Object.keys(coverBy[c]).length : 0; }
  function pctOf(c){ return cntBy[c] ? cntBy[c] / META.posts : 0; }
  var zyPct = zyPosts / META.posts;
  /* 每帖被引密度排序 —— 用来客观地指出谁是「落地端」 */
  var perRank = CH_ORDER.filter(function(c){ return cntBy[c]; })
    .map(function(c){ return {c:c, n:cntBy[c], ind:indegBy[c], v:indegBy[c]/cntBy[c]}; })
    .sort(function(a,b){ return b.v - a.v; });
  var rootChs = [];
  POSTS.forEach(function(x){ if(!x.q && rootChs.indexOf(x.ch) < 0) rootChs.push(x.ch); });
  var ceGapH = CE.length > 1
    ? (Date.parse(CE[CE.length-1].window[0].replace(' ', 'T') + ':00') -
       Date.parse(CE[0].window[0].replace(' ', 'T') + ':00')) / 36e5 : 0;

  setHTML('r13',
    /* ① 覆盖台阶：渠道数在哪几轮变过（本轮改为以「台阶」开篇） */
    p('事件全程在 '+rank.filter(function(x){ return x.n > 0; }).length+' 个平台产生内容：'+
      rank.filter(function(x){ return x.n > 0; }).map(function(x){
        return x.c+' '+x.n+' 条（'+pct(pctOf(x.c))+'，覆盖 '+coverN(x.c)+' 天）'; }).join('；')+
      '；渠道池共 '+CHS.length+' 个'+(MISS_CH.length ? ('，'+MISS_CH.join('与')+'全程没有任何内容') : '')+'。'+
      '按轮看渠道覆盖，全程只出现 '+(GROW.length - 1)+' 次台阶：t0 起始 '+GROW[0].n+' 个（'+
      GROW[0].set.join('、')+'），'+
      GROW.slice(1).map(function(m){
        return '轮 '+m.round+'（'+S[m.i].window[0]+'）加入'+m.add.join('、')+'、渠道数 '+
          m.prev+' → '+m.n; }).join('，')+
      '，此后 '+(N - 1 - GROW[GROW.length - 1].i)+' 轮渠道数不再变化。'+
      /* ★ 2026-10-14 第二十一轮：原先无条件写「自 <首个完整日> 起成为主阵地，当日即产出 N 条…
         此后逐日回落至末日的 M 条」。换事件后两条都会假：EVT-004 主阵地当天产出 0 条
         （「当日即产出 0 条」），末日 8 条又恰是全程峰值（「逐日回落至 8 条」）。改成分支。 */
      (function(){
        var c = rank[0].c, pd = peakDays.map(function(k){ return k.slice(5); });
        return c+'全程 '+rank[0].n+' 条、覆盖 '+coverN(c)+' 天，是发帖最多的渠道；单日峰值 '+
          peakDayN+' 条'+(pd.length > 1 ? '（'+pd.join('、')+' 并列）' : '（'+pd[0]+'）')+
          (firstTopDay ? '；首次在单日发帖数上严格居首是 '+firstTopDay.slice(5) : '')+
          '；末日 '+lastDayN+' 条。';
      })()) +

    /* ② 渠道数停了，跨渠道没有停 —— 且两种机制必须分开说
       （★ 2026-10-13 第十三轮补上「跨渠道引用」，此前只讲了转载一路） */
    /* ★ 2026-10-15 第二十六轮：零转载时，原先会印出「全程共 0 起…落在轮 （空）」（列表为空）、
       「只按渠道边计数，跨渠道就只有 0 起；把引用算上…量级完全不同」—— 句子骨架还在，
       但「落在轮 」后面是空的。改为三支现算。 */
    p((NO_XC
        ? '渠道数停住之后，内容始终没有跨出原发平台。两种机制的区分仍然要交代清楚：'
        : '渠道数停住之后，跨渠道传播并没有停，只是换了形态。这里要把两种机制分清：')+
      (NO_CE
        ? '<b>转载</b>把新渠道挂在原帖上，是唯一会产生「渠道边」记录的机制 —— '+
          '本跑全程<b>一次都没有发生</b>（新增渠道边 0 条）；'
        : '<b>转载</b>把新渠道挂在原帖上，因此只有它能产生「渠道边」记录，全程共 '+CE.length+
          ' 起（按新落地的渠道计 '+META.cross_n+' 条渠道边），落在轮 '+
          MILE.filter(function(m){ return m.evs.length; }).map(function(m){ return m.round; }).join('、')+
          '；')+
      '<b>跨渠道引用</b>另起一帖、落在引用者自己的渠道，一条渠道边都不产生，'+
      '却同样把内容带到了行动者自己的平台，全程共 '+QX.length+' 条，散在 '+
      Object.keys(QX_BY_R).length+' 个轮次里。'+
      (NO_XC
        ? '本跑两种机制都是 0，内容只在原发平台内流动。'
        : (NO_CE
            ? '本跑的跨平台流动<b>全部由引用这一路完成</b> —— 只按渠道边计数，跨渠道是 0 起；'+
              '把引用算上，跨平台流动的内容有 '+QX.length+' 条，两种数法差的正是这 '+QX.length+' 条。'
            : '两者差别是形态不是能力 —— 只按渠道边计数，跨渠道就只有 '+
              CE.length+' 起；把引用一并算上，'+
              '跨平台流动的内容是 '+QX.length+' 条，量级完全不同。'))+
      /* ★ 2026-10-14 第二十一轮：原先这一句把「轮 1 / 微博 / 小红书 / 两次台阶中的第一次」
         全部写死 —— 换事件后既可能印出「0 条微博帖引用轮 0 的小红书帖」，
         也和同段自己算出的台阶数直接打架。改为按 QX / GROW 现算。 */
      (function(){
        var ks = Object.keys(QX_BY_R).map(Number).sort(function(a2, b2){ return a2 - b2; });
        if(!ks.length) return '全程没有发生任何跨渠道引用，内容只在原发平台内流动。';
        var r0 = ks[0], lst = QX_BY_R[r0];
        var land = {}, sour = {}, srcR = {};
        lst.forEach(function(x){
          land[x.ch] = (land[x.ch] || 0) + 1;
          var s0 = P_BY_ID[x.q];
          var c0 = s0 ? s0.ch : '原平台';
          sour[c0] = (sour[c0] || 0) + 1;
          if(s0) srcR[s0.r] = (srcR[s0.r] || 0) + 1;
        });
        var lk = Object.keys(land).sort(function(a2, b2){ return land[b2] - land[a2]; });
        var sk = Object.keys(sour).sort(function(a2, b2){ return sour[b2] - sour[a2]; });
        var sname = Object.keys(srcR).sort(function(a2, b2){ return srcR[b2] - srcR[a2]; })[0];
        var simple = (lk.length === 1 && sk.length === 1);
        /* 该轮是否为渠道台阶轮；是的话它是本文件的第几次台阶（台阶计次从 1 起，t0 那次不计） */
        var gi = 0;
        GROW.forEach(function(m, i){ if(m.round === r0 && i > 0) gi = i; });
        return '最早的一次就发生在轮 ' + r0 + '：' +
          (simple
            ? lst.length + ' 条' + lk[0] + '帖引用轮 ' + sname + ' 的' + sk[0] + '帖，' +
              lk[0] + '就是这么做进来的，'
            : '共 ' + lst.length + ' 条跨渠道引用，' +
              lk.map(function(c){ return '落在' + c + ' ' + land[c] + ' 条'; }).join('、') +
              '（被引的原帖来自' + sk.map(function(c){ return c + ' ' + sour[c] + ' 条'; }).join('、') + '），') +
          (gi > 0
            ? '这也是全程' + CN_NUM(GROW.length - 1) + '次渠道台阶中的第' + CN_NUM(gi) + '次。'
            : '该轮并不是渠道扩容的台阶轮 —— 引用把内容带到的是已经存在的平台，渠道数不变。');
      })() +
      /* ★ 2026-10-15 第二十六轮：这一整段讲的全是「转载」。零转载时原先会印出
         「转载这一路落在 0 个自然日（），首起与末起相隔 0 天」「发起方按原发渠道是 ；到达方是 ，」
         —— 括号与列表都是空的。整段在 NO_CE 下不写。 */
      (NO_CE ? '' :
      '转载这一路落在 '+ceDays.length+' 个自然日（'+ceDays.map(function(d){ return d.slice(5); }).join('、')+
      '），首起与末起相隔'+
      /* ★ 2026-10-20 第二十八轮：toFixed(0) 会把 5.5 天进位成「相隔 6 天」，而前一句刚列出
         5 个自然日（EVT-008 实测 07-15→07-20），两者当场打架。改为向下取整，不足 1 天照实说。
         空格只给数字分支，否则会印成「相隔 不足 1 天」（EVT-006 实测）。 */
      (ceGapH < 24 ? '不足 1 天' : ' '+Math.floor(ceGapH/24)+' 天')+'。'+
      /* ★ 2026-10-20 第三十轮：原句写「轮 N 之后的每一起来自转载的跨渠道传播都是…」——
         EVT-001 在渠道池定型（轮 30）之后再没有转载，「每一起来自转载的…」是在空集上下断言。 */
      (function(){
        var after = CE.filter(function(e){ return e.round > GROW[GROW.length-1].round; });
        return after.length
          ? '也就是说，轮 '+GROW[GROW.length-1].round+' 之后还有 '+after.length+
            ' 起转载，全部落在既有平台之间，不再带来新的平台 —— 渠道数不再增长，不等于不再跨渠道。'
          : '也就是说，轮 '+GROW[GROW.length-1].round+' 之后（渠道池定型之后）再没有发生转载，'+
            '此后的跨渠道流动全部由引用完成 —— 渠道数不再增长，不等于不再跨渠道。';
      })()+
      '发起方按原发渠道是'+
      srcRank.map(function(c){ return c+' '+(src[c] || 0)+' 起'; }).join('、')+
      '；到达方是'+
      arrRank.map(function(c){ return c+' '+arrive[c]+' 起'; }).join('、')+'，')+
      (CE.length
        ? (multi.length
            ? '其中 '+multi.length+' 起同时打开多个平台。'
            : '每一次转载只打开一个新平台，渠道扩展是逐步叠加的。')
        : '')+
      /* ★ 2026-10-14 第二十四轮：原先无条件把 `rank[0]`（**发帖最多**的渠道）称作
         「最主要的跨渠道跳板」，紧接着又印出它自己的转出数 —— 两个量根本不是一回事：
         主阵地看**发帖量**（`rank`），跳板看**转出量**（`src`）。EVT-006 实测：微博 73 条帖
         居首、转出 0 起 ⇒ 印出「微博既是内容主阵地（73 条帖），也是最主要的跨渠道跳板，
         0 起从它转出，占全部转载的 0.0%」——后半句自己把自己否掉了。同一跑里真正的转出
         发起方是抖音（1 起），却在句中被彻底忽略。
         另：`pct(x/CE.length)` 在 CE 为空时是 0/0 ⇒ 会印出「NaN%」（`pct` 不挡 NaN）。
         改成分两支现算：发帖最多的是不是转出最多的，分别措辞；CE 为空另有兜底。 */
      (CE.length
        ? (srcRank[0] === rank[0].c
            ? rank[0].c+'既是内容主阵地（'+cntBy[rank[0].c]+' 条帖），也是最主要的跨渠道跳板，'+
              (src[rank[0].c] || 0)+' 起从它转出，占全部转载的 '+pct((src[rank[0].c] || 0)/CE.length)+'。'
            : rank[0].c+'是内容主阵地（'+cntBy[rank[0].c]+' 条帖），但它不是跨渠道转出的发起方'+
              '（转出 '+(src[rank[0].c] || 0)+' 起）；全程 '+CE.length+' 起转载里转出最多的是'+
              srcRank[0]+'（'+src[srcRank[0]]+' 起，占全部转载的 '+pct(src[srcRank[0]]/CE.length)+'）。')
        : rank[0].c+'是内容主阵地（'+cntBy[rank[0].c]+' 条帖）；全程没有发生跨渠道转载，谈不上跳板。')) +

    /* ③ 内容落地端与管控面 */
    p(/* ★ 2026-10-14 第二十二轮：原先无条件写「最高的**两个**渠道是」；有内容产出的渠道
         不足两个时（本跑实测会出现）会印出「两个渠道是 X」却只列一个。改为按实际条数取词。
         另加一层「本跑没有产生任何帖子」的兜底，避免 `perRank` 为空时印出空列表。 */
      (perRank.length
        ? '按每帖被引次数排序，'+
          (perRank.length > 1
            ? '最高的'+CN_NUM(Math.min(2, perRank.length))+'个渠道是'
            : '获得引用的渠道只有 1 个：')+
          perRank.slice(0, 2).map(function(x){
            return x.c+'（'+x.n+' 条帖、被引 '+x.ind+' 次、每帖 '+x.v.toFixed(2)+' 次）';
          }).join('与')+'。'
        : '全程没有产生任何帖子，渠道层面的被引排序无从计算。')+
      /* ★ 2026-10-14 第二十二轮：原先无条件写「它们承担的是落地端角色而不是源头。相比之下主阵地 X
         的 M 条帖只被引用 K 次」——前提是主阵地**不在**被引最高的两个渠道里。EVT-005 的微博既是
         主阵地又是每帖被引最高的渠道，那句话会拿同一组数字（20 条／28 次／1.40 次）做对比，
         印出来自相矛盾。改为按主阵地是否位列其中分两支。 */
      (perRank.length
        /* ★ 2026-10-15 第二十六轮：判据原先写成「主阵地位列每帖被引**前二**」
           (`perRank.slice(0,2).some(...)`)，但那一支的**文案**说的是「**也是每帖被引最高的渠道**」
           （＝第一）。二者不等价 —— 主阵地排第二时文案为假。
           实测：EVT-004 / 007 / 008 / 011 / 012 五场的主阵地正是每帖被引第二
           （如 EVT-011：微博 1.643 居首，主阵地新闻门户 0.652 排第二），
           报告会印出「新闻门户…也是每帖被引最高的渠道」，与上一句刚列出的排序当场打架。
           ⇒ 拆成三支：A＝主阵地就是第一（原支，逐字不动）；B2＝主阵地位列第二（新支）；
              B1＝主阵地不在前二（原支，逐字不动）。 */
        ? (perRank[0].c === rank[0].c
            ? rank[0].c+'既是发帖量最大的主阵地（'+cntBy[rank[0].c]+' 条帖、被引 '+indegBy[rank[0].c]+
              ' 次、每帖 '+perPost(rank[0].c)+' 次），也是每帖被引最高的渠道 —— 内容从主阵地流出，'+
              '在本平台内被反复引用放大，源头与放大器这一次没有分开。'
            : (perRank.slice(0, 2).some(function(x){ return x.c === rank[0].c; })
                ? rank[0].c+'是发帖量最大的主阵地（'+cntBy[rank[0].c]+' 条帖、被引 '+indegBy[rank[0].c]+
                  ' 次、每帖 '+perPost(rank[0].c)+' 次），但每帖被引的最高位不是它，而是'+perRank[0].c+
                  '（'+perRank[0].n+' 条帖、每帖 '+perPost(perRank[0].c)+' 次）—— 源头的体量与放大器的'+
                  '穿透力这一次落在两个不同的渠道上。'
                /* ★ 2026-10-20 第三十一轮：这一支的渠道名前后各带一个半角空格，
                   EVT-001 渲染成「相比之下主阵地 微博 的 19 条帖」——中文渠道名被空格切开。
                   同一判据的 A / B2 两支都没有空格，这里去掉，与它们一致。 */
                : '它们承担的是落地端角色而不是源头。相比之下主阵地'+rank[0].c+'的 '+
                  cntBy[rank[0].c]+' 条帖只被引用 '+indegBy[rank[0].c]+' 次，每帖 '+perPost(rank[0].c)+
                  ' 次 —— 内容从主阵地流出，在少数平台上被反复引用放大。'))
        : '')+
      /* ★ 2026-10-14 第二十二轮：原先无条件写「却**没有一条**成为跨渠道传播的载体」，
         并在 `CE.length === 0` 时印出「0 起转载全部由账号发起」（rolePhrase 空串拼出来的）。
         模块五建议 2 早已按 `CE_NO_CN` 分了支，这里补齐同一判据，并加零起转载的兜底。 */
      (CE.length
        ? '客户与网民账号共 '+zyPosts+' 条帖（占全池 '+pct(zyPct)+'），'+
          (CE_NO_CN ? '却没有一条成为跨渠道传播的载体' : '其中一部分同时也是跨渠道传播的载体')+
          '：'+CE.length+' 起转载全部由'+rolePhrase+'发起，只涉及 '+CE_AG_N+
          ' 个账号，跨平台管控面明确。'
        : '客户与网民账号共 '+zyPosts+' 条帖（占全池 '+pct(zyPct)+'）；全程没有发生转载，'+
          /* ★ 2026-10-15 第二十六轮：原先在此收尾为「内容全部在原发平台内流动」，
             零转载但**有引用**时（EVT-011 实测 26 条）当场为假 —— 引用同样把内容带到了别的平台。 */
          (NO_XC ? '内容全部在原发平台内流动。'
                 : '跨平台流动全部由 '+QX.length+' 条跨渠道引用完成（引用落在引用者自己的渠道，不产生渠道边）。'))));
});

/* ★ 2026-10-12 第四轮改版（仿真侧指令）：1.2 的两种呈现（堆叠面积 / 桑基）都撤，
   回到**每日堆叠柱** —— 一根柱＝一个自然日的发帖量，柱内色段＝该日各渠道产出，
   柱高看总量、色段看「这天是谁在说」。口径未变：按帖子的**原发渠道**（posts[].ch）
   与**实际发布时间**归属自然日。 */
mk('#chChanDay', {
  grid:{left:64, right:40, top:46, bottom:50},
  tooltip:{trigger:'axis', axisPointer:{type:'shadow'},
    formatter:function(ps){
      var k = DAYS[ps[0].dataIndex];
      return '<b>'+k+'</b>　合计 '+BD[k].n+' 条<br>'+
        ps.map(function(p){ return p.marker+p.seriesName+'：<b>'+p.value+'</b> 条'; }).join('<br>'); }},
  legend:{top:6, textStyle:{fontSize:13}, itemWidth:14, itemHeight:9},
  xAxis:{type:'category', data:DAYS.map(function(k){ return k.slice(5); }), axisLabel:{fontSize:13}},
  yAxis:{type:'value', name:'发帖量', nameTextStyle:{fontSize:12.5}, nameGap:14,
    splitLine:{lineStyle:{color:'#eef1f6'}}},
  series: CH_ORDER.map(function(c){
    return {name:c, type:'bar', stack:'ch', barMaxWidth:52,
      itemStyle:{color:CH_COLOR[c]},
      emphasis:{focus:'series'},
      label:{show:true, fontSize:11.5, color:'#fff',
        formatter:function(pr){ return pr.value ? pr.value : ''; }},
      data:DAYS.map(function(k){ return BD[k].ch[c] || 0; })};
  })
});

SEC(1114, function(){
  var h = '<thead><tr><th>渠道</th>'+
    DAYS.map(function(k){ return '<th class="num">'+k.slice(5)+'</th>'; }).join('')+
    '<th class="num">合计</th><th class="num">占比</th></tr></thead><tbody>';
  CH_ORDER.forEach(function(c){
    h += '<tr><td>'+chChip(c)+'</td>'+
      DAYS.map(function(k){ return '<td class="num">'+(BD[k].ch[c]||0)+'</td>'; }).join('')+
      '<td class="num"><b>'+CO[c].posts+'</b></td><td class="num">'+pct(CO[c].posts/META.posts)+'</td></tr>';
  });
  h += '<tr class="hot"><td><b>合计</b></td>'+
    DAYS.map(function(k){ return '<td class="num"><b>'+BD[k].n+'</b></td>'; }).join('')+
    '<td class="num"><b>'+META.posts+'</b></td><td class="num">100.0%</td></tr>';
  setHTML('tbChanDay', h+'</tbody>');
});

/* ★ 2026-10-12 第十一轮：原先在此构造的「跨渠道传播逐次明细」表 1-3 已整块替换为
   「渠道覆盖里程碑」表 1-3，连同新图 1-3 一并挪到 `dayAxis` 定义**之后** ——
   图 1-3 要用仿真日副轴，而 `DAY_LBL` 在 992 行才赋值，本处（952 行附近）早于它，
   若留在原位，副轴取到的是 undefined。派生量仍在 1.2 之前一次算好。
   ★ 2026-10-13 第十三轮：图 1-3 整块删除（仿真侧「不需要图了，就保留这个表」），
     故本处现在只剩「表 1-3 的构造挪到了后面」这一句仍然成立。 */

/* ★ 2026-10-12 第四轮改版（仿真侧指令）：1.3「传播关键事件时间轴」整块删除 ——
   卡片、时间轴 IIFE（含 10 类节点与【跨渠道】标记）与 #r14 引言段一并撤掉。
   全文留在 `_版本留痕_模块一_第四轮_20261012\_del_1.3_时间轴_删除内容.txt`，可整段贴回。
   随之无消费方的变量与占位符也一并删除：`dPost2`（只被时间轴引用）、
   `ARC_LINK` / `__ARCLINK__`（桑基通道数）、`__CEN__` / `__CEAGN__`（只出现在桑基图注里）。 */

/* ================= 模块二 ================= */
/* ★ 2026-10-12 模块二改版（仿真侧给定的报告撰写要求，只改本模块的**呈现与正文**）：
   ① 新增「模块总结」#r20，结论先行
   ② 2.1 保留折线图，横轴下加一层**仿真日**副标注，并只标 3 个方向性拐点
      （★ 2026-10-21 第三十三轮撤销了拐点标注，图上只留「全场最低」蓝点，见下文该处注释）
   ③ 2.2 的**两张热力图换成折线图**（角色 5 条 / 渠道 6 条），并补角色×渠道交叉洞察
   载荷里的数字一个都没改；只**新增**两项取数：`rc_mat`（角色×渠道矩阵）与 `gbar`（逐轮钟点
   增益），都由 viz_extract.py 从既有快照现算（见该文件 §8b）。 */

/* ---- 仿真日：横轴的第二层标注 ----
   t0 = 2026-05-22 22:00，每 24 小时为一日、每 6 轮为一日，共 Day1—Day7
   （Day1 只覆盖 22:00—24:00 两个小时，Day7 止于轮 41 的 05-29 22:00）。
   用**仿真日**而不是自然日：t0 落在 05-22 的倒数第二小时，自然日被切成 8 个，
   同一段 7 天的推演要用 8 个标签，读者极易错位。 */
var T0SEC = wsec(S[0].window[0]);
var SDAY = S.map(function(x){ return Math.floor((wsec(x.window[0]) - T0SEC) / 86400e3); });
var SDAY_N = SDAY[SDAY.length - 1] + 1;
function dayOf(i){ return 'Day' + (SDAY[i] + 1); }
/* 标签落在**每个仿真日的中间那一轮**，而不是日界上 —— 日界落点会把 Day2 的标签
   压在 Day1 的尾巴上，读起来像"Day2 从倒数第二轮才开始"。 */
var DAY_LBL = (function(){
  var out = S.map(function(){ return ''; }), g = SDAY[0], i0 = 0;
  for(var i = 1; i <= N; i++){
    if(i === N || SDAY[i] !== g){
      out[Math.floor((i0 + i - 1) / 2)] = 'Day' + (g + 1);
      if(i < N){ g = SDAY[i]; i0 = i; }
    }
  }
  return out;
})();
/* ★ 2026-10-12 第十一轮：加 `gap` 第二参。折线图（图 2-1 #chEmo）的类目轴 boundaryGap 为 false，
   而**柱状图**（原图 1-3 #chMile 已于第十三轮删除；现为图 1-1 #chDaily、图 1-2 #chChanDay、
   图 3-1 #chWin）的类目轴 boundaryGap 默认为 true ——
   副轴必须与主轴一致，否则仿真日标签会相对柱子整体错开半个格。
   省略第二参时行为与本轮之前逐字不变（#chEmo 的 dayAxis(26) 调用点不加第二参）。 */
function dayAxis(off, gap){
  return {type:'category', data:DAY_LBL, boundaryGap:!!gap, position:'bottom', offset:off,
    axisLine:{show:false}, axisTick:{show:false}, splitLine:{show:false},
    axisLabel:{fontSize:11.5, color:'#8a97a8', interval:0}};
}

/* ★ 2026-10-12 第十一轮：表 1-3 —— 由「跨渠道传播逐次明细」改为「渠道覆盖里程碑」。
   仿真侧要的不是「每次跨渠道各自发生了什么」，而是「**哪一轮渠道从几个扩展到几个**」，
   跨渠道的点标出来、点开才看细节。
   ★ 2026-10-13 第十三轮：仿真侧「不需要图了，就保留这个表」⇒ 图 1-3 整块删除，
     只余下这里的表 1-3；同时末列由「只数转载」改为「转载 + 跨渠道引用」合计
     （见 showCross 与表体），使轮 1（微博首现）的末列不再为空。
     · 表 1-3 —— 只列「渠道数发生变化」或「当轮发生跨渠道传播」的轮次，末列「N 起 ▸」可点；
     · 点末列在下方 #crossDetail 展开该轮细节；无传播的轮不改动（与拐点交互一致）。
   ⚠ 本块原在 1.2 的 IIFE 之后（旧表 1-3 的位置），第十一轮**挪到 `dayAxis` 定义之后**；
     表本身不再用仿真日副轴，但位置不动（`showCross` 已在此处，挪回去只会多一处漂移）。
     派生量（REACH_AT / REACH_SET / REACH_N / CE_BY_R / CE_N / MILE / GROW / QX / QX_BY_R）
     仍在 1.2 之前算好。 */
function showCross(i){
  var x = S[i], evs = (x && CE_BY_R[x.round]) || [], qxs = (x && QX_BY_R[x.round]) || [];
  if(!evs.length && !qxs.length) return;        /* 无传播的轮：不改动当前内容 */
  setHTML('crossDetail',
    '<b>轮 '+x.round+'　'+x.window[0]+'</b>'+
    '<span class="pivdv">本轮跨渠道传播 <b>'+(evs.length + qxs.length)+
      '</b> 起（转载 '+evs.length+' · 跨渠道引用 '+qxs.length+'）　累计已触达渠道 '+
      REACH_N[i]+' / '+CHS.length+'（'+REACH_SET[i].join('、')+'）</span>'+
    evs.map(function(e){
      return '<div class="pivrow"><span class="pivk">转载 → '+e.to.join('+')+'</span>'+
        '<b>'+(e.name || '—')+'</b>（'+e.role+'）· 帖 '+e.post+
        '　'+e.from.join('+')+' → <b>'+e.to.join('+')+'</b>'+
        '　该帖渠道数 '+e.prev_n+' → '+e.now_n+'</div>';
    }).join('')+
    qxs.map(function(q){
      return '<div class="pivrow"><span class="pivk">引用 → '+q.ch+'</span>'+
        '<b>'+(q.name || '—')+'</b>（'+q.role+'）· 新帖 '+q.id+
        '　'+P_BY_ID[q.q].ch+' → <b>'+q.ch+'</b>　引的是 '+q.q+'</div>';
    }).join(''));
}
window.showCross = showCross;                   /* 供表 1-3 末列的 onclick 调用 */

/* ★ 2026-10-13 第十三轮（仿真侧指令「不需要图了，就保留这个表」）：图 1-3（#chMile
   渠道覆盖里程碑）整块删除 —— 卡片里的 <div> 与图注、这里的 mk() 实例与 on('click')
   接线一并撤掉。1.2 只保留表 1-3；#chPoolN 与「累计已触达」的口径移入表 1-3 的图注。
   被删的图 1-3 全文留在 `_版本留痕_第十三轮_20261013\`，可整段贴回。
   `CE_N` 就此失去消费方，但保留 —— 它是 `cross_events` 的逐轮计数，与 QX_BY_R 并列
   才有「两种机制」的对照，且造价为零；下一轮若要恢复图 1-3 可直接复用。 */
setHTML('chPoolN', CHS.length);
setHTML('crossDetail', '<span class="pivhint">表 1-3 末列的「N 起 ▸」是该轮跨渠道传播的起数，'+
  '由<b>转载</b>与<b>跨渠道引用</b>两种机制合计而成：转载把新渠道挂在原帖上（只有它会留下「渠道边」记录），'+
  '引用则另起一帖落到引用者自己的渠道 —— 两者都把内容带到行动者自己的平台，差别是形态不是能力。'+
  '点击此处展开明细：转载行给出原帖、发起方、角色与来源→到达渠道，引用行给出新帖与被引原帖。</span>');

SEC(1228, function(){
  var h = '<thead><tr><th class="num">轮次</th><th>时点</th><th>累计已触达渠道</th>'+
    '<th class="num">渠道数</th><th>本轮新增渠道</th><th class="num">跨渠道传播</th></tr></thead><tbody>';
  MILE.forEach(function(m){
    var before = m.i > 0 ? REACH_SET[m.i - 1] : [];
    var add = REACH_SET[m.i].filter(function(c){ return before.indexOf(c) < 0; });
    h += '<tr><td class="num"><b>'+m.round+'</b></td>'+
      '<td class="mono">'+S[m.i].window[0]+'</td>'+
      '<td>'+REACH_SET[m.i].map(chChip).join(' ')+'</td>'+
      '<td class="num">'+(m.i > 0 ? m.prev + ' → ' : '')+'<b>'+m.n+'</b></td>'+
      '<td>'+(add.length ? add.map(function(c){ return '<b>'+c+'</b>'; }).join('、') : '—')+'</td>'+
      /* 无传播的格写「—」而不是「0」，与左列的「本轮新增渠道」保持同一套空值记法。
         ★ 2026-10-13 第十三轮：末列 = 转载 + 跨渠道引用。只数转载会让轮 1（微博首现，
         该轮没有任何 cross_events）留空，与「轮 1 就出现了微博」自相矛盾。 */
      '<td class="num">'+((m.evs.length + m.qx.length)
        ? '<span class="xev" onclick="showCross('+m.i+')"><b>'+(m.evs.length + m.qx.length)+
          '</b> 起 ▸</span>'
        : '—')+'</td></tr>';
  });
  setHTML('tbCross', h+'</tbody>');
});

/* ---- 相邻轮涨跌：拐点判据与"日内节律"甄别共用 ---- */
var DVAL = S.map(function(x, i){ return i ? +(x.valence - S[i-1].valence).toFixed(4) : null; });

/* ---- ★ 本节最关键的一个判断：区分"机制节律"与"事件拐点" ----
   本跑全场效价呈明显的 24 小时锯齿，每夜覆盖 02—05 点的轮次都出现回升。它**不是**事件，
   而是仿真层钟点活动增益的机械结果：`t1_runner.G_of()` 按钟点取增益（活动峰格 ×1.5、
   谷格 ×0.3），覆盖 [02,03,04,05] 四个谷格的轮恒得 Ḡ = 0.300，传染推力最小，
   效价在衰减项作用下回升。用逐轮快照**自记**的钟点增益（`params.gbar`）复算
   corr(Ḡ_r, Δ效价_r) = −0.88 ⇒ 相邻轮的起伏大部分由钟点解释，与事件无关。
   **不在报告层复刻钟点表**：那会多出一处随规则改动而漂移的硬编码。
   ⇒ 本节只在能对应到传播事件的轮次上标拐点，不把每夜的回升标成"拐点"。 */
var GHAT = D.gbar || [];
var GHAT_VALS = (function(){
  var s = {}; GHAT.forEach(function(g){ if(g !== null && g !== undefined) s[g] = 1; });
  return Object.keys(s).map(Number).sort(function(a, b){ return a - b; }); })();
var GHAT_MIN = GHAT_VALS.length ? GHAT_VALS[0] : null;
var GHAT_MAX = GHAT_VALS.length ? GHAT_VALS[GHAT_VALS.length - 1] : null;
var CORR_G = (function(){
  var a = [], b = [];
  for(var i = 2; i < N; i++){
    /* 下标 i 是 `S` 的行号；`GHAT`（= `D.gbar`）是**按轮号索引**的，故要 +R0。 */
    if(GHAT[i + R0] === null || GHAT[i + R0] === undefined || DVAL[i] === null) continue;
    a.push(GHAT[i + R0]); b.push(DVAL[i]);
  }
  if(a.length < 3) return null;
  var n = a.length, ma = sum(a, function(v){ return v; }) / n, mb = sum(b, function(v){ return v; }) / n;
  var c = 0, sa = 0, sb = 0;
  for(var j = 0; j < n; j++){
    c += (a[j] - ma) * (b[j] - mb); sa += (a[j] - ma) * (a[j] - ma); sb += (b[j] - mb) * (b[j] - mb);
  }
  return (sa && sb) ? c / Math.sqrt(sa * sb) : null; })();
var VALLEY_ROUNDS = S.filter(function(x, i){ return GHAT_MIN !== null && GHAT[i + R0] === GHAT_MIN; });
var VALLEY_UP = VALLEY_ROUNDS.filter(function(x){ return DVAL[x.round - R0] > 0; }).length;
/* ★ 2026-10-20 第二十八轮：EVT-002 实测 7 个谷格轮只有 4 轮上涨、corr = -0.03，
   原文却写「每夜出现一次回升……起伏大部分由钟点解释」—— 被同一段的数字当场推翻。
   ⇒ 三种措辞都按数据分支；下面三个串由 r20 与 2.1 两处共用。 */
var VALLEY_ALLUP = VALLEY_ROUNDS.length > 0 && VALLEY_UP === VALLEY_ROUNDS.length;
var VALLEY_NODN  = VALLEY_ROUNDS.length > 0 && VALLEY_UP === 0;
var VALLEY_LEAD  = VALLEY_ALLUP ? '曲线上每夜出现一次回升'
                 : (VALLEY_NODN ? '曲线上与谷格重合的轮次不涨反跌'
                                : (VALLEY_UP * 4 > VALLEY_ROUNDS.length * 3
                                   ? '曲线上与谷格重合的轮次多数上涨、个别走低'
                                   : '曲线上与谷格重合的轮次涨跌互见'));
var VALLEY_CNT   = VALLEY_ALLUP ? '，全部为上涨'
                 : (VALLEY_NODN ? '，全部为下跌'
                                : '，其中 '+VALLEY_UP+' 轮上涨、'+
                                  (VALLEY_ROUNDS.length - VALLEY_UP)+' 轮下跌');
var VALLEY_MECH  = VALLEY_ALLUP
  ? '效价在衰减项作用下回升 —— 这不是舆情回暖。'
  : (VALLEY_NODN
     ? '本跑并未因此出现回升（谷格轮全部在跌），说明主导这一段的是事件而非钟点。'
     : '效价因此更容易回升，但不保证每轮都涨 —— 本跑有 '+
       (VALLEY_ROUNDS.length - VALLEY_UP)+
       ' 轮在谷格上继续走低，读图时不可把谷格轮一律当成回暖信号。');
var GHAT_TXT = function(v){ return (v === null || v === undefined) ? '—' : String(v); };

/* ---- 拐点：公式定义，个数由数据决定（★ 2026-10-20 第二十七轮） ----
   【旧法】固定取三个候选 —— 全程最深单轮跌幅 / 有跨渠道且下跌最深的一轮 / 全场效价最低点
   —— 去重后必然落在 1—3 个之间。三者都是**极值点**，不是**方向反转点**：一条一路向下的
   曲线同样能凑出「三个拐点」。仿真侧指出「3 个点固定是有问题的」。
   【新法】在日级均值曲线上找**趋势状态切换点**，四个式子：
     v6(k) = mean(valence[k … k+5])   6 轮 = 24 小时 = 1 个仿真日，滑窗正好抵消昼伏夜出的
                                      日节律（不抵消的话，每夜 02:00—05:00 的机械回升会被
                                      逐轮误判成拐点 —— 这一点 2.1 末段本来就在讲）
     s(k)  = v6(k+1) − v6(k)          日级步长
     状态  = 回升 (s ≥ τ) / 走平 (|s| < τ) / 下跌 (s ≤ −τ)
     τ     = max(0.05 × (max v6 − min v6), 0.0005)   本跑自身尺度上「走一步」的噪声地板
     拐点  = 状态切换处，轮次取相邻两窗的交界轮 k+3；切换前后每一段状态须持续 ≥ 3 个步长，
             过短的段并入前一段 —— 防单点抖动被读成拐点
   ⇒ 个数由本跑数据决定：可以是 0 个，也可以多于 3 个。τ 与段表都现算，换事件自动跟着变。
   ★「全场效价最低点」**不再占用拐点编号**，单列为蓝点与专门的一句（仿真侧：
     「肯定要指出哪个点是最低的」）；与某个拐点同轮时，图注照旧写明。 */
var PIV_CALC = (function(){
  var W = 6, v6 = [], s = [], st = [], k, j;
  for(k = 0; k + W <= N; k++){
    var sm = 0;
    for(j = 0; j < W; j++) sm += S[k + j].valence;
    v6.push(sm / W);
  }
  for(k = 0; k + 1 < v6.length; k++) s.push(v6[k + 1] - v6[k]);
  var hi = Math.max.apply(null, v6), lo = Math.min.apply(null, v6);
  var tau = Math.max(0.05 * (hi - lo), 0.0005);
  s.forEach(function(x){ st.push(x >= tau ? 1 : (x <= -tau ? -1 : 0)); });
  var runs = [];
  st.forEach(function(x, idx){
    if(runs.length && runs[runs.length - 1][0] === x) runs[runs.length - 1][2] = idx;
    else runs.push([x, idx, idx]);
  });
  /* 过短段并入前一段（首段则并入后一段），直到每段都 ≥ MINRUN 步；随后合并相邻同态段 */
  var MINRUN = 3, guard = 0;
  while(runs.length > 1 && guard++ < 500){
    var bad = -1;
    for(j = 0; j < runs.length; j++){
      if(runs[j][2] - runs[j][1] + 1 < MINRUN){ bad = j; break; }
    }
    if(bad < 0) break;
    var tgt = bad > 0 ? bad - 1 : 1;
    if(tgt >= runs.length) break;
    runs[tgt][1] = Math.min(runs[tgt][1], runs[bad][1]);
    runs[tgt][2] = Math.max(runs[tgt][2], runs[bad][2]);
    runs.splice(bad, 1);
    var mg = [];
    runs.forEach(function(r){
      if(mg.length && mg[mg.length - 1][0] === r[0]) mg[mg.length - 1][2] = r[2];
      else mg.push(r);
    });
    runs = mg;
  }
  return {w:W, v6:v6, s:s, tau:tau, runs:runs};
})();
var PIV_W     = PIV_CALC.w;
var PIV_TAU   = PIV_CALC.tau;
var PIV_TAGS  = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫';
var PIV_ST_CN = {'1':'回升', '0':'走平', '-1':'下跌'};
var PIV_TURN  = {'-1|0':'止跌转平', '-1|1':'转为回升', '0|-1':'转为下跌',
                 '0|1':'转为回升', '1|0':'涨势止步', '1|-1':'转为下跌'};
var PIV_FIRST_OF = {};
POSTS.forEach(function(x){ if(PIV_FIRST_OF[x.role] === undefined || x.r < PIV_FIRST_OF[x.role]) PIV_FIRST_OF[x.role] = x.r; });
var PIV_CE_BY = {};
CE.forEach(function(e){ (PIV_CE_BY[e.round] = PIV_CE_BY[e.round] || []).push(e); });
/* 任一轮次的「事件 / 影响」两段文字：拐点、最深跌幅轮、最低点三处共用同一口径。
   ★ 2026-10-12 第九轮新增 ev（事件）/ imp（影响）供点击弹窗使用，window 供写出「轮 X（时点）」。 */
function pivRow(i){
  if(i === null || i === undefined || i < 0 || i >= N) return null;
  /* `i` 是行号；`PIV_FIRST_OF` / `PIV_CE_BY` 的键都是**轮号**（见顶部 R0）。 */
  var fresh = Object.keys(PIV_FIRST_OF).filter(function(r){ return PIV_FIRST_OF[r] === i + R0; });
  var ces = PIV_CE_BY[i + R0] || [];
  var ev = [];
  if(fresh.length) ev.push(fresh.join('与') + '首次入场 —— 此前全程没有该角色的帖子');
  if(ces.length) ev.push('内容跨出原平台：' + ces.map(function(e){
    return e.from.join('、') + ' 的内容到达 ' + e.to.join('、'); }).join('；'));
  if(!ev.length) ev.push('无新角色入场、无跨渠道事件 —— 波动由既有讨论的持续累积造成');
  var imp = ['单轮效价变化 ' + (DVAL[i] >= 0 ? '+' : '') + f3(DVAL[i]) +
             '，回合效价 ' + f3(S[i].valence) + '。'];
  if(i + R0 === LOW_V.round) imp.push('该轮为全程效价最低点，此后 ' +
    (N - 1 - i) + ' 轮都未能回到这一水平，是本次事件情绪面的谷底。');
  if(fresh.length) imp.push('新角色的入场把议题从既有受众扩展到新的表达群体，' +
    '讨论主体的构成随之改变。');
  if(ces.length) imp.push('内容离开原平台后，触达的受众基数扩大，' +
    '此后的讨论不再局限于首发渠道。');
  /* `i` 是行号、`round` 是轮号（两者差不一定是 0，见顶部 R0 注释）。 */
  return {i:i, round:i + R0, v:S[i].valence, dv:DVAL[i], fresh:fresh, ce:ces, window:S[i].window,
          ev:ev.join('；') + '。', imp:imp.join('')};
}
var PIVOTS = (function(){
  var out = [];
  for(var j = 1; j < PIV_CALC.runs.length; j++){
    var a = PIV_CALC.runs[j - 1][0], b = PIV_CALC.runs[j][0];
    var q = pivRow(PIV_CALC.runs[j][1] + 3);
    if(!q) continue;
    q.tag  = PIV_TAGS.charAt(out.length);
    q.turn = PIV_TURN[a + '|' + b] || '趋势切换';
    q.from = PIV_ST_CN[a]; q.to = PIV_ST_CN[b];
    out.push(q);
  }
  return out;
})();
/* 全程最深单轮跌幅 / 有跨渠道且下跌最深的一轮：**独立于拐点集**现算。
   ★ 2026-10-14 第二十二轮就要求这两个量不得取自拐点集（拐点集不是极值集）；
     2026-10-20 起拐点由上面的公式产出，与极值彻底无关 ⇒ 必须单独扫一遍，
     否则 `PIVOTS.filter(...)` 可能为空、`PIVOTS[0]` 可能不存在。 */
var PIV_DEEP_R = null, PIV_CE_R = null;
(function(){
  for(var i = 2; i < N; i++){
    if(DVAL[i] === null) continue;
    if(PIV_DEEP_R === null || DVAL[i] < DVAL[PIV_DEEP_R]) PIV_DEEP_R = i;
    if(PIV_CE_BY[i + R0] && DVAL[i] < 0 && (PIV_CE_R === null || DVAL[i] < DVAL[PIV_CE_R])) PIV_CE_R = i;
  }
})();
var PIV_DROP = PIV_DEEP_R === null ? pivRow(0) : pivRow(PIV_DEEP_R);
var PIV_CE   = PIV_CE_R === null ? null : pivRow(PIV_CE_R);
/* ★ 2026-10-21 第三十三轮：图 2-1 **不再在曲线上标方向性拐点**（仿真侧：「图里的①根本没什么用，
   既然你给不出拐点，不如就不给了」）。拐点仍由本节末的公式算出并参与 2.1 正文的判断，
   但图上只留「全场最低」那一个蓝点 —— 一两个孤零零的红点既读不出趋势，又挡住折线。
   随之删掉：散点系列、点击展开的 `#pivDetail`、图注里的红点说明与「与拐点③同轮」括注。
   ⇒ 图注里不再有需要现算回填的 span，原来的两行回填 IIFE 一并删除。 */

/* ---- 模块二各段落共用的现算量 ---- */
function seriesMin(a){ var m = null, i = -1;
  a.forEach(function(v, j){ if(v !== null && v !== undefined && (m === null || v < m)){ m = v; i = j; } });
  return {v:m, i:i, ok:(i >= 0)}; }
function mean(a){ return sum(a, function(v){ return v; }) / a.length; }
function corrV(a){                       /* 某条线与全场效价曲线的相关 */
  var xs = [], ys = [], i;
  for(i = 0; i < N; i++){
    if(a[i] === null || a[i] === undefined) continue;
    xs.push(a[i]); ys.push(S[i].valence);
  }
  if(xs.length < 3) return null;
  var mx = mean(xs), my = mean(ys), c = 0, vx = 0, vy = 0;
  for(i = 0; i < xs.length; i++){
    c += (xs[i]-mx)*(ys[i]-my); vx += (xs[i]-mx)*(xs[i]-mx); vy += (ys[i]-my)*(ys[i]-my);
  }
  return (vx && vy) ? c / Math.sqrt(vx*vy) : null; }
function rankFinal(names, mat){
  return names.map(function(n){
      /* 取**列尾**而不是写死 [N-1]：渠道矩阵长 NR（新跑 43，末位 = 轮 42），
         角色矩阵长 N（末位 = 末行）；两者「最后一格即末轮」，故一律按列长取。 */
      var col = mat[n] || [];
      return {n:n, v:col.length ? col[col.length - 1] : null}; })
    .filter(function(x){ return x.v !== null && x.v !== undefined; })
    .sort(function(a, b){ return a.v - b.v; }); }
var ROL_RANK = rankFinal(ROLES, D.role_mat);
var CH_RANK  = rankFinal(CHS, CH_MAT_S);
var ROL_MILD = ROL_RANK.length ? ROL_RANK[ROL_RANK.length - 1].n : null;      /* 末轮最温和的角色 */
var ROL_LOW = ROLES.map(function(r){ var m = seriesMin(D.role_mat[r]);
  return {n:r, i:m.i, round:(m.i < 0 ? m.i : m.i + R0), v:m.v}; })
  .filter(function(x){ return x.i >= 0; }).sort(function(a, b){ return a.i - b.i; });
var CH_DEEP = CHS.map(function(c){ var m = seriesMin(CH_MAT_S[c]);
  return {n:c, i:m.i, round:(m.i < 0 ? m.i : m.i + R0), v:m.v}; })
  .filter(function(x){ return x.i >= 0; }).sort(function(a, b){ return a.v - b.v; });
var CORR_ROL = {}, CORR_CH = {};
ROLES.forEach(function(r){ CORR_ROL[r] = corrV(D.role_mat[r]); });
CHS.forEach(function(c){ CORR_CH[c] = corrV(CH_MAT_S[c]); });
function topBy(names, obj){
  return names.map(function(n){ return {n:n, c:obj[n]}; })
    .filter(function(x){ return x.c !== null; })
    .sort(function(a, b){ return b.c - a.c; })[0] || null; }
var ROL_TOP = topBy(ROLES, CORR_ROL);     /* 与全场曲线最同步的角色 */
var CH_TOP  = topBy(CHS, CORR_CH);        /* 与全场曲线最同步的渠道 */
/* 角色 × 渠道 的 15 个有效组合 */
var ROLCHAN = D.rc_mat || {}, RCCNT = D.rc_cnt || {};
var RC_CELLS = [];
ROLES.forEach(function(r){
  if(!ROLCHAN[r]) return;
  CHS.forEach(function(c){
    var row = ROLCHAN[r][c]; if(!row) return;
    var m = seriesMin(row);
    /* rc_mat 按**轮号**索引（长 NR）⇒ 末轮是列尾，不是 [N-1]（新跑后者是轮 41）。 */
    var vEnd = row.length ? row[row.length - 1] : null;
    if(m.ok) RC_CELLS.push({r:r, c:c, v:vEnd, mn:m.v, mi:m.i, n:((RCCNT[r]||{})[c] || 0)});
  });
});
var RC_DEEP = RC_CELLS.slice().sort(function(a, b){ return a.mn - b.mn; });
function rcCell(r, c){
  var f = RC_CELLS.filter(function(x){ return x.r === r && x.c === c; })[0];
  return f || null; }
var NEG_MIN_V = Math.min.apply(null, S.map(function(x){ return x.neg; }));
var NEG_MIN_R = S.filter(function(x){ return x.neg === NEG_MIN_V; })[0];
var NEG_D2 = (function(){                       /* 第 2 个仿真日起的负面占比下限 */
  var v = 1;
  S.forEach(function(x, i){ if(SDAY[i] >= 1 && x.neg < v) v = x.neg; });
  return v; })();
var DROP_N = DVAL.filter(function(v, i){ return i >= 1 && v !== null && v < 0; }).length;
var D1_ROWS = S.filter(function(x, i){ return SDAY[i] === 0; });          /* 第 1 个仿真日 */
var D1_END = D1_ROWS[D1_ROWS.length - 1];
/* ★ 2026-10-20 第二十九轮：删掉 D1_SHARE —— 它把「首日跌幅 / 全程跌幅」当百分比，
   而最低点可能就落在 t0（分母为 0），EVT-002 印出 8700000000%。首日的陈述改由
   r21 里的 D1_TXT 按方向分三支现写。 */
var DOWN_STREAK = S.filter(function(x){ return x.round > LOW_V.round; }).length;
var CE_AFTER = CE.filter(function(e){ return e.round > LOW_V.round; }).length;
var CH_REV = (function(){                        /* 渠道排序是否出现"最负者易位" */
  /* ★ 2026-10-21 第三十轮：基线的取法由「写死轮 0」改成「**第一个有数的轮**」。
     老跑轮 0 有数 ⇒ 取到的仍是轮 0，输出不变；新跑的 series 从轮 1 起，
     `ch_mat[c][0]` 全 null ⇒ 原写法会取到空集，整句「渠道排序反转」被静默跳过。 */
  var cmLen = 0;
  CHS.forEach(function(c){ var l = (D.ch_mat[c] || []).length; if(l > cmLen) cmLen = l; });
  var k0 = 0;
  while(k0 < cmLen && !CHS.some(function(c){
          var v = (D.ch_mat[c] || [])[k0];
          return v !== null && v !== undefined; })) k0++;
  var at0 = CHS.map(function(c){ return {n:c, v:(D.ch_mat[c] || [])[k0]}; })
    .filter(function(x){ return x.v !== null && x.v !== undefined; })
    .sort(function(a, b){ return a.v - b.v; });
  if(!at0.length) return null;
  var i1 = CH_RANK.map(function(x){ return x.n; }).indexOf(at0[0].n);
  if(i1 < 0 || i1 === 0) return null;
  /* ★ 2026-10-14 第二十一轮：末轮最负者在**轮 0**的位次。原先正文写死「则由第二位升为最负」——
     那只是 EVT-001 恰好成立；换事件若它轮 0 排第三，句子就错。 */
  var i0 = at0.map(function(x){ return x.n; }).indexOf(CH_RANK[0].n);
  /* 从轮 0 起连续多少轮它仍是最负渠道（"一直是最负"这句话要靠数据说，不靠印象） */
  var span = 0;
  for(var r = k0; r < cmLen; r++){
    var mn = null, mnc = null;
    CHS.forEach(function(c){
      var v = D.ch_mat[c][r];
      if(v !== null && v !== undefined && (mn === null || v < mn)){ mn = v; mnc = c; } });
    if(mnc === at0[0].n) span = r + 1; else break;
  }
  return {n:at0[0].n, i1:i1, i0:i0, span:span};
})();
/* ★ 2026-10-14 第二十一轮：「主要平台」原先只排除了「监管通报」，把同样全程 0 帖的「黑猫投诉」也算进去，
   正文却说「四个主要平台」。改为**排除全部无内容产出的渠道**（判据 = CO[c].posts === 0），
   口径与报告别处的 CH_ZERO 一致。⚠ 这里不能引用 `CH_ZERO_JS`：它在下面才赋值，
   本行在装载期执行，取到的是 undefined。 */
var CH_PLAT = CH_RANK.filter(function(x){ return CO[x.n] && CO[x.n].posts; });
var CH_SPAN = CH_PLAT.length > 1 ? Math.abs(CH_PLAT[0].v - CH_PLAT[CH_PLAT.length - 1].v) : null;
/* ★ 2026-10-14 第二十二轮：删掉 `CELL_WB` / `CELL_WW` / `ROLE_GAP_WB` 三个量 ——
   它们写死了「微博上客户与网民」这一对。r20 的第二段原先拿它去比「渠道跨度 vs 角色跨度」，
   而 EVT-003 的微博上压根没有客户账号。现在该段改在 SEC(1546) 内部按 `RC_CELLS` 现算
   全渠道里角色跨度最大的那一处（`RC_GAP`），这三个量再无消费方。 */
var RECOV = S[N-1].valence - LOW_V.valence;
/* ★ 2026-10-14 第二十二轮：回升量占全程极差的比例 —— 2.1 正文用它区分「末段基本收复」
   与「仍贴在谷底」两种形状（原先一律写「末值与谷底处在同一水平」）。区间退化时为 0。 */
var V_RECOV_SH = V_SPAN > 1e-9 ? Math.max(0, RECOV) / V_SPAN : 0;

/* ---- 2.1 全场情绪走势：折线（全场 / 在线 / 负面占比）+ 仿真日副轴 + 拐点标注 ---- */
var chEmoInst = mk('#chEmo', {
  grid:{left:66, right:74, top:56, bottom:96},
  tooltip:{trigger:'axis', axisPointer:{type:'cross'},
    formatter:function(ps){
    var i = ps[0].dataIndex, x = S[i];
    return '<b>轮 '+x.round+'</b>　'+x.window[0]+'　（'+dayOf(i)+'）'+
      (DVAL[x.round - R0] === null ? '' : '　Δ ' + (DVAL[x.round - R0] >= 0 ? '+' : '') + f3(DVAL[x.round - R0])) +
      '<br>全场效价：<b>'+f3(x.valence)+'</b><br>在线效价：'+f3(x.valence_online)+
      '（在线 '+x.online+' 人）'+'<br>负面占比：'+pct(x.neg)+'　负面占比(在线)：'+pct(x.neg_online)+
      '<br>唤醒度：'+f3(x.arousal)+'　该轮钟点增益：'+GHAT_TXT(GHAT[x.round])+
      '<br>本轮新帖 '+x.post+'　评论 '+x.comment+'　转发 '+x.repost+'　点赞 '+x.like+
      '　跨渠道新增 '+x.newcross+' 条'; }},
  legend:{top:8, data:['全场效价均值','在线效价均值','负面占比'], itemWidth:16, itemHeight:9,
    textStyle:{fontSize:13}},
  xAxis:[{type:'category', data:S.map(function(x){ return '轮'+x.round; }), boundaryGap:false,
    axisLabel:{fontSize:11.5, interval:1}, axisTick:{show:false}}, dayAxis(26)],
  yAxis:[
    {type:'value', name:'效价', min:-0.6, max:0, nameTextStyle:{fontSize:12.5},
     splitLine:{lineStyle:{color:'#eef1f6'}}},
    {type:'value', name:'负面占比', min:0, max:1,
     axisLabel:{formatter:function(v){ return (v*100)+'%'; }},
     nameTextStyle:{fontSize:12.5}, splitLine:{show:false}}
  ],
  series:[
    {name:'全场效价均值', type:'line', smooth:true, symbol:'circle', symbolSize:5,
     data:S.map(function(x){ return x.valence; }), itemStyle:{color:'#d4380d'},
     lineStyle:{width:2.8, color:'#d4380d'},
     areaStyle:{color:{type:'linear',x:0,y:0,x2:0,y2:1,colorStops:[
       {offset:0,color:'rgba(212,56,13,.26)'},{offset:1,color:'rgba(212,56,13,.02)'}]}},
     markArea:{silent:true, itemStyle:{opacity:.5}, data: SEG_UNIFORM ? [] : BANDS.map(function(b){
       return [{name: spanOf(SROW(b.lo))+'h/轮', xAxis:'轮'+b.lo,
                itemStyle:{color:b.area}}, {xAxis:'轮'+b.hi}];
     })},
     /* ★ 2026-10-21 第三十三轮：原先这里还有一个 `方向性拐点` 散点系列（红点 ①②③，
        点击展开 `#pivDetail`）。仿真侧看图后判定「一个孤零零的①根本没什么用」⇒ 整块删掉。
        现在图上只剩 `markLine`（t0 水平）与下面的蓝点（全程最低）。 */
     markLine:{silent:true, symbol:'none', lineStyle:{type:'dashed', color:'#98a6b8'},
       label:{formatter:'t0 水平 {c}', fontSize:11.5}, data:[{yAxis:S[0].valence}]},
     /* ★ 2026-10-12 第十轮（仿真侧：「最低点在图中做一个标记，使阅读者能知道哪个点是最低点」）：
        蓝点、标签写在点**下方**。措辞写「全场」——在线效价均值那条虚线的
        低点更低（轮 25，−0.5847），笼统写「最低点」会被读者当成标错。 */
     markPoint:{silent:true, symbol:'circle', symbolSize:9,
       itemStyle:{color:'#0b5cad', borderColor:'#fff', borderWidth:1.5},
       label:{show:true, position:'bottom', distance:7, fontSize:11.5, fontWeight:600,
         color:'#0b5cad', formatter:'全场最低 '+f3(LOW_V.valence)},
       /* 类目轴上的 `coord` 认的是**下标**（轴标签是 '轮'+round），故要 -R0。 */
       data:[{coord:[LOW_V.round - R0, LOW_V.valence]}]}},
    {name:'在线效价均值', type:'line', smooth:true, symbol:'none',
     data:S.map(function(x){ return x.valence_online; }),
     lineStyle:{width:1.8, color:'#8a97a8', type:'dashed'}, itemStyle:{color:'#8a97a8'}},
    {name:'负面占比', type:'line', yAxisIndex:1, smooth:true, symbol:'none',
     data:S.map(function(x){ return x.neg; }),
     lineStyle:{width:2, color:'#4a90d9'}, itemStyle:{color:'#4a90d9'},
     areaStyle:{color:'rgba(74,144,217,.08)'}}
  ]
});


/* ★ 2026-10-21 第三十三轮：原 `pivHTML` / `showPiv` / 点击绑定 / `#pivDetail` 初始提示整块删除
   （图 2-1 不再标拐点，也就没有可点的圆点）。`var chEmoInst = mk('#chEmo', …)` 保持原样
   —— `mk()` 本来就返回实例，留着这个变量既不影响渲染，也方便以后再加交互。 */

/* ---- 2.2 角色 / 渠道逐轮情绪折线 ----
   为什么从热力图换成折线：折线能直接读出"某群体在第 X—Y 轮持续走低"这种斜率变化，
   转折点落在具体轮次上，可与 2.1 的全场曲线、与传播路径的关键事件逐点对齐；
   热力图只能给出静态深浅，读不出速度。 */
var LINE_STYLE = ['solid', 'dashed', 'dotted', 'solid', 'dashed', 'dotted'];
var LINE_W = [2.8, 2.2, 2.2, 2.4, 1.9, 1.6];
function emoLines(sel, names, mat, colors){
  mk(sel, {
    grid:{left:66, right:34, top:48, bottom:100},
    tooltip:{trigger:'axis', axisPointer:{type:'cross'}, formatter:function(ps){
      var i = ps[0].dataIndex, x = S[i];
      return '<b>轮 '+x.round+'</b>　'+x.window[0]+'　（'+dayOf(i)+'）'+
        '<br>全场：<b>'+f3(x.valence)+'</b>'+
        ps.map(function(q){
          return '<br>'+q.marker+q.seriesName+'：'+
            ((q.value === null || q.value === undefined) ? '—' : '<b>'+f3(q.value)+'</b>');
        }).join(''); }},
    legend:{top:6, data:names, textStyle:{fontSize:13}, itemWidth:17, itemHeight:9},
    xAxis:[{type:'category', data:S.map(function(x){ return '轮'+x.round; }), boundaryGap:false,
      axisLabel:{fontSize:11, interval:1}, axisTick:{show:false}}, dayAxis(24)],
    yAxis:{type:'value', name:'效价', nameTextStyle:{fontSize:12.5},
      splitLine:{lineStyle:{color:'#eef1f6'}},
      axisLabel:{formatter:function(v){ return v.toFixed(2); }}},
    series:names.map(function(nm, i){
      return {name:nm, type:'line', smooth:false, connectNulls:false,
        /* 空轮作 null 传入 ⇒ 折线自然断开。**不补 0**：补 0 会画出一条并不存在的下探。 */
        data:mat[nm].map(function(v){ return (v === null || v === undefined) ? null : v; }),
        symbol:'circle', symbolSize:3.4, showSymbol:true,
        itemStyle:{color:colors[nm] || '#8a97a8'},
        lineStyle:{width:LINE_W[i % LINE_W.length], color:colors[nm] || '#8a97a8',
                   type:LINE_STYLE[i % LINE_STYLE.length]}};
    })
  });
}
emoLines('#chRoleLine', ROLES, D.role_mat, ROLE_COLOR);
emoLines('#chChanLine', CHS, CH_MAT_S, CH_COLOR);

/* ---- 模块总结（结论先行） ---- */
SEC(1546, function(){
  /* ★ 2026-10-14 第二十二轮：本段原先四处写死 ——
     ① 比较对象写死「网民」（EVT-005 上最负角色本身就是网民 ⇒ 印出「网民…且逐轮都低于网民」）；
     ②「逐轮都低于」无核（EVT-002 只有 13/42 轮成立，EVT-005 39/42）；
     ③ 把最负角色一律解释成「已付费用户」、把末轮最温和的角色一律说成「只作观察、不参与讨论」
        （EVT-002/004 最温和的是网民、EVT-005 是媒体，而 EVT-005 的媒体发了 17 条帖）；
     ④「6 个渠道全程为负」（EVT-005 新闻门户末轮 +0.064）与「渠道差距小于角色差距」
        （EVT-005 实测 0.114 > 0.014，方向相反）。
     改为全部现算：比较对象取真正的次低角色并数出逐轮占优的轮数；观察性断言只在**该角色本跑
     0 帖**时才写；渠道正负口径与跨度比较方向按本跑数据判定。 */
  var RLOW  = ROL_RANK.length ? ROL_RANK[0] : null;
  var RNEXT = ROL_RANK.length > 1 ? ROL_RANK[1] : null;
  var RCMP  = (function(){
    if(!RLOW || !RNEXT) return null;
    var a = D.role_mat[RLOW.n], b = D.role_mat[RNEXT.n], tot = 0, win = 0;
    for(var i = 0; i < N; i++){
      if(a[i] === null || a[i] === undefined || b[i] === null || b[i] === undefined) continue;
      tot++; if(a[i] < b[i]) win++; }
    return {tot:tot, win:win}; })();
  var CH_NEG_N = CHS.filter(function(c){ var v = CH_MAT_S[c][N-1];
    return v !== null && v !== undefined && v < 0; }).length;
  /* 同一渠道内角色差距最大的一处（原写死「微博上客户与网民」，EVT-003 的微博上压根没有客户） */
  var RC_GAP = (function(){ var m = null;
    CHS.forEach(function(c){
      var vs = RC_CELLS.filter(function(z){ return z.c === c; });
      if(vs.length < 2) return;
      var hi = vs[0], lo = vs[0];
      vs.forEach(function(z){ if(z.v > hi.v) hi = z; if(z.v < lo.v) lo = z; });
      if(hi === lo) return;
      var g = {c:c, a:hi.r, b:lo.r, v:Math.abs(hi.v - lo.v)};
      if(!m || g.v > m.v) m = g; });
    return m; })();
  setHTML('r20',
    /* ★ 2026-10-20 第二十七轮：开头原先写死「全程未进入修复通道」—— 那是一个未定义的判据，
       换事件后既无法复核也可能为假。改为本模块现算的自然平息判定（`CALM`），
       段末也由「没有出现任何可持续的回稳信号」改为按两个必要条件实际过没过分开写。 */
    p('本模块的判断是：本次推演的舆情情绪'+CALM.title+
      (CALM.level === '未平息' ? '（卡在「'+CALM.why+'」）' : '')+'。全场平均效价自 t0 的 '+
      f3(S[0].valence)+' 起步，'+
      /* ★ 2026-10-14 第二十二轮：`PIV_DROP` 已改为真正的全程最深单轮跌幅（原为 `PIVOTS[0]`，
         即三个拐点里最早的一个，见其定义处的注释）。同时补一支「全程没有下跌」的写法，
         免得某跑各轮变动非负时把「最深的一次下跌」印在一个正数上。 */
      (PIV_DROP.dv < 0
        ? '轮 '+PIV_DROP.round+' 出现全程最深的一次单轮下跌（'+f3(PIV_DROP.dv)+'）'
        : '全程未出现单轮下跌（幅度最小的一轮是轮 '+PIV_DROP.round+'，'+f3(PIV_DROP.dv)+'）')+
      /* ★ 2026-10-14 第二十二轮：原先写死「此后中枢逐日下移，最低点落在轮 Y」——只有在最深跌幅
         早于（或就在）谷底那一轮时才成立。EVT-002 的最深跌幅在轮 21、谷底却在轮 1，
         会印出「轮 21 出现最深的一次下跌，此后中枢逐日下移，最低点落在轮 1」的自相矛盾。 */
      (PIV_DROP.round <= LOW_V.round
        ? '，此后中枢逐日下移，最低点落在轮 '
        : '；全程最低点其实出现在更早的轮 ')+
      LOW_V.round+'（'+LOW_V.window[0]+'）的 <b>'+
      f3(LOW_V.valence)+'</b>；末轮（轮 '+(N-1+R0)+'，'+S[N-1].window[0]+'）收于 '+f3(S[N-1].valence)+
      '，较最低点'+(Math.abs(RECOV) < 1e-9 ? '持平' : (RECOV > 0 ? '回升 ' : '下跌 ')+f3(Math.abs(RECOV)))+
      /* ★ 2026-10-14 第二十二轮：「实质上是低位持平」原为无条件断言，回升幅度大时与前半句打架。
         `RECOV` 的 0.02 门限与 2.1 里 `shapeTxt` 的判据一致。 */
      (Math.abs(RECOV) < 0.02 ? '，实质上是低位持平。'
        : (RECOV > 0 ? '，末值已明显高于谷底，但与全程水平位相比仍属低位修复。'
                     : '，末值进一步低于谷底，全程没有出现修复迹象。'))+
      '负面智能体占比自 t0 即达 '+pct(S[0].neg)+'，全程未曾低于 '+
      pct(NEG_MIN_V)+'（轮 '+NEG_MIN_R.round+'），峰值 '+pct(PEAK_NEG.neg)+' 出现在轮 '+
      PEAK_NEG.round+'。'+SDAY_N+' 个仿真日内，'+
      (CALM.needV
        ? '末 6 轮效价均值已回到 '+f3(CALM.vTail)+'，情绪面具备了平息的条件（V = '+f2(CALM.V)+'）。'
        : '末 6 轮效价均值为 '+f3(CALM.vTail)+'，与谷底 '+f3(LOW_V.valence)+
          ' 相差无几，情绪面没有出现可持续的回稳信号（V = '+f2(CALM.V)+'，门槛 0.35）。')) +
    p('分角色看，'+(RLOW
        ? '末轮最负的是'+RLOW.n+'群体（'+f3(RLOW.v)+'）'+
          (RCMP ? '，在 '+RCMP.tot+' 个可比轮次中有 '+RCMP.win+' 轮低于次低的'+RNEXT.n+
                  '群体（末轮 '+f3(RNEXT.v)+'）' : '')+'；'+
          (ROof(RLOW.n).posts
            ? RLOW.n+'群体本跑共发出 '+ROof(RLOW.n).posts+' 条帖，负面主要由其自身发声累积。'
            : RLOW.n+'群体本跑未发帖，其低位来自被动接收。')
        : '角色情绪序列为空。')+
      (ROL_MILD ? ROL_MILD+'群体末轮最温和（'+f3(D.role_mat[ROL_MILD][N-1])+'）'+
        (ROof(ROL_MILD).posts
          ? '，但它本跑也发出了 '+ROof(ROL_MILD).posts+' 条帖，只能读作表达全程平稳，不能读作「立场中性」。'
          : '，与其在事件中只作观察、不参与讨论的行为设定一致。')
                : '其余群体全程只作观察、不参与讨论，情绪曲线不构成独立走势。')+
      '分渠道看，'+CHS.length+' 个渠道'+
      (CH_NEG_N === CHS.length ? '末轮全部处于负区间'
                               : '末轮有 '+CH_NEG_N+' 个处于负区间、'+(CHS.length - CH_NEG_N)+' 个已回到正区间')+
      '，差异体现在水平位：'+CH_RANK[0].n+'（末轮 '+f3(CH_RANK[0].v)+
      '）与'+CH_RANK[1].n+'（'+f3(CH_RANK[1].v)+'）最深，'+CH_RANK[CH_RANK.length-1].n+'（'+
      f3(CH_RANK[CH_RANK.length-1].v)+'）几乎不受波及。'+
      (RC_GAP && CH_SPAN !== null
        ? (CH_SPAN < RC_GAP.v
            ? '渠道之间的差距（'+f3(CH_SPAN)+'）小于同一平台上角色之间的差距（'+RC_GAP.c+'上'+
              RC_GAP.a+'与'+RC_GAP.b+'相差 '+f3(RC_GAP.v)+'），渠道差异主要由「谁在该平台上发声」'+
              '的构成决定，而不是平台自身的情绪属性。'
            : '渠道之间的差距（'+f3(CH_SPAN)+'）不小于同一平台上角色之间的差距（'+RC_GAP.c+'上'+
              RC_GAP.a+'与'+RC_GAP.b+'相差 '+f3(RC_GAP.v)+'），说明除了人员构成，'+
              '平台自身的表达氛围也参与其中。')
        : (CH_SPAN === null ? '' : '渠道之间的差距为 '+f3(CH_SPAN)+'。'))) +
    /* ★ 2026-10-14 第二十二轮：本段原先（a）把 `PIV_CE` 那一轮说成「内容首次跨出原平台」——
        `PIV_CE` 是**有跨渠道且下跌**的拐点，不是最早的跨渠道事件：EVT-005 上是最早的在轮 13、
        `PIV_CE` 落在轮 17，EVT-004 上更早的在轮 2 而 `PIV_CE` 在轮 17。（b）两句都用「轮 X 的…」
        起头，`PIV_DROP.round` 与 `PIV_CE.round` 相同时（EVT-005 的 PIV_DROP 修正后两者仍可能同轮）
        会印出「轮 17 的急跌，轮 17 的下跌…」的重复。
        改为：最早的一起跨渠道传播按 `CE[0]` 现取；与拐点同轮时并入一句。 */
    p('情绪的结构性拐点与传播路径'+
      (PIV_CE && PIV_CE.round === PIV_DROP.round ? '吻合' : '的关系需要分开看')+'。'+
      (PIV_DROP.dv < 0
        ? '轮 '+PIV_DROP.round+'（'+SROW(PIV_DROP.round).window[0]+'）出现全程最深的一次单轮下跌 '+
          f3(PIV_DROP.dv)+
          /* ★ 2026-10-20 第二十八轮：「与 KOL、客户 群体」——角色名被中西文空格夹住（EVT-001 实测）。
             角色名以中文/ASCII 结尾两种都要照顾：只有末位是 ASCII（KOL）时才补空格。 */
          (PIV_DROP.fresh.length ? '，与 '+PIV_DROP.fresh.join('、')+
            (/[A-Za-z0-9]$/.test(PIV_DROP.fresh[PIV_DROP.fresh.length-1]) ? ' ' : '')+'群体首次发声同轮' : '')+
          (PIV_CE && PIV_CE.round === PIV_DROP.round
            ? '，同轮也记录到跨渠道传播（'+CE_LINE(PIV_CE)+'）' : '')
        : '全程没有出现单轮下跌；'+
          (PIV_DROP.fresh.length ? '轮 '+PIV_DROP.round+' 有 '+PIV_DROP.fresh.join('、')+
            (/[A-Za-z0-9]$/.test(PIV_DROP.fresh[PIV_DROP.fresh.length-1]) ? ' ' : '')+'群体首次发声' : ''))+
      (PIV_CE && PIV_CE.round !== PIV_DROP.round
        ? '；轮 '+PIV_CE.round+'（'+SROW(PIV_CE.round).window[0]+'）的下跌与跨渠道传播同轮（'+
          CE_LINE(PIV_CE)+'）' : '')+
      /* ★ 2026-10-15 第二十六轮：零转载时这一句原先印「全程没有发生跨渠道传播」——
         与同段自第十三轮起就写明的「跨渠道引用」口径打架（EVT-011 有 26 条引用）；
         末句 else 支还会印出「跨渠道流转已全部发生在最低点之前（共 0 起）」。 */
      (NO_XC
        ? '。全程没有发生跨渠道传播；'
        : (NO_CE
            ? '。全程没有发生跨渠道转载，但有 '+QX.length+' 条跨渠道引用把内容带到了别的平台；'
            : '。本事件最早的一起跨渠道传播发生在轮 '+CE[0].round+'（'+CE[0].window[0]+'），共 '+CE.length+' 起；'))+
      '轮 '+LOW_V.round+' 见底转横盘之后，'+
      (CE_AFTER ? '内容仍在跨渠道流转（'+CE_AFTER+' 起），却不再把效价推低，'
        : (NO_CE ? '本跑全程没有转载驱动的跨渠道流转，效价也没有再被往下推，'
                 : '跨渠道流转已全部发生在最低点之前（共 '+CE.length+' 起），此后再无新增，效价也不再被继续推低，'))+
      '说明负面的同质化已经饱和 —— 新增声量只改变规模，不改变基调。读图时须把事件与节律分开：'+
      VALLEY_LEAD+'（共 '+VALLEY_ROUNDS.length+' 轮'+VALLEY_CNT+
      '，均落在覆盖 02:00—05:00 活动谷格的轮次上），'+
      '那是仿真层钟点增益的机械结果，不是舆情回暖。'));
});

/* ---- 2.1 正文 ---- */
SEC(1583, function(){
  /* ★ 2026-10-20：原先只按**绝对**回升量 0.02 分档 —— EVT-002 全程极差只有 0.018，
     回升 0.013（占极差七成、末值已在区间高端）却被判成「尾段走平」，与同一段自己
     算出的「基本收复」直接打架。改为「绝对 0.02」**或**「占极差一半」任一成立即算回升。 */
  /* ★ 2026-10-20 第二十八轮：形态与「其后」原先是两把尺子 —— 形态看「末值离**谷底**多远」，
     「其后」看「末值离**第一日终点**多远」。EVT-008 上两句当场打架：形态判「缓慢回升」
     （末值比谷底高 0.026），「其后」判「转入缓慢下行」（末值比第一日终点低 0.014），
     同一句里先说升、后说降。现统一按**谷底落在哪一段**分档：「其后」问的本就是
     第一日之后先跌还是先升，谷底的位置就是它的答案。 */
  var lowEarly = LOW_V.round <= D1_END.round;
  var lowLate  = LOW_V.round >= N - 6 + R0;   /* 轮号域，见顶部 R0 */
  var shapeTxt = lowLate ? '「快速下探、持续阴跌、末段才见底」'
               : (lowEarly
                   ? (RECOV >= 0.02 || V_RECOV_SH >= 0.5 ? '「快速下探、底部震荡、缓慢回升」'
                   : (RECOV <= -0.02 ? '「快速下探、底部震荡、尾段继续走低」'
                                     : '「快速下探、底部震荡、尾段走平」'))
                   : '「快速下探、底部震荡、末段回升」');
  /* ★ 2026-10-20 第二十九轮：首日那句原先无条件写「结束时效价已跌至 X，完成全程跌幅的 N%」。
     EVT-002 实测：全程最低点就是 t0 本身（轮 0，-0.228），首日结束时其实已回升到 -0.141，
     分母 |最低点 − t0| = 0 ⇒ 印成「完成全程跌幅的 8700000000%」。改为按首日净变化的方向、
     以及最低点是否落在首日窗口内，分三支陈述；分母退化时不再出现百分比。 */
  var D1_DELTA = D1_END.valence - S[0].valence;
  var D1_TXT = (function(){
    if(Math.abs(D1_DELTA) < 5e-4){
      return '结束时与 t0 基本持平（'+f3(D1_END.valence)+'）';
    }
    if(D1_DELTA < 0){
      var full = Math.abs(LOW_V.valence - S[0].valence);
      if(LOW_V.round <= D1_END.round || full < 5e-4){
        return '结束时跌至 '+f3(D1_END.valence)+'（较 t0 下跌 '+f3(-D1_DELTA)+
               '）；全程最低点就落在首日区间内（轮 '+LOW_V.round+'，'+f3(LOW_V.valence)+'），首日即见底';
      }
      return '结束时跌至 '+f3(D1_END.valence)+'，完成全程跌幅的 '+pct(Math.abs(D1_DELTA)/full);
    }
    return '结束时回升至 '+f3(D1_END.valence)+'（较 t0 上行 '+f3(D1_DELTA)+
           '）；全程最低点就是 t0 自身的 '+f3(S[0].valence)+'，开盘即见底，此后未再下探';
  })();
  var tailTxt  = (V_SPAN <= 1e-9) ? '其后基本原地不动'
               : (lowEarly
                   ? (RECOV >= 0.02 || V_RECOV_SH >= 0.5 ? '其后转为止跌回升'
                                                         : '其后转入底部震荡')
               : (lowLate ? '其后继续走低，谷底直到末段才出现'
                          : '其后继续走低至轮 '+LOW_V.round+' 才见底，再缓慢回升'));
  setHTML('r21',
    p('全场平均效价自 t0 的 '+f3(S[0].valence)+' 起步，'+
      /* ★ 2026-10-14 第二十二轮：原先无条件写「全程维持在负值区间」；EVT-002 全程效价
         在 +0.035~+0.048 之间，这句话与该段自己印出的正数直接打架。改为按 `anyNeg` 分档。 */
      (V_NEG_FROM.anyNeg
        ? '全程维持在负值区间，'
        : '全程<b>没有跌破 0</b>，一直在正区间运行，')+
      N+' 轮均值 '+
      f3(V_MEAN)+'。曲线呈'+shapeTxt+'的形态：第 1 个仿真日（轮 '+R0+'—'+D1_END.round+'，至 '+
      D1_END.window[1]+'）'+D1_TXT+'；'+tailTxt+'，全程 '+(N-1)+' 次轮间变动中有 '+DROP_N+
      ' 次为下跌。最低点出现在轮 '+LOW_V.round+'（'+LOW_V.window[0]+'），全场效价 <b>'+
      f3(LOW_V.valence)+'</b>；末轮（轮 '+(N-1+R0)+'，'+S[N-1].window[0]+'）收于 '+f3(S[N-1].valence)+
      '，'+(Math.abs(RECOV) < 1e-9 ? '与最低点持平'
        : '较最低点'+(RECOV > 0 ? '回升' : '继续下跌')+' '+f3(Math.abs(RECOV)))+
      /* ★ 2026-10-14 第二十二轮：原先无条件写「末值与谷底处在同一水平，推演结束时情绪
         没有走出底部」——「同一水平」从未与数据比过。EVT-002 的回升量 0.013 恰好等于全程
         极差 0.013，末值其实是全程最高位，「没有走出底部」为假。改为按回升量占全程极差
         的比例分三档（区间退化时另有兜底）。 */
      (V_SPAN <= 1e-9
        ? '，全程效价没有变化。'
        : (V_RECOV_SH >= 0.5
            ? '，回升量占全程极差（'+f3(V_SPAN)+'）的 '+pct(V_RECOV_SH)+
              '，末值已回到区间高端，情绪在推演末段基本收复。'
            : (V_RECOV_SH <= 0.1
                ? '，回升量只占全程极差（'+f3(V_SPAN)+'）的 '+pct(V_RECOV_SH)+
                  '，末值仍贴在谷底附近，推演结束时情绪没有走出底部。'
                : '，回升量占全程极差（'+f3(V_SPAN)+'）的 '+pct(V_RECOV_SH)+
                  '，末值处在区间中段，情绪在推演末段只是部分收复。')))) +
    p('负面智能体占比自 t0 起即处于 '+pct(S[0].neg)+
      /* ★ 2026-10-14 第二十二轮：「的高位」原为无条件形容。 */
      (S[0].neg >= 0.5 ? ' 的高位' : '')+'，自第 2 个仿真日起未低于 '+
      pct(NEG_D2)+'，峰值 <b>'+pct(PEAK_NEG.neg)+'</b> 出现在轮 '+PEAK_NEG.round+'（'+
      PEAK_NEG.window[0]+'）'+
      /* ★ 2026-10-14 第二十二轮：「该轮作用域内的全部智能体均为负面」原为无条件断言，
         只在峰值恰为 100% 时成立（EVT-001 是 100%，EVT-002/003/004/005 分别是 47%/68%/94%/58%）。 */
      (PEAK_NEG.neg >= 0.9999
        ? '——该轮作用域内的全部智能体均为负面'
        : '——为全程最高，但仍有 '+pct(1 - PEAK_NEG.neg)+' 的智能体未转为负面')+
      '；末轮为 '+pct(S[N-1].neg)+
      '。'+
      /* ★ 2026-10-14 第二十二轮：后半句的推理原为无条件 —— 若全程最低占比本就很小，
         「没有回落到 X 以下」推不出「全场的共同基调」。 */
      (NEG_MIN_V >= 0.5
        ? '全程没有回落到 '+pct(NEG_MIN_V, 0)+' 以下，说明负面不是少数账号的极端表达，而是全场的共同基调。'
        : '全程最低曾落到 '+pct(NEG_MIN_V, 0)+'（轮 '+NEG_MIN_R.round+
          '），负面并非全时段的共同基调，需按轮次分档理解。')+
      '（口径：负面占比统计该轮作用域内<b>全部</b>智能体中效价为负的比例，非仅在线者。）') +
    /* ★ 2026-10-20 第二十七轮：拐点改由公式产出后，本段跟着改两处 ——
       ① 每条拐点先写**判据本身**（日级趋势由什么状态转为什么状态），单轮涨跌退为附注；
       ② 拐点数可以是 0（公式没找到状态切换），也可以是 5 个以上，故「其一…其四」改用
          「其 + CN_NUM(k+1)」现算，0 个时整段改写，不再印「曲线上的零个方向性拐点」。
       保留 2026-10-14 第二十二轮的现算口径：命中数为 0 时整句改写、角色与跨渠道全部现算。 */
    p(PIVOTS.length === 0
      ? '按本节末的拐点公式，'+N+' 轮内日级均值曲线始终处在一个趋势状态，'+
        '没有任何一轮发生状态切换 —— 本跑的方向性拐点为 <b>0 个</b>，'+
        '曲线呈单向走势，中途没有反转。全程效价最低点出现在轮 '+LOW_V.round+'（'+
        LOW_V.window[0]+'），为 <b>'+f3(LOW_V.valence)+'</b>，在图 2-1 上以蓝点单独标出。'
      /* ★ 2026-10-21 第三十三轮：图上不再标红点，「曲线上的①…」这类指着图的说法一并改掉，
         只保留公式判出来的结论本身（几处、能不能在传播路径中找到对应）。 */
      : ('按本节末的拐点公式，'+(PIVOTS.length === 1 ? '全程只判出 <b>1 个</b>方向性拐点，' : '全程判出 '+CN_NUM(PIVOTS.length)+'个方向性拐点')+
      /* ★ 2026-10-14 第二十二轮：命中数为 0 时原先印成「…能找到对应的是其中零个」；改为整句改写。
         ★ 2026-10-20 第二十七轮：拐点可能只有 1 个 ⇒ 「中没有任何一个」这种复数说法不成话，单列一支。 */
      (function(){
        var hit = PIVOTS.filter(function(q){ return q.ce.length || q.fresh.length; }).length;
        if(hit === PIVOTS.length) return (PIVOTS.length === 1)
          ? '它能在传播路径中找到对应'
          : '都能在传播路径中找到对应';
        if(hit === 0) return (PIVOTS.length === 1)
          ? '它不能在传播路径中找到对应'
          : '中没有任何一个能在传播路径中找到对应';
        return '中能在传播路径中找到对应的是其中' + CN_NUM(hit) + '个';
      })()+
      '。'+PIVOTS.map(function(q, k){
        /* 只有一个拐点时不用序数词，「第一个拐点」读着像还有第二个 */
        /* ★ 2026-10-20：序号用「其一/其二/其三」，而 `CN_NUM(2)` 返回「两」（基数用法）
           ⇒ 第二处会印成「其两」。序号单独取字。 */
        var s = (PIVOTS.length === 1 ? '' : '其'+(['一','二','三','四','五','六','七','八','九','十'][k] || String(k + 1))+'，')+
                /* ★ 2026-10-20 第二十八轮：`PIV_TURN` 里 0|1、-1|1、1|-1 三档的值就是「转为回升 /
                   转为下跌」，与前半句「由 A 转为 B」重复，读起来像「由走平转为回升（转为回升）」
                   （EVT-002 轮 7 实测）。只有「止跌转平 / 涨势止步」是补充信息，才加括注。 */
                '轮 '+q.round+'（'+q.window[0]+'），日级趋势由<b>'+
                q.from+'</b>转为<b>'+q.to+'</b>'+
                (q.turn === '转为'+q.to ? '' : '（'+q.turn+'）')+'，该轮单轮效价变动'+
                /* 变动不足千分之一时不印 -0.000 / ≈0.000，改说「不足 0.001」（EVT-005 的轮 24 正是 0）。
                   ★ 空格只在数字分支里给，否则会印成「变动 不足 0.001」。 */
                (Math.abs(q.dv) < 5e-4 ? '不足 0.001' : ' '+(q.dv > 0 ? '+' : '')+f3(q.dv));
        if(q.i === PIV_DEEP_R) s += '，同时是全程最深的一次单轮下跌';
        /* ★ 2026-10-20 第二十八轮：原为「同轮 客户 在……」，中西文空格把角色名夹在中间（EVT-005 实测）。 */
        if(q.fresh.length) s += '；同轮'+q.fresh.join('、')+'在本事件中首次发声，讨论主体的构成随之改变';
        if(q.ce.length) s += '；同轮记录到'+CN_NUM(q.ce.length)+'起跨渠道传播（'+CE_LINE(q)+
                            '），内容跨出原发平台，情绪变化与传播范围扩大同时发生';
        if(q.round === LOW_V.round) s += '；该轮触及全程最低 '+f3(LOW_V.valence)+' 后转入锯齿横盘，'+
          /* ★ 2026-10-14 第二十二轮：谷底就在末轮时（EVT-003 的轮 41 = 末轮）原先印「其后 0 轮没有再创新低」 */
          (DOWN_STREAK ? '其后 '+DOWN_STREAK+' 轮没有再创新低，' : '其后直到推演结束都没有再创新低，')+
          /* ★ 2026-10-15 第二十六轮：零转载时「此后再无新增跨渠道传播」为假（本跑确有跨渠道
             **引用**，只是不产生渠道边）⇒ 按 NO_CE 换成「渠道边」口径的说法。 */
          (CE_AFTER ? '同期跨渠道传播仍发生 '+CE_AFTER+' 起，但未能把效价继续推低'
                    : (NO_CE ? '此后再无新增渠道边（跨平台流动由引用完成，不产生渠道边），效价亦未被继续推低'
                             : '此后再无新增跨渠道传播，效价亦未被继续推低'));
        return s+'。';
      }).join('')+ (CE.length ? '最早的一起跨渠道传播发生在轮 '+CE[0].round+'（'+
        CE[0].window[0]+'），全程共 '+CE.length+' 起。' : ''))) +
    p('情绪走势与传播热度并不同步。互动量峰值 '+PEAK_INT.flow+' 次出现在轮 '+PEAK_INT.round+'（'+
      PEAK_INT.window[0]+'）'+PEAK_INT_TIE+'，比情绪最低点'+(PEAK_INT.round >= LOW_V.round ? '晚 ' : '早 ')+
      /* ★ 2026-10-14 第二十一轮：原先方向写死为「早」，换事件后互动峰落在情绪谷底之后会印出「早 −3 轮」。 */
      Math.abs(LOW_V.round - PEAK_INT.round)+' 轮、约 '+
      (Math.abs(LOW_V.round - PEAK_INT.round) * spanOf(S[0]))+' 小时；发帖量峰值 '+PEAK_POST.post+
      ' 帖出现在轮 '+PEAK_POST.round+'。'+
      /* ★ 2026-10-14 第二十一轮：原先写死「情绪见底发生在发帖量最高峰的同一时段」，与紧邻的
         「仅差 N 轮」本就打架；改为按两者的先后与间距现算。
         ★ 2026-10-20 第二十八轮：改到现算之后，原「与最低点仅差 N 轮」与句末「早/晚 N 轮」
         说的是同一件事（EVT-002 实测连印两遍「1 轮」）⇒ 去掉前一处，只留带方向的后者。 */
      /* ★ 2026-10-14 第二十一轮：原先写死「早期的互动高峰并没有把情绪砸下去」，换事件后互动峰
         落在情绪谷底之后（EVT-005：峰在轮 35、谷底在轮 24），「早期」二字当场反了；改为按先后现算。 */
      (PEAK_INT.round === LOW_V.round
        ? '互动高峰与情绪见底落在同一轮，峰值互动并没有把情绪托住，'
        : (PEAK_INT.round < LOW_V.round
            ? '互动高峰出现在情绪见底之前，并没有把情绪砸下去，'
            : '互动高峰出现在情绪见底之后，此时情绪已在低位，互动量回升也未能把它托起来，'))+
      (PEAK_POST.round === LOW_V.round
        ? '情绪见底与发帖量最高峰落在同一轮'
        : '情绪见底（轮 '+LOW_V.round+'）比发帖量最高峰（轮 '+PEAK_POST.round+'）'+
          (LOW_V.round < PEAK_POST.round ? '早 ' : '晚 ')+
          Math.abs(LOW_V.round - PEAK_POST.round)+' 轮')+
      '。') +
    /* ★ 2026-10-20 第二十七轮：原先这一段无条件收在「以「热度下降 / 效价回升 / 效价未转负」为信号
       判定舆情平息的结论不成立」—— 三种写法都是**先有结论再找理由**，且「修复通道」此前没有定义。
       现在拆成两段：先讲风险形态（深度还是宽度，全部现算），再按 2.1 自己公布的平息判据判结果。 */
    p('本次事件的情绪风险形态是'+
      (V_NEG_FROM.anyNeg
        ? (V_RECOV_SH <= 0.25
            ? '<b>深度负面固化</b>：效价全程在负区间，末轮与最低点仅相差 '+f3(Math.abs(RECOV))+
              '，回升量占全程极差的 '+pct(Math.max(V_RECOV_SH, 0))+'。'
            : '<b>末段确有回升，但不满的人数面未同步收窄</b>：末轮已比谷底高 '+f3(Math.abs(RECOV))+
              '（占全程极差的 '+pct(V_RECOV_SH)+'），而末轮负面占比仍有 '+pct(S[N-1].neg)+
              '，较峰值 '+pct(PEAK_NEG.neg)+'（轮 '+PEAK_NEG.round+'）只降了 '+
              pct(Math.max(0, PEAK_NEG.neg - S[N-1].neg))+'。')
        : '<b>宽度而非深度</b>：效价全程在 0 上方运行，负面占比却持续维持在 '+
          pct(NEG_MIN_V)+'—'+pct(PEAK_NEG.neg)+' 的宽度上 —— 分歧体现在「有多少人不满」，'+
          '而不是「不满有多深」。')) +
    /* ---- 自然平息判定：两个必要条件 + 一个分级（判据在 `CALM` 处定义，此处把三个指数全写出来） ---- */
    p('自然平息判定：<b>'+CALM.level+'</b>。判据是两个必要条件加一个分级，三个指数逐条列出 —— '+
      '① <b>量能回落</b> H = '+f2(CALM.H)+'（门槛 0.50，'+(CALM.needH?'<b>已过</b>':'<b>未过</b>')+
      '）：末 6 轮（24 小时）发帖合计 '+n0(CALM.w)+' 条，全程最旺的 6 轮为 '+n0(CALM.wMax)+' 条，'+
      '回落 '+(CALM.wMax > 0 ? pct(1 - CALM.w / CALM.wMax) : '—')+'；'+
      '② <b>情绪转好</b> V = '+f2(CALM.V)+'（门槛 0.35，'+(CALM.needV?'<b>已过</b>':'<b>未过</b>')+
      '）：末 6 轮效价均值 '+f3(CALM.vTail)+'，较全程最低 '+f3(CALM.vMin)+' 回升，'+
      '回升指数 V_up = '+f2(CALM.vUp)+'，末段落在「'+CALM.vBand+'」档、档位分 V_lvl = '+
      f2(CALM.vLvl)+'；'+
      '③ <b>不满面收窄</b> N = '+f2(CALM.Nq)+'（仅在 ①② 都过时参与分级）：负面占比的 6 轮均值'+
      '由峰值 '+pct(CALM.nPeak)+' 变为末段 '+pct(CALM.nLast)+'。'+
      (CALM.level === '未平息'
        ? (CALM.needH
            /* ★ 第二十八轮：原写死「回落到峰值的一半上下」—— H ≥ 0.50 对应的其实是**回落 25% 以上**，
               与「一半」不是一个量级（EVT-008 只回落 25.3%，却印成「一半上下」）。改为现算降幅。 */
            ? '① 已过、② 未过 —— 发布量确实回落了 '+
              (CALM.wMax > 0 ? pct(1 - CALM.w / CALM.wMax) : '—')+
              '（门槛 25%），但情绪没有跟着转好；'+
              '关注度先撤、情绪后走是本次事件的特征，故判<b>未平息</b>。'
            : (CALM.needV
                ? '② 已过、① 未过 —— 情绪面已具备平息条件，但发布量只回落了 '+
                  (CALM.wMax > 0 ? pct(1 - CALM.w / CALM.wMax) : '—')+
                  '（门槛 25%），事件仍有人在持续推动，故判<b>未平息</b>。'
                : '①② 均未过 —— 发布量只回落了 '+
                  (CALM.wMax > 0 ? pct(1 - CALM.w / CALM.wMax) : '—')+
                  '（门槛 25%）、情绪也没有转好，'+
                  '本次事件在推演窗口内<b>既没有降温也没有修复</b>。'))
        : (CALM.level === '已平息'
            ? '三项全部达到门槛，情绪在推演窗口内<b>完成了自然平息</b>。'
            : '①② 已过、③ 未达门槛：规模确已回落、效价也已转好，但不满的人数面没有同步收窄，'+
              '只能判「<b>趋于平息</b>」。'))) +
    /* ---- 修复通道（★ 2026-10-20 第二十七轮新增，公式在 `REPAIR` 处定义） ----
       原先这里根本没有「修复通道」这个概念，报告却一路写「未进入修复通道」—— 判据不存在，
       自然只能一个都不命中。现在三条判据写死、逐窗现算，命中就说命中，不命中就报**最接近**的那一段
       以及三条各自差多少，而不是含糊地写一句「没有修复」。 */
    p('修复通道：把「连续 6 轮（= 24 小时）之内，效价净回升、不满的人数面净收窄、期间没有内容跨渠道扩散」'+
      '的一段窗口称为一次修复通道。三条判据必须同时满足 —— 单看效价回升会被噪声骗，'+
      '效价回升而不满面不动说明只是少数账号变温和，窗口内正跨平台扩散则回升可能来自新受众的稀释而非修复。'+
      (REPAIR.has
        ? '本次推演命中<b>'+CN_NUM(REPAIR.hits.length)+'</b>段：'+
          REPAIR.hits.map(function(h){
            return '轮 '+(h.lo+R0)+'→'+(h.hi+R0)+'（自 '+S[h.lo].window[0]+' 起），效价 '+f3(S[h.lo].valence)+
                   '→'+f3(S[h.hi].valence)+'（Δ = '+(h.dv>=0?'+':'')+f3(h.dv)+
                   '，门槛 '+f3(REPAIR.floor)+'），负面占比 '+pct(S[h.lo].neg)+'→'+pct(S[h.hi].neg)+
                   '（Δ = '+(h.dn>=0?'+':'')+n1(h.dn*100)+' 个百分点），期间跨渠道新增 0 起 —— 三条全中。';
          }).join('')+
          '这几段是全程唯一同时满足三条的窗口：在没有外部干预的前提下，情绪在一天之内自行修复了一部分。'+
          '注意修复通道只看窗口两端的净变化，不保证下一段窗口接着修复；'+
          '窗口是否能接续，要看后面 2.2 节的逐轮曲线。'
        : '本次推演<b>没有任何</b> 6 轮窗口同时满足三条。最接近的一段是轮 '+(REPAIR.best.lo+R0)+'→'+
          (REPAIR.best.hi+R0)+'（自 '+S[REPAIR.best.lo].window[0]+' 起）：效价 '+f3(S[REPAIR.best.lo].valence)+
          '→'+f3(S[REPAIR.best.hi].valence)+'（Δ = '+(REPAIR.best.dv>=0?'+':'')+f3(REPAIR.best.dv)+
          '，门槛 '+f3(REPAIR.floor)+'，'+(REPAIR.best.dv>=REPAIR.floor?'<b>满足</b>':'<b>未达</b>')+'）；'+
          '负面占比 '+pct(S[REPAIR.best.lo].neg)+'→'+pct(S[REPAIR.best.hi].neg)+
          '（Δ = '+(REPAIR.best.dn>=0?'+':'')+n1(REPAIR.best.dn*100)+' 个百分点，门槛 −2.0，'+
          (REPAIR.best.dn<=-0.02?'<b>满足</b>':'<b>未达</b>')+'）；期间跨渠道新增 '+REPAIR.best.eg+
          ' 起（'+(REPAIR.best.eg===0?'<b>满足</b>':'<b>未达</b>')+'）。'+
          (REPAIR.best.pass===0 ? '三条<b>一条也没</b>满足' : '三条中满足'+CN_NUM(REPAIR.best.pass)+'条')+
          '：'+(REPAIR.best.pass===0
            ? '即便把满足条数最多的一天挑出来，情绪与人数面仍没有同步改善，'+
              '本次事件在推演窗口内<b>没有发生任何自发修复</b>。'
            : '差在'+REPAIR.best.unmet.join('与')+(REPAIR.best.unmet.length===1?'这一条':'这两条')+' —— '+
              (REPAIR.best.unmet.length===1
                ? '其余条件都已具备，只是没有同时成立，故只能算「局部好转」，不能算进入修复通道。'
                : '两条同时落空，离进入修复通道还有明显距离，故只能算「局部好转」，不能算进入修复通道。')))) +
    p((SEG_UNIFORM
        ? '读图口径：本表每轮覆盖时长等长（'+spanOf(S[0])+' 小时/轮，共 '+N+
          ' 轮），各点之间可直接比较，相邻两点的落差即真实变化，无需再按轮长折算。'
        : '读图口径：本表并非等长轮次（'+BANDS.map(function(b){
              return '轮 '+b.lo+'—'+b.hi+' 每轮 '+spanOf(SROW(b.lo))+' 小时'; }).join('；')+
          '），切换点已在图上以阴影区标出。读数时应将相邻轮的差异结合该轮覆盖时长一并理解，'+
          '而非只比较相邻两点的落差。')+
      (CORR_G === null ? '' :
        VALLEY_LEAD+'（'+VALLEY_ROUNDS.map(function(x){ return '轮 '+x.round; }).join('、')+
        '，共 '+VALLEY_ROUNDS.length+' 轮'+VALLEY_CNT+'）。'+
        '这些轮次覆盖 02:00—05:00 的活动谷格，仿真层的钟点活动增益取最低档 '+GHAT_TXT(GHAT_MIN)+
        '（峰值档 '+GHAT_TXT(GHAT_MAX)+'），传染推力最小，'+VALLEY_MECH+
        '以逐轮快照自记的钟点增益与单轮涨跌求相关，系数为 '+f2(CORR_G)+
        (Math.abs(CORR_G) >= 0.60
          ? '（增益越低、效价越容易回升），相邻轮的起伏大部分可由钟点解释，与事件无关。'
          : (Math.abs(CORR_G) >= 0.30
              ? '（增益越低、效价越容易回升），相邻轮的起伏有相当一部分可由钟点解释，其余来自事件。'
              : '（方向与预期的「增益越低、效价越容易回升」一致，但本跑几乎没有稳定对应），'+
                '说明这一场的夜间起伏不能全部归给钟点，事件的作用不容忽视。'))+
        /* ★ 2026-10-15 第二十五轮：这里也写死过「①②③三处标注」—— 与图 2-1 的图注同一个病。
           ★ 2026-10-20 第二十七轮：拐点可为 0 个，「图中零处标注」不成话 ⇒ 分两支。
           ★ 2026-10-21 第三十三轮：图上已不再标拐点 ⇒ 不再说「图中①②③标出的」。 */
        (PIVOTS.length === 0
          ? '拐点公式先在 6 轮滑窗上求日级均值、再判趋势状态，正是为了不把该类节律算成拐点。'
          : '正文判出的'+(PIVOTS.length === 1 ? '那一处拐点已' : CN_NUM(PIVOTS.length)+'处拐点均已')+
            '剔除该类节律。'))));
});

/* ---- 2.2 正文：分角色 / 分渠道 / 交叉洞察 ---- */
SEC(1639, function(){
  var mildVals = ROL_MILD ? D.role_mat[ROL_MILD] : null;
  var cDeep = CH_DEEP[0];
  var deepRC = RC_DEEP[0];
  var regN = (RCCNT['监管'] || {})['监管通报'] || 0;
  var chanTop = (CO[CH_TOP.n] || {}), roleTop = (RO[ROL_TOP.n] || {});
  /* ★ 2026-10-14 第二十一轮：这轮补三处「换事件才显形」的残留。 */
  /* ① 只作观察方存在的角色。原先正文无条件把「末轮最温和的那个角色」说成「观察立场不受舆情波及」——
     EVT-002 / EVT-004 上最温和的是**网民**（在场参与者），这句当场为假。
     判据：该角色的账号只落在「监管通报」这一个渠道上（`RCCNT` 是账号口径的渠道普查）。 */
  var obsRole = (function(){
    var m = null;
    ROLES.forEach(function(r){
      /* ⚠ `rc_cnt` 对每个角色都把 **6 个渠道**铺成了键（无人的那些取值 0），
         故必须先滤掉 0，不能只数键的个数 —— 只数键的话这里恒为 6、`obsRole` 恒为 null。 */
      var ks = Object.keys(RCCNT[r] || {}).filter(function(c){ return (RCCNT[r][c] || 0) > 0; });
      if(ks.length === 1 && ks[0] === '监管通报') m = r; });
    return m; })();
  /* ② 「最先恶化的是谁」只在场**参与者**里比。★ 2026-10-14 第二十一轮补：
     原先直接取 `ROL_LOW[0]`（全体角色里最低点来得最早的），EVT-003 上它就是**监管**（最低点在第 3 轮，
     幅度 -0.046）⇒ 同一段先写「最先出现情绪恶化的是监管群体」，紧接一句又写「其观察立场不受舆情波及」，
     自相矛盾。观察方不参与讨论，它的起伏属噪声，不该被当成事件信号；故先从候选里剔除。 */
  var ROL_PART = ROL_LOW.filter(function(x){ return x.n !== obsRole; });
  var rFirst  = ROL_PART[0] || ROL_LOW[0];
  /* ★ 2026-10-20 第二十九轮：原先「早于」从句直接取 `ROL_PART.slice(1,4)` ——
     EVT-002 实测：客户、KOL、媒体三者的最低点**都在第 0 轮**，句子却印成
     「来得最早的（与其余角色并列），早于KOL（第 0 轮）、媒体（第 0 轮）、网民（第 1 轮）」，
     并列被写成了「早于」。改为把「同一轮并列」与「更晚」拆成两组分别陈述。 */
  var rTie  = ROL_PART.slice(1).filter(function(x){ return x.i === rFirst.i; });
  var rLate = ROL_PART.slice(1).filter(function(x){ return x.i > rFirst.i; });
  function tieNames(){ return rTie.map(function(x){ return x.n; }).join('、'); }
  /* ③ 按**账号口径**数「某渠道的在册账号发出了多少条帖」。
     §2.2 这两段讲的是折线图的样本构成，而折线（`ch_mat` / `role_mat`）取自记录员逐 scope 行：
     `recorder_a.py:154` 明写 scope=渠道 时按 `agent['primary_channel']` 过滤 agent
     ⇒ **账号口径**，与 `agents` 同源。故同一句里的「条帖」也必须用账号口径，
     不能拿 `CO[c].posts`（**帖的原发渠道**口径）来配 —— 那正是 EVT-004「监管通报 4 → 0」分叉的地方。 */
  var AG_CH = {}; ND.forEach(function(z){ AG_CH[z.agent_id] = z.channel; });
  function postsOfChan(c){
    return POSTS.filter(function(p2){ return AG_CH[p2.agent] === c; }).length; }
  /* ★ 2026-10-14 第二十一轮：本段原先通篇写死角色名 / 渠道名（客户、网民、KOL、媒体、微博、小红书、抖音）
     以及「1 名客户账号」「意见领袖的负面表达集中在事件中段」「抖音是例外：其唯一的一名客户账号」
     这类只对 EVT-001 成立的断言。换事件后实测印出与数据相反的话：
       · EVT-002/003：末轮最负的其实是媒体 / KOL，却写作「客户群体全程最负」；
         客户最低点在第 0 轮（正是推演**起点**），却写作「推演结束时仍在走低，没有见底迹象」；
         最先恶化的是客户 / 监管，却写作「意见领袖的负面表达集中在事件中段」。
       · EVT-002：「最深的单点出现在监管通报……该渠道只承载 1 名客户账号」（监管通报上压根没有客户）。
     现整段改为按本轮结果现算。⚠ 这是本次唯一**允许 EVT-001 渲染文字随之变化**的段落
     —— 原句是手写的，本身并非句句为真（如「客户与网民则是持续下行」在 EVT-001 上也需重核）。 */
  var R22 = (function(){
    var rl    = ROL_RANK.length ? ROL_RANK[0].n : null;      /* 末轮最负的角色 */
    var rlA   = rl ? D.role_mat[rl] : null;
    var rlMin = rlA ? seriesMin(rlA) : null;
    var deep  = (function(){ var m = null;
      ROLES.forEach(function(r){ var q = seriesMin(D.role_mat[r]);
        if(!q.ok) return; if(!m || q.v < m.v) m = {n:r, v:q.v, i:q.i}; });
      return m; })();                                        /* 全程最低点最深的角色 */
    var domAll = (rlA ? ROLES.every(function(r){
      if(r === rl) return true;
      var b = D.role_mat[r]; if(!b) return true;
      for(var i2 = 0; i2 < N; i2++){
        var x = rlA[i2], y = b[i2];
        if(x === null || x === undefined || y === null || y === undefined) continue;
        if(x > y) return false; }
      return true; }) : false);                              /* 是否逐轮都不高于其余任何角色 */
    function segOf(i){ return i < N/3 ? '前段' : (i < 2*N/3 ? '中段' : '后段'); }
    /* 某渠道由哪些角色、各多少名账号构成（原先这几处全是写死的） */
    function chanRoles(c){
      var out = [];
      ROLES.forEach(function(r){ var n2 = ((RCCNT[r] || {})[c]) || 0;
        if(n2 > 0) out.push({r:r, n:n2}); });
      return out.sort(function(a2, b2){ return b2.n - a2.n; }); }
    function roleStr(c){ var z = chanRoles(c);
      return z.length ? z.map(function(q){ return q.r+' '+q.n+' 名'; }).join('、') : '0 名'; }
    /* 同一渠道内角色差距最大的那一处（原写作「微博上客户与网民相差 X」） */
    var rcGap = (function(){ var m = null;
      CH_PLAT.forEach(function(cx){
        var vs = RC_CELLS.filter(function(z){ return z.c === cx; });
        if(vs.length < 2) return;
        var hi = vs[0], lo = vs[0];
        vs.forEach(function(z){ if(z.v > hi.v) hi = z; if(z.v < lo.v) lo = z; });
        if(hi === lo) return;
        var g = {c:cx, a:hi.r, b:lo.r, v:Math.abs(hi.v - lo.v)};
        if(!m || g.v > m.v) m = g; });
      return m; })();
    /* 只由 ≤1 名账号支撑的有效组合（原写作「抖音是例外：其唯一的一名客户账号」） */
    var thin = RC_CELLS.filter(function(z){ return z.n <= 1; });

    return {
      p1: '<b>分角色看</b>，末轮效价由最负到最正的次序为：'+ROL_RANK.map(function(x){
          return x.n+' '+f3(x.v); }).join('、')+'。'+
        ((rl && rlMin) ? rl+'群体'+(deep && deep.n === rl ? '全程最负' : '末轮最负')+
          (domAll ? '，且逐轮都不高于其余任何角色' : '，但不是每一轮都最低')+
          '，其最低点出现在第 '+(rlMin.i + R0)+' 轮（'+f3(rlMin.v)+'），'+
          (rlMin.i >= N-1 ? '即推演结束时仍在走低，没有见底迹象。'
                          : '此后回到末轮的 '+f3(rlA[N-1])+'，并未停在谷底。')
          : '角色序列为空。')+
        (rFirst.n === rl
          ? '它的最低点也是全部在场角色里来得最早的'+       /* 上一句已说过这个角色与那个轮次，不再重复 */
            (rTie.length ? '（与'+tieNames()+'并列于第 '+(rFirst.i + R0)+' 轮）' : '')+'，'
          : (rFirst.i === 0
            ? '最低点来得最早的是'+rFirst.n+'群体 —— 第 '+(rFirst.i + R0)+' 轮（'+
              (R0 === 0 ? '推演起点' : '首个推演轮')+'）就是它的全程最低位 '+f3(rFirst.v)+
              '，属于「开局即最低」，不是中途转差'+
              (rTie.length ? '（与'+tieNames()+'并列于此轮）' : '')+
              /* 「早于……」从句要挂在同一句上，用分号会被读成残句（EVT-002 实测） */
              (ROL_PART.length > 1 ? '，' : '；')
            : '最先出现情绪恶化的是'+rFirst.n+'群体，最低点在第 '+(rFirst.i + R0)+' 轮（'+f3(rFirst.v)+
              '，属'+segOf(rFirst.i)+'）'+
              (rTie.length ? '，与'+tieNames()+'并列' : '')+'，'))+
        (rLate.length ? '早于'+rLate.slice(0, 4).map(function(x){
          return x.n+'（第 '+(x.i + R0)+' 轮）'; }).join('、')+'；' : '')+
        rFirst.n+'此后'+(function(){
          var L = D.role_mat[rFirst.n][N-1];
          var d = (L === null || L === undefined) ? null : L - rFirst.v;
          return d === null ? '的走势见表。'
               : (d > 1e-9 ? '回升 '+f3(d)+'，呈脉冲式。' : '未再回升。'); })()+
        (ROL_MILD ? ROL_MILD+'群体全程波动最小（区间 '+f3(Math.min.apply(null, mildVals))+' 至 '+
          f3(Math.max.apply(null, mildVals))+'），'+
          (ROL_MILD === obsRole ? '其观察立场不受舆情波及。'
                                : '但波动小只说明它的表达全程平稳，不能读作「立场中性」。') : ''),
      p2: '<b>分渠道看</b>，末轮次序为：'+CH_RANK.map(function(x){
          return x.n+' '+f3(x.v); }).join('、')+'。把窗口拉回全程，最深的单点出现在'+
        cDeep.n+'第 '+cDeep.round+' 轮（'+f3(cDeep.v)+'），该渠道在本场只承载'+roleStr(cDeep.n)+
        (function(){ var z = chanRoles(cDeep.n);
          return (z.length === 1 && z[0].n <= 2) ? '，读数代表性强但稳定性弱' : ''; })()+'。'+
        (CH_REV ? '渠道排序在推演过程中发生过反转：'+CH_REV.n+'在前 '+CH_REV.span+
          ' 轮一直是最负渠道，末轮回落到第 '+(CH_REV.i1 + 1)+' 位；'+CH_RANK[0].n+
          (CH_REV.i0 >= 0 ? '则由第 '+(CH_REV.i0 + 1)+' 位升为最负。' : '则后来升为最负。')+
          '两个渠道的人员构成分别是：'+CH_REV.n+'上为'+roleStr(CH_REV.n)+
          '，'+CH_RANK[0].n+'上为'+roleStr(CH_RANK[0].n)+
          '；「最负渠道」这一标签在本事件中本身并不稳定，不宜据此判断平台属性。' : '')+
        (regN ? '监管通报的曲线与监管角色完全重合 —— 同一批 '+regN+' 名账号，'+
          /* ★ 2026-10-14 第二十二轮：原写死「作为观察方始终未参与讨论」。EVT-004 的监管类
             账号全程发帖 4 条，这句话当场为假。改为按该角色实际发帖量现算。 */
          (ROof('监管').posts === 0
            ? '作为观察方始终未参与讨论。'
            : '其间共发出 '+ROof('监管').posts+' 条帖，介入方式是低量、低频的定调式发帖。') : ''),
      p3: '<b>两组曲线的交叉</b>可以分辨出驱动全场情绪的两个不同来源。'+
        '与全场效价曲线同步度最高的是'+ROL_TOP.n+'（r='+f3(ROL_TOP.c)+'）与'+CH_TOP.n+'（r='+
        f3(CH_TOP.c)+'）：'+CH_TOP.n+'在册 '+chanTop.agents+' 个账号、其账号共发出 '+
        postsOfChan(CH_TOP.n)+' 条帖'+
        /* ★ 第二十八轮：这句的条数是**账号口径**（按账号的 primary_channel 归属），与表 1-2
           「按帖子原发渠道」不是同一个数 —— EVT-008 印出「发出 219 条帖」，而表 1-2 同一条渠道
           是 198 条，读者会当成前后矛盾。两者不等时补一句口径说明。 */
        (CO[CH_TOP.n] && CO[CH_TOP.n].posts !== postsOfChan(CH_TOP.n)
          ? '（账号口径；若按帖子原发渠道计为 '+CO[CH_TOP.n].posts+' 条，见表 1-2）' : '')+
        '，'+ROL_TOP.n+'在册 '+roleTop.agents+' 个账号 —— 全场均值在很大程度上就是这两者的下沉，'+
        '因此曲线的形状由它们主导；这一同步性反映的是样本构成，而不是因果关系。'+
        (deepRC ? '把水平位压低的是另一处：'+deepRC.r+'在'+deepRC.c+'上是 '+RC_CELLS.length+
          ' 个有效角色—渠道组合中最深的一格（最低 '+f3(deepRC.mn)+'，'+deepRC.n+' 名账号）'+
          (RC_DEEP.length > 1 ? '，次深的是'+RC_DEEP[1].r+'×'+RC_DEEP[1].c+'（'+f3(RC_DEEP[1].mn)+
            '，'+RC_DEEP[1].n+' 名账号）' : '')+'。' : '')+
        (rcGap && CH_SPAN ? '同一平台上的角色差距（'+rcGap.c+'上'+rcGap.a+'与'+rcGap.b+'相差 '+
          f3(rcGap.v)+'）'+(rcGap.v > CH_SPAN ? '大于' : '小于')+'平台之间的差距（'+
          CN_NUM(CH_PLAT.length)+'个主要平台末轮极差 '+f3(CH_SPAN)+'），'+(rcGap.v > CH_SPAN
            ? '说明渠道差异由「谁在该平台上发声」的构成决定，而不是平台自身的情绪属性。'
            : '说明本场的渠道分化主要来自平台自身，而不是发声者的构成。') : '')+
        (thin.length ? '需要提醒的是，'+RC_CELLS.length+' 个有效组合里有 '+thin.length+
          ' 个只由 1 名账号支撑（'+thin.slice(0, 3).map(function(z){ return z.r+'×'+z.c; }).join('、')+
          (thin.length > 3 ? '等' : '')+'），这些格的读数样本过小，不宜单独解读。' : '')+
        '据此，若只针对声量最大的'+CH_TOP.n+'渠道开展沟通，改变的是曲线形状；'+
        (deepRC ? '要抬高它的水平位，须同时覆盖'+deepRC.r+'在'+deepRC.c+'上的表达。'
                : '要抬高它的水平位，须先补足样本过小的角色—渠道组合。')
    };
  })();
  setHTML('r22', p(R22.p1) + p(R22.p2) + p(R22.p3) + llmBlock('L1', '交叉解读'));
});

/* ================= 模块三 =================
   ★ 2026-10-12 第九轮：按仿真侧提供的 prompt 重写为「模块总结 + 3.1 关键时段 + 3.2 渠道影响力」。
   ★ 2026-10-12 第十二轮：仿真侧**重新定义「关键节点」= 关键事件**，3.1 按新定义重做
     （原为 图 3-1 时间线 / 表 3-1 清单 / 图 3-2 影响评估；3.1 的两张图已于第十四轮删除，
     只余表 3-1），原按账号统计的 3.1 降为 3.2（该盯谁），
     **该节已于第十五轮（2026-10-13）整节删除**（表 3-2 + 图 3-1 + #r32 + KN_* 派生量），
     关键时段 / 渠道影响力再由 3.3 / 3.4 上移为 3.2 / 3.3。
   ★ 2026-10-12 第十一轮：在「关键时段」之前补 3.1 关键节点（该盯谁），原两节顺延为 3.2 / 3.3；
     节点指标直接取载荷的 `out`（被引用）与 `rep`（被转发）之和，加 `betw`（必经路径数），
     跨圈层一维由 `posts[].q` + `posts[].ch` 现推 —— **未改 viz_extract.py**。
     整块删除：原 3.2（出度/介数/中心性三项排序 + 节点明细表）、原 3.4（按角色）、
     原 3.5（发帖结构与行为构成），以及随其失去消费方的 `rankBar()` 与 9 张图、2 张表。
     echarts 实例由 17 降为 8。
   ★ 关键时段的定义也在本轮由「t0 + 56 小时的钟点锚点」改为「连续 4 轮动作总量 argmax」
     （见脚本上方的 WIN 块），以便 3.1 能忠实回答「互动量最高的时间窗」这一问题。
   ★ 删除内容的原文留在 `_版本留痕_第九轮_20261012\` 与 `R50run_等长4h\README_本次运行.md` §3.10，
     需要恢复可整段贴回。
   ★ 2026-10-13 第十五轮：3.2（该盯谁，含表 3-2 / 图 3-1 / #r32）**整节删除**，关键时段与渠道影响力
     由 3.3 / 3.4 上移为 3.2 / 3.3，图 3-1 #chWin / 图 3-2 #chChanInt / 表 3-2 渠道明细；
     删除原文见 `_版本留痕_第十五轮_20261013\_del_3.2关键节点_删除内容.txt`。 */

/* ---- 模块三共用现算量 ---- */
var WSEG      = S.slice(WIN.lo, WIN.hi + 1);                       /* 窗口内的逐轮行 */
var WPOSTS    = POSTS.filter(function(x){ return x.r >= WIN.lo+R0 && x.r <= WIN.hi+R0; });
var W_ACTSH   = WIN_ACT / TOTAL_ACT;                               /* 窗口动作占全程 */
var W_HR_SH   = WIN_HOURS / __HOURS__;                             /* 窗口时长占全程 */
var W_DENS    = W_ACTSH / W_HR_SH;
var W_RC      = (function(){ var c={}; WPOSTS.forEach(function(p2){ c[p2.role]=(c[p2.role]||0)+1; }); return c; })();
var W_CC      = (function(){ var c={}; WPOSTS.forEach(function(p2){ c[p2.ch]=(c[p2.ch]||0)+1; }); return c; })();
var W_CC_RANK = Object.keys(W_CC).sort(function(a2,b2){ return W_CC[b2]-W_CC[a2]; });
var W_INDEG   = sum(WPOSTS, function(p2){ return p2.indeg; });
var W_TOP2POST= WPOSTS.slice().sort(function(a2,b2){ return b2.indeg-a2.indeg; })
                  .filter(function(p2){ return p2.indeg > 0; }).slice(0, 2);
/* ★ 2026-10-14 第二十一轮：窗口内「引用落在哪个渠道上」原先借用 `W_CC_RANK[0]`（**发帖最多**的渠道），
   两者换事件就分叉（EVT-002 该窗口引用落在抖音的帖上、发帖最多的却是微博，句子写成
   「1 次引用集中在微博的 0 条帖上」）。这里按引用量单独算一次。 */
var W_INDEG_CH= (function(){
  var c = {}; WPOSTS.forEach(function(p2){ if(p2.indeg > 0) c[p2.ch] = (c[p2.ch]||0) + p2.indeg; });
  var ks = Object.keys(c).sort(function(a2,b2){ return c[b2]-c[a2]; });
  return {top: ks[0] || null, n: c}; })();
/* ★ 2026-10-20 第二十九轮：r30「综合判断与建议」原先把 `W_CC_RANK[0]`（**窗口内发帖最多**的渠道）
   一律说成「窗口内的传播由它承载」「在渠道上仍以它为主战场」。EVT-002 实测该渠道是新闻门户：
   窗口内 1 条帖、**0 次引用**，三渠道各 1 条并列 —— 「由它承载传播」「仍是主战场」当场为假。
   先把这三件事现算出来，再按有没有引用、是否并列分两支说。 */
var W_POST_TOP  = W_CC_RANK.length ? W_CC_RANK[0] : null;
var W_POST_TIED = W_POST_TOP
  ? W_CC_RANK.filter(function(c){ return (W_CC[c]||0) === (W_CC[W_POST_TOP]||0); }) : [];
var W_TOP_IND_WIN = W_POST_TOP
  ? sum(WPOSTS.filter(function(p2){ return p2.ch === W_POST_TOP; }), function(p2){ return p2.indeg; }) : 0;
var W_CH_SPREAD_TXT = W_CC_RANK.map(function(c){ return c+' '+(W_CC[c]||0)+' 条'; }).join('、');
var W_TOPACC  = (function(){                                       /* 窗口内发帖账号，按全期介数降序 */
  var ag = {}; WPOSTS.forEach(function(p2){ ag[p2.agent] = (ag[p2.agent]||0)+1; });
  return Object.keys(ag).map(function(a2){
    var nn = ND.filter(function(z){ return z.agent_id === a2; })[0] || {};
    return {name:nn.name||a2, role:nn.role||'?', ch:nn.channel||'?', betw:nn.betw||0,
            out:nn.out||0, n:ag[a2]};
  }).sort(function(a2,b2){ return b2.betw - a2.betw; });
})();
/* ★ 2026-10-14 第二十二轮：r34 的「渠道治理建议」原先写死「重点对象是…那几个媒体账号」——
   那是 EVT-001 的角色构成。EVT-005 的窗口内三个介数最高账号全是 KOL，印出来即错。
   改为按 `W_TOPACC` 前三名的角色构成现算（与紧邻那半句读的是同一批账号）。 */
var W_TOP_ROLE_STR = (function(){
  var seen = [];
  W_TOPACC.slice(0, 3).forEach(function(z){ if(seen.indexOf(z.role) < 0) seen.push(z.role); });
  return seen.length ? seen.map(function(r){ return r+'类'; }).join('、')+'账号' : '账号';
})();
var ACT_MAX   = Math.max.apply(null, RACT);
var ACT_MAXR  = S.filter(function(x){ return (x.post+x.comment+x.repost+x.like) === ACT_MAX; });
var ACT_WIN_N = ACT_MAXR.filter(function(x){ return x.round>=WIN.lo+R0 && x.round<=WIN.hi+R0; }).length;
var W_SEC     = (function(){                                       /* 第二名的 4 轮窗口 */
  var bst = null;
  for(var i2 = 0; i2 + 4 <= N; i2++){
    if(i2 === WIN.lo) continue;
    var s2 = RACT[i2]+RACT[i2+1]+RACT[i2+2]+RACT[i2+3];
    if(bst === null || s2 > bst.s) bst = {lo:i2, s:s2};
  }
  return bst; })();
/* 渠道每帖被引降序。★ 用 `CHS`（全部 6 个渠道）而**不是** `CH_ORDER` —— 后者只含 4 个主要
   平台（微博/小红书/抖音/新闻门户），用它会把监管通报与黑猫投诉整个漏掉；这两个渠道虽然
   全程无发帖、每帖被引为 0，但正是「有智能体关注、无内容产出」这一事实本身要被看见。 */
var PP_RANK   = CHS.filter(function(c){ return CO[c]; })
                  .sort(function(a2,b2){ return CO[b2].out_per_post - CO[a2].out_per_post; });
var PP_POSTS  = PP_RANK.filter(function(c){ return CO[c].posts > 0; });
var PP_TOP    = PP_POSTS[0], PP_LAST = PP_POSTS[PP_POSTS.length - 1];
var PP_HAS    = PP_POSTS.length > 0;                    /* 有没有「有内容产出」的渠道 */
/* ★ 第十八轮：「全程无发帖」的渠道一律**现算**，不再写死渠道名。
   同一份代码跑不同事件：这里恰好是 监管通报 与 黑猫投诉，换个跑可能是别的渠道、也可能一个都没有。 */
var CH_ZERO_JS  = CHS.filter(function(c){ return !CO[c] || !CO[c].posts; });
var CH_ZERO_STR = CH_ZERO_JS.join('与');
/* 图 3-2 的类目 yAxis：echarts 把**第一项画在最下方**，故取降序的逆序，
   使每帖被引最高的渠道出现在最上面。绘图与 tooltip 共用这一个数组。 */
var PP_PLOT   = PP_RANK.slice().reverse();
var PP_TOP_D  = (function(){                                       /* 抖音被引最集中的那条帖 */
  var dz = POSTS.filter(function(p2){ return p2.ch === PP_TOP; })
                 .sort(function(a2,b2){ return b2.indeg - a2.indeg; });
  return dz[0] || {indeg:0, r:'—', name:'—', ch:PP_TOP}; })();
var OUT_VOL_C = CHS.filter(function(c){ return CO[c]; })
                  .sort(function(a2,b2){ return CO[b2].out - CO[a2].out; })[0];
/* ★ 2026-10-14 第二十一轮：`OUT_VOL_C` 是**被引总量**最大的渠道，而 3.3 正文里有一句按字面说的是
   「发帖量最大的渠道」—— 两者在 EVT-001/002/003 上恰好同指一个渠道，换事件就可能分叉
   （补一个真正的「发帖量最大」变量，两处各读各的，不再互相借名）。 */
var POST_VOL_C = CHS.filter(function(c){ return CO[c]; })
                  .sort(function(a2,b2){ return CO[b2].posts - CO[a2].posts; })[0];
/* 关键时段窗口内、`PP_TOP` 自己的帖子共获得多少次引用（原先在正文里内联算了两遍）。 */
var WIND_TOP_IND = sum(WPOSTS.filter(function(p2){ return p2.ch === PP_TOP; }),
                       function(p2){ return p2.indeg; });
/* ★ 2026-10-14 第二十二轮：r34 与 r35 两处都无条件写「高传播力渠道并未在该时段内产生放大效应」。
   是否放大由数据决定 —— 判据取「窗口内该渠道被引占其全程被引之比」与「窗口时长占全程之比」：
   前者更高即说明它在这一段里比平时更活跃，是放大而不是收敛。 */
var AMP_SH   = (CO[PP_TOP] && CO[PP_TOP].out) ? WIND_TOP_IND / CO[PP_TOP].out : 0;
var AMP_FLAG = AMP_SH > W_HR_SH;
/* ★ 2026-10-14 第二十二轮：r30 的「综合判断与建议」原先把「每帖被引最高的渠道」与
   「窗口内承载传播的渠道」当作**两个**来看（"两个维度指向同一处缺口"）。EVT-002 里
   两者是同一个微博，印出来就成了「微博并未在关键时段内产生放大效应 —— 窗口内的传播
   由微博承载」，自相矛盾。这里先判一次两者是否同一，供 r30 分两支。 */
var PP_IS_WIN = (W_CC_RANK.length > 0 && W_CC_RANK[0] === PP_TOP);
/* ★ 2026-10-14 第二十一轮：建议 5 的抬头原先无条件写「内容少但转化强」——那是 EVT-001 的情形
   （冠渠道抖音只有 8 条帖）。换事件后每帖被引最高的渠道可能是发帖量最大的那个
   （EVT-002 是微博 20 条帖、4 个有产出渠道里的最多者），「内容少」当场为假。
   判据：该渠道发帖量是否低于「有内容产出渠道的平均发帖量」。 */
var PP_LEAN = (PP_POSTS.length > 0 && CO[PP_TOP] &&
               CO[PP_TOP].posts < META.posts / PP_POSTS.length);

/* ================= 3.1 关键影响节点 = 关键事件（★ 2026-10-12 第十二轮，按仿真侧新定义重做） =====
   定案原话：「我对关键节点有了新的定义」—— 关键影响节点**不是账号，是关键事件**：
   某个轮次里出现的、对整体事件后续展开起到关键影响的重要事件或情况，回答「**为什么那时候危险**」，
   为模块二情绪曲线上的拐点提供归因。与模块二的分工：模块二说「是什么」（第几轮负面跳到多少），
   本节说「为什么」（那一轮发生了什么）。
   ★ 五个识别维度全部由载荷现算，**不写死任何轮号**：
       ① 互动量突变 —— prompt 原文「某轮互动量远超**历史基线**，如超过均值 2 倍以上」。
          ★ 这里的「基线」取**该轮此前 3 轮**的互动量均值（前一版本误取**全程均值**）。
          取全程均值有两处不对：一是把事件**之后**的轮次也算进基线，等于拿未来数据判事件；
          二是被 t0 残缺轮（零互动）拉低，门槛反而更容易被越过。按全程均值（含 t0）复算，
          逐轮互动量 max 19 / 均值 11.81 / 2×均值 23.62，**无一轮越过**；换成动作总量
          （发帖+评论+转发+点赞）同样是 max 21 / 均值 14.81 / 2×均值 29.62、**零命中** ——
          原因是逐轮动作量被「名额区间 + 钟点活动增益」共同锁死（按钟点槽位 round % 6 分组，
          同槽位极差 ≤ 4，谷格轮恒为 5）。改成「相对该轮此前 3 轮基线」后，命中 2 个轮次
          （轮 2 为 7.00 倍、轮 27 为 2.00 倍），判据这才有意义。
          ★ 全程均值口径的实测零命中结果**一并保留在正文里披露** —— 它说明本跑的逐轮总量
          被机制锁在一个窄带内，是报告必须交代的事实，不因换了口径就抹掉。
          ★ 该轮前窗剔空（即轮 1）时不判此项。
       ② 情绪急跌 —— 该轮全场效价较上一轮下跌 ≥ 0.02。
          ★ 回升一律不计：每夜 02—05 点那格的规律性回升是钟点活动增益的机械结果
            （模块二已认定 corr(Ḡ, Δ效价) = −0.88），不是事件。
       ③ 跨渠道传导 —— 该轮使某个此前无内容产出的渠道**首次**出现，或该轮发生了
          「转载给原帖新增渠道」的跨渠道传播（载荷 cross_events）。
       ④ 角色行为突变 —— 某类角色当轮发帖 ≥ 3 条，且 ≥ 该角色全程均值的 3 倍。
       ⑤ 多点共振 —— 该轮有 ≥ 3 个渠道同时有新帖产出。
     ★ **关键影响节点 = 命中 ≥ 2 个维度**的轮次（门槛写死为 2，轮次本身现算）；
       ★ 2026-10-13 第十六轮：仿真侧要求「不要列出未命中关键节点标准的轮次」⇒
         单维轮次不再在正文里点名（EVT_MINOR 与其文案派生量已随之一并移除）。
       ★ **轮 0 一律不参与识别** —— 它是 t0 注入轮，两条原生帖会让 ③ 误判成「渠道首次出现」，
         而 t0 注入不是事件。
     ★ 影响倍数 = 事件后 3 轮互动量均值 ÷ 事件前 3 轮均值。
       **前窗剔轮 0**：轮 0 是 t0 残缺轮（当日只覆盖 2 小时、零互动），计入会把首个事件的
       倍数额外放大到 14.33 倍，剔掉后为 7.17 倍 —— 后者才是可比读数。
       首个事件的前窗剔完为空，倍数记 null，渲染为「—」，不以 0 或 ∞ 冒充。 */
/* ★ 2026-10-13 第十六轮：此处原先是为 3.1 正文里「维度①互动量基线」那两段服务的成套
   派生量 —— 动作总量、钟点槽位中位数、按槽位偏离、逐轮互动量的最大值与均值。
   仿真侧本轮要求正文「不要对基线计算方法等细节展开讨论」，那两段删除后这九项在本文件里
   **失去全部消费方**（RDX 的 `act:` / `dev:` 两个字段自建立起就无人读取，本轮一并摘掉）。
   维度①的判据本体不受影响 —— 它现在是 RDX 的 `ratio`（该轮互动量 ÷ 此前 3 轮均值），照旧现算。
   ★ 判据改动的原因与被否掉的「全程均值」口径的实测零命中读数，仍完整留在本节块头的长注释里。 */

var EVT_AG = {}; ND.forEach(function(z){ EVT_AG[z.agent_id] = z; });
var CH_FIRST = {};                                /* 渠道 -> 首个有内容产出的轮次 */
POSTS.forEach(function(p){ if(!(p.ch in CH_FIRST) || p.r < CH_FIRST[p.ch]) CH_FIRST[p.ch] = p.r; });
var CH_JOIN = Object.keys(CH_FIRST).filter(function(c){ return CH_FIRST[c] > 0; });
  /* ★ 2026-10-20 第三十轮：t0 之后才第一次有内容的渠道（= 全程新平台加入次数，
     与 EV_LAUNCH 不是一回事：后者只数**关键事件**轮）。 */
var ROLE_RTOT = {};                               /* 角色 -> 全程发帖数 */
POSTS.forEach(function(p){ var a = EVT_AG[p.agent], k = a ? a.role : '其他';
  ROLE_RTOT[k] = (ROLE_RTOT[k] || 0) + 1; });

var RDX = S.map(function(x, _idx){
  /* ⚠ `i` 是**行号**（S 的下标），`r` 是**轮号** —— 两者差不一定是 0（见顶部 R0）。
     下面凡是「在 S 里取前后几行」的用 i，凡是「拿轮号去比 posts / _BY_R / CH_FIRST」的用 r。 */
  var i = _idx, r = x.round, ps = POSTS.filter(function(p){ return p.r === r; });
  var chs = {}, ro = {};
  ps.forEach(function(p){
    chs[p.ch] = (chs[p.ch] || 0) + 1;
    var a = EVT_AG[p.agent], k = a ? a.role : '其他'; ro[k] = (ro[k] || 0) + 1; });
  var hot = Object.keys(ro).filter(function(k){
    return ro[k] >= 3 && ro[k] >= 3 * (ROLE_RTOT[k] / S.length); });
  /* 前窗起点：老跑要跳过**第 0 行**（那是 t0 残缺轮）；新跑 series 从轮 1 起，
     第 0 行就是首轮、是完整的一轮，再跳就白丢一轮。故起点 = R0 === 0 ? 1 : 0。 */
  var pre = S.slice(Math.max(R0 === 0 ? 1 : 0, i - 3), i), post = S.slice(i + 1, i + 4);
  return {i:i, round:r, n:ps.length, ps:ps, chs:chs, ro:ro, hot:hot,
    nCh:Object.keys(chs).length, ce:CE_BY_R[r] || [],
    newCh:Object.keys(chs).filter(function(c){ return CH_FIRST[c] === r; }),
    dval:i ? +(x.valence - S[i-1].valence).toFixed(4) : null,
    flow:x.flow,
    /* ① 的判据：该轮互动量 ÷ **该轮此前 3 轮**（剔轮 0）互动量均值。前窗为空则不判。 */
    ratio: (pre.length && sum(pre, function(y){ return y.flow; }) > 0)
             ? x.flow / (sum(pre, function(y){ return y.flow; }) / pre.length) : null,
    preF:  pre.length  ? sum(pre,  function(y){ return y.flow; }) / pre.length  : null,
    postF: post.length ? sum(post, function(y){ return y.flow; }) / post.length : null,
    dneg: (pre.length && post.length)
      ? (sum(post, function(y){ return y.neg; }) / post.length
       - sum(pre,  function(y){ return y.neg; }) / pre.length) : null};
});
RDX.forEach(function(d){
  var m = [];
  if(d.ratio !== null && d.ratio >= 2) m.push(1); /* ① 该轮 ÷ 此前 3 轮基线 ≥ 2 倍 */
  if(d.dval !== null && d.dval <= -0.02) m.push(2);
  if(d.ce.length || d.newCh.length) m.push(3);
  if(d.hot.length) m.push(4);
  if(d.nCh >= 3) m.push(5);
  d.dims = m;
  d.mult = (d.preF && d.preF > 0) ? d.postF / d.preF : null;
});

var DIM_LAB = {1:'互动量突变', 2:'情绪拐点归因', 3:'跨渠道传导', 4:'角色行为突变', 5:'多点共振'};
var TIER_CLR = {'一级催化剂':'#a82a06', '二级放大器':'#c25a16', '前哨信号':'#6a7b8f'};

var EVENTS   = RDX.filter(function(d){ return d.i > 0 && d.dims.length >= 2; });
EVENTS.forEach(function(d){
  d.tier = (d.mult !== null && d.mult >= 2) ? '一级催化剂'
         : (d.mult === null || d.mult < 1.25) ? '前哨信号' : '二级放大器';
});
var EVT_RANK = EVENTS.slice().sort(function(a, b){ return (b.mult || 0) - (a.mult || 0); });
var EV_TIER1 = EVENTS.filter(function(d){ return d.tier === '一级催化剂'; });
var EV_TIER2 = EVENTS.filter(function(d){ return d.tier === '二级放大器'; });
var EV_PRE   = EVENTS.filter(function(d){ return d.tier === '前哨信号'; });
var EV_FIRST = EVENTS[0], EV_TOP = EVT_RANK[0];
var EV_CROSS = EVENTS.filter(function(d){ return d.dims.indexOf(3) >= 0; });
/* 有**转载**的关键事件：句子的主语是转载，参照系跟主语走（跨渠道传导维度③还含
   「新平台首现」，而那是引用干的、不产生跨渠道事件 ⇒ 两者不能混用同一列表）。 */
var EV_CE = EV_CROSS.filter(function(d){ return d.ce.length; });
var EV_LAUNCH = EVENTS.filter(function(d){ return d.newCh.length; });
var ROOTS = POSTS.filter(function(p){ return !p.q; });
/* ★ 2026-10-14 第二十二轮：r31 原先写死「轮 0 的 N 条原生帖」——五跑的根帖确实都在轮 0
   （引擎把 t0 注入帖记为根帖），但写字面量等于把引擎的实现细节当成了报告的事实来源。
   改为取根帖实际所在的最小轮号。 */
var ROOT_R = ROOTS.length ? Math.min.apply(null, ROOTS.map(function(p){ return p.r; })) : 0;

function evtWhy(d){
  var s = [];
  d.dims.forEach(function(k){
    if(k === 1) s.push('互动量突变（该轮 ' + d.flow + ' 次，为此前 3 轮基线的 ' + d.ratio.toFixed(2) + ' 倍）');
    if(k === 3) s.push('跨渠道传导' + (d.newCh.length
      ? '（' + d.newCh.join('、') + '首次有内容产出）' : '（' + d.ce.length + ' 起转载送往新渠道）'));
    if(k === 4) s.push('角色行为突变（' + d.hot.map(function(r){ return r + ' ' + d.ro[r] + ' 条'; }).join('、') + '）');
    if(k === 5) s.push('多点共振（' + d.nCh + ' 个渠道同轮有新帖）');
    if(k === 2) s.push('情绪急跌（单轮 ' + String(d.dval.toFixed(4)).replace('-', '−') + '）');
  });
  return s.join('；');
}
function evtWhat(d){
  var ro = Object.keys(d.ro).sort(function(a, b){ return d.ro[b] - d.ro[a]; })
    .map(function(k){ return k + ' ' + d.ro[k] + ' 条'; }).join('、');
  var s = d.n + ' 条新帖，由' + ro + '发出，落在' + Object.keys(d.chs).join('、') + '上';
  if(d.ce.length) s += '；' + d.ce.length + ' 起转载把内容送往新渠道';
  if(d.newCh.length) s += '；' + d.newCh.join('、') + '自本轮起有内容产出';
  return s;
}
function evtMultTxt(d){ return d.mult === null ? '—' : d.mult.toFixed(2) + ' 倍'; }
function evtHtml(d){
  return '<b>轮 ' + d.round + '　' + S[d.i].window[0] + '</b>' +
    '<span class="evttier" style="color:' + TIER_CLR[d.tier] + '">' + d.tier + '</span>' +
    '<span class="pivdv">命中 ' + d.dims.length + ' 个维度　影响倍数 ' +
      (d.mult === null ? '—（前窗剔 t0 后为空）' : d.mult.toFixed(2) + ' 倍') + '</span>' +
    '<div class="pivrow"><span class="pivk">事件</span>' + evtWhat(d) + '</div>' +
    '<div class="pivrow"><span class="pivk">触发</span>' + evtWhy(d) + '</div>' +
    '<div class="pivrow"><span class="pivk">后果</span>互动量前三轮均值 ' +
      (d.preF === null ? '—' : d.preF.toFixed(1)) + ' → 后三轮均值 ' +
      (d.postF === null ? '—' : d.postF.toFixed(1)) +
      (d.dneg === null ? '' : '；负面占比' + (d.dneg >= 0 ? '上升 ' : '下降 ') +
        Math.abs(d.dneg * 100).toFixed(1) + ' 个百分点') +
      '；累计已触达渠道 ' + REACH_N[d.i] + ' / ' + CHS.length + '（' + REACH_SET[d.i].join('、') + '）</div>';
}
window.showEvt = function(i){
  var d = null; EVENTS.forEach(function(e){ if(e.i === i){ d = e; } });
  if(!d){ return; }
  setHTML('evtDetail', evtHtml(d));
};

SEC(1908, function(){                                 /* #r31 正文（★ 2026-10-13 第十六轮：仿真侧要求精简） */
  setHTML('evtN', EVENTS.length);
  setHTML('r31',
    p('本节为模块二图 2-1 的情绪拐点归因，回答「为什么那时候危险」。五个维度：'+
      [1, 2, 3, 4, 5].map(function(k){ return DIM_LAB[k]; }).join('、') +
      '；命中两个及以上才记为关键影响节点，全程共 ' + EVENTS.length + ' 个。') +
    p(EVENTS.map(function(d){
      return '轮 ' + d.round + '（' + S[d.i].window[0].slice(5) + '）' +
        Object.keys(d.ro).sort(function(a, b){ return d.ro[b] - d.ro[a]; })
          .map(function(k){ return k + ' ' + d.ro[k] + ' 条'; }).join('、') +
        '落在' + Object.keys(d.chs).join('、') +
        (d.ce.length ? '，' + d.ce.length + ' 起转载送往新渠道' : '') +
        (d.newCh.length ? '，' + d.newCh.join('、') + '自本轮起有内容产出' : '') +
        '；命中' + d.dims.map(function(k){ return DIM_LAB[k]; }).join('、') +
        '；此后三轮互动量 ' + (d.preF === null ? '—' : d.preF.toFixed(1)) + ' → ' +
        (d.postF === null ? '—' : d.postF.toFixed(1)) + ' 次。';
    }).join('<br>')) +
    p((EVENTS.length ? '因果上，轮 ' + ROOT_R + ' 的 ' + ROOTS.length + ' 条原生帖是全池唯一起点，此后 ' +
      (META.posts - ROOTS.length) + ' 条帖全部是引用帖。轮 ' + EV_FIRST.round +
      ' 的引用帖把内容带到' + Object.keys(EV_FIRST.chs).join('、') +
      /* ★ 2026-10-20 第三十轮：EV_LAUNCH 只数**关键事件**里的新平台，原句却写成
         「全程唯一一次新平台加入」——EVT-001 的关键事件只有轮 1 开了新平台，
         但轮 30 还有一次（黑猫投诉，不构成关键事件），句子当场与表 1-3 打架。
         改为把「关键事件里的次数」与「全程次数」分开写。 */
      (EV_LAUNCH.length === 0
        ? '，全程没有关键影响节点同时开启新平台'+
          (CH_JOIN.length ? '（全程新平台加入共 '+CH_JOIN.length+' 次，均不在关键事件轮）' : '')
       : EV_LAUNCH.length === 1
         ? '，是关键事件里唯一一次开启新平台'+
           (CH_JOIN.length > 1 ? '（全程新平台加入共 '+CH_JOIN.length+' 次）' : '')
         : '，是全程 ' + EV_LAUNCH.length + ' 次新平台加入之一') +
      /* ★ 2026-10-14 第二十一轮：原先「上媒体、KOL、客户在微博接棒」与「是全程最深的一次
         单轮下跌」两句都是写死的 EVT-001 事实 —— 换事件后角色、渠道、跌幅名次全不一样。
         现改为：参与角色取该轮实际构成（按条数降序）、渠道取该轮实际落点、
         跌幅名次与 `PIV_DROP`（模块二自己判定的最深单轮跌幅）对齐后才下这个结论。 */
      '；轮 ' + EV_TOP.round + ' 上' +
      Object.keys(EV_TOP.ro).sort(function(a, b){ return EV_TOP.ro[b] - EV_TOP.ro[a]; }).join('、') +
      '在' + Object.keys(EV_TOP.chs).join('、') + '接棒，量级与情绪同时跳变，' +
      (EV_TOP.round === PIV_DROP.round
        ? '是全程最深的一次单轮下跌'
        : '但不是全程最深的一次单轮下跌（本轮 ' + f3(Math.abs(DVAL[EV_TOP.i])) + '，最深的一次在轮 ' +
          PIV_DROP.round + '，' + f3(Math.abs(PIV_DROP.dv)) + '）') +
      /* ★ 2026-10-15 第二十六轮：零转载时原先印「此后 0 起转载未再引入新平台」——
         句子成立但空洞，且与同段前文刚说的「本跑没有转载」重复。 */
      '；' + (EV_CE.length
        ? '此后 ' + EV_CE.length + ' 起转载未再引入新平台。'
        : (NO_CE ? '本跑全程没有转载，新平台全部由引用落成。' : '此后再无转载引入新平台。')) +
      '按强度分档：' +
      (EV_TIER1.length
        ? EV_TIER1.map(function(d){ return '轮 ' + d.round; }).join('、') +
          ' 是<b>一级催化剂</b>（' + EV_TIER1[0].mult.toFixed(2) + ' 倍）；'
        : '') +
      (EV_TIER2.length
        ? EV_TIER2.map(function(d){ return '轮 ' + d.round; }).join('、') +
          ' 是<b>二级放大器</b>，维持着热度；'
        : '') +
      (EV_PRE.length
        ? EV_PRE.map(function(d){ return '轮 ' + d.round; }).join('、') +
          ' 是<b>前哨信号</b>，量没被推高。'
        : '')
     /* ★ 2026-10-20：EVENTS 为空时（EVT-002 实测 0 个关键影响节点）上面整句里的
        EV_FIRST / EV_TOP 都是 undefined，`EV_FIRST.round` 当场抛异常 ⇒ 整块 #r31
        从未写入。零节点要另写一句，不能拿有节点的句式硬套。 */
     : ('因果上，轮 ' + ROOT_R + ' 的 ' + ROOTS.length + ' 条原生帖是全池唯一起点，此后 ' +
        (META.posts - ROOTS.length) + ' 条帖全部是引用帖。' +
        '本跑没有任何一轮同时命中两个及以上维度，因此不设关键影响节点；' +
        (NO_XC
          ? '全程没有跨渠道传播，内容全部在原发平台内流转。'
          : (NO_CE
              ? '全程没有转载（新增渠道边 0 条），跨平台流动由 ' + QX.length + ' 条跨渠道引用完成。'
              : '跨渠道转载与引用都发生过，只是没有一轮同时满足两个维度。'))))) +
    llmBlock('L2', '事件链解读'));
});

/* 提示文字**无条件**写入：若放进「有实例才写」的分支，冒烟桩下 `#evtDetail` 会恒为空
   而被空壳扫描判红。★ 第十四轮删掉图 3-1 / 图 3-2 之后，这是 `#evtDetail` 唯一的初始内容。 */
setHTML('evtDetail', '<span class="pivhint">点击上表任一事件名，此处展开该事件的触发指标与后果。'+
  '本跑识别出 ' + EVENTS.length + ' 个关键影响节点' +
  (EVENTS.length ? '（' + EVENTS.map(function(d){ return '轮 ' + d.round; }).join('、') + '）' : '') +
  '。</span>');

/* ---- 表 3-1 关键事件清单（按时间顺序） ---- */
SEC(1953, function(){
  var h = '<thead><tr><th class="num">序</th><th class="num">轮次</th><th>时点</th><th>关键事件</th>'+
    '<th>命中维度</th><th class="num">影响倍数</th><th class="num">前 3 轮互动</th>'+
    '<th class="num">后 3 轮互动</th><th class="num">负面占比变化</th><th>强度档</th></tr></thead><tbody>';
  EVENTS.forEach(function(d, i){
    h += '<tr><td class="num">' + (i + 1) + '</td><td class="num"><b>' + d.round + '</b></td>' +
      '<td class="mono">' + S[d.i].window[0] + '</td>' +
      '<td><span class="xev" onclick="showEvt(' + d.i + ')">' + evtWhat(d) + ' ▸</span></td>' +
      '<td>' + d.dims.map(function(k){ return DIM_LAB[k]; }).join('、') + '</td>' +
      '<td class="num">' + evtMultTxt(d) + '</td>' +
      '<td class="num">' + (d.preF === null ? '—' : d.preF.toFixed(1)) + '</td>' +
      '<td class="num">' + (d.postF === null ? '—' : d.postF.toFixed(1)) + '</td>' +
      '<td class="num">' + (d.dneg === null ? '—'
        : (d.dneg >= 0 ? '+' : '−') + Math.abs(d.dneg * 100).toFixed(1) + ' pp') + '</td>' +
      '<td><b style="color:' + TIER_CLR[d.tier] + '">' + d.tier + '</b></td></tr>';
  });
  setHTML('tbEvt', h + '</tbody>');
});


/* ---- 模块总结（结论先行，约 450 字） ---- */
SEC(1974, function(){
  setHTML('r30',
    /* ★ 2026-10-12 第十二轮：节序改为「3.1 关键影响节点（事件）→
       3.2 关键时段 → 3.3 渠道影响力」，小结须与节序同向。 */
    p('关键影响节点。全程识别出 '+EVENTS.length+' 个关键事件'+
      (EVENTS.length ? '（'+EVENTS.map(function(d){ return '轮 '+d.round; }).join('、')+'）' : '')+
      '；'+
      (EV_TIER1.length
        /* ★ 2026-10-14 第二十二轮：原写死「把互动量抬升**一个数量级**的只有 X（N 倍）」——
           一个数量级是 10 倍，而五跑实测最高只有 7.17 倍（EVT-001），EVT-003 更是 2.56 倍，
           「数量级」当场为假。改为只陈述「抬升幅度最大的是谁、多少倍」。 */
        ? '其中把互动量抬升幅度最大的是'+EV_TIER1.map(function(d){ return '轮 '+d.round; }).join('、')+
          '（'+EV_TIER1[0].mult.toFixed(2)+' 倍），其余是维持热度的放大环节或改变参与结构的前置铺垫。'
        : (EVENTS.length
            ? (EVENTS.some(function(d){ return d.mult !== null && d.mult >= 1.25; })
                ? '本跑没有出现影响倍数达到 2 倍的一级催化剂，最强的也只是二级放大器。'
                /* ★ 2026-10-20 第三十一轮：全部关键事件都是前哨信号（倍数缺失或 <1.25）时，
                   原句仍写「最强的也只是二级放大器」—— EVT-001 的关键事件表里倍数印的是
                   「—」，小结与表格当场矛盾。 */
                : '本跑没有出现影响倍数达到 2 倍的一级催化剂，关键事件均属前哨信号（影响倍数低于 1.25 或前窗为空）。')
            : '本跑没有任何一轮同时命中两个及以上识别维度，不设关键事件。'))+
      /* ★ 2026-10-14 第二十二轮：原先以 `EV_LAUNCH.length` 为真条件就写「全程唯一一个」——
         有 2 个及以上时该句为假。改为按个数分三支。 */
      (EV_LAUNCH.length === 0
        ? '全程没有关键事件同时开启新平台。'
        : (EV_LAUNCH.length === 1
            ? '全程唯一一个既构成关键事件、又让某个平台第一次有内容产出的轮次是轮 '+EV_LAUNCH[0].round+
              '（'+EV_LAUNCH[0].newCh.join('、')+'）。'
            : '全程共 '+EV_LAUNCH.length+' 个关键事件同时让平台第一次有内容产出，分别是轮 '+
              EV_LAUNCH.map(function(d){ return d.round+'（'+d.newCh.join('、')+'）'; }).join('、')+'。'))) +
    p('关键时段。以连续 4 轮（'+WIN_HOURS+' 小时）为窗口逐段比较动作总量（发帖＋评论＋转发＋点赞），'+
      '最大的一段是轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+'（'+S[WIN.lo].window[0]+' 至 '+S[WIN.hi].window[1]+
      '），窗口内发生 <b>'+n0(WIN_ACT)+'</b> 次动作，占全程 '+n0(TOTAL_ACT)+' 次的 '+pct(W_ACTSH)+
      '，而时长只占推演区间的 '+pct(W_HR_SH)+'，密度为全程平均的 <b>'+W_DENS.toFixed(2)+
      ' 倍</b>。'+
      /* ★ 2026-10-14 第二十二轮：「该时段同时是负面占比最高的一段」原为无条件断言 ——
         窗口由**动作总量** argmax 选出，与情绪峰值是否同段并无因果关系；「全程唯一一次全场
         智能体均处于负面」也只在峰值恰为 100% 时成立。EVT-002/003/004 的负面峰值
         （轮 21 / 36 / 18）都不在本窗口内。 */
      (WIN_NEG_MAX >= PEAK_NEG.neg - 1e-9 ? '该时段同时是负面占比最高的一段：'
                                          : '该时段的负面占比并不是全程最高，四轮依次为 ')+
      WSEG.map(function(x){ return pct(x.neg,0); }).join('、')+'，其中轮 '+PEAK_NEG.round+
      ' 的 '+pct(PEAK_NEG.neg)+
      (PEAK_NEG.neg >= 0.9999
        ? (NEG_FULL_N === 1 ? ' 是全程唯一一次全场智能体均处于负面。'
                            : ' 是全程 '+CN_NUM(NEG_FULL_N)+' 次全场智能体均处于负面之一（首次在轮 '+
                              NEG_FULL[0].round+'）。')
        : (WIN_NEG_MAX >= PEAK_NEG.neg - 1e-9
            ? ' 是全程最高，但场内仍有 '+pct(1 - PEAK_NEG.neg)+' 的智能体未转为负面。'
            : ' 是全程峰值，落在本窗口之外。'))) +
    p('渠道影响力。按每帖平均被引次数排序，'+PP_TOP+'最高（<b>'+CO[PP_TOP].out_per_post.toFixed(3)+
      ' 次/帖</b>，'+CO[PP_TOP].posts+' 条帖获得 '+CO[PP_TOP].out+' 次引用），其后依次为'+
      PP_POSTS.slice(1).map(function(c){
        return c+'（'+CO[c].out_per_post.toFixed(3)+'，'+CO[c].posts+' 条帖）'; }).join('、')+
      '；'+(CH_ZERO_JS.length ? CH_ZERO_STR+'全程无发帖，不存在被引。' : '')+
      PP_TOP+'的领先高度集中：其 '+CO[PP_TOP].out+
      ' 次被引中有 '+PP_TOP_D.indeg+' 次落在同一条帖上（轮 '+PP_TOP_D.r+'，'+PP_TOP_D.name+
      '）。按被引总量，'+OUT_VOL_C+'以 '+CO[OUT_VOL_C].out+' 次居首，占全程 '+META.edges+
      ' 次引用的 '+pct(CO[OUT_VOL_C].out/META.edges)+'。') +
    p('综合判断与建议。'+
      /* ★ 2026-10-14 第二十二轮：原先无条件写「两个维度指向同一处缺口」并把 PP_TOP 与
         窗口承载渠道当成两个 —— 两者为同一个渠道时（EVT-002 的微博）会印出自相矛盾的
         「微博并未在关键时段内产生放大效应 —— 窗口内的传播由微博承载」。改为先判同异。 */
      (PP_IS_WIN
        ? '两个维度指向同一个渠道：每帖被引最高的'+PP_TOP+'同时也是关键时段内发帖最多的渠道 —— '+
          '该时段 '+WPOSTS.length+' 条新帖中它占 '+(W_CC[PP_TOP]||0)+' 条、被引 '+
          sum(WPOSTS.filter(function(p2){ return p2.ch === PP_TOP; }), function(p2){ return p2.indeg; })+
          ' 次，'+(AMP_FLAG
            ? '这一段里它比平时更活跃，放大效应与发出量同时集中在它身上。'
            : '量的输出集中在它身上，但这一段里的引用没有同步跟上，属于「出声多、被转少」的一段；'+
              '风险方向因此从「谁在扩散」转成「扩散的接力棒这一段没人接」。')+
          '风险在时间上集中在 '+S[WIN.lo].window[0].slice(0,10)+' 的下午至夜间，渠道侧与时间侧指向同一个主战场'+
          PP_TOP+'。建议把最高等级的值班与响应资源压在轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+'，并对'+PP_TOP+
          '按每帖被引而非发帖量配置监测资源'
        : '两个维度指向同一处缺口：每帖被引最高的'+PP_TOP+
          (AMP_FLAG ? '在关键时段内同样产生了放大效应 —— ' : '并未在关键时段内产生放大效应 —— ')+
          '该时段 '+WPOSTS.length+' 条新帖中它只有 '+(W_CC[PP_TOP]||0)+' 条、被引 '+
          sum(WPOSTS.filter(function(p2){ return p2.ch === PP_TOP; }), function(p2){ return p2.indeg; })+
          ' 次；'+(W_TOP_IND_WIN > 0
            ? '窗口内的传播由'+W_POST_TOP+'承载（'+W_CC[W_POST_TOP]+' 条帖、'+W_TOP_IND_WIN+' 次引用）。'
            : '窗口内新帖分散在'+W_CH_SPREAD_TXT+'，没有任何一条在窗口内被引用 —— 这一段只有内容供给，没有扩散接力。')+
          '风险在时间上集中在 '+S[WIN.lo].window[0].slice(0,10)+' 的下午至夜间，'+
          (W_TOP_IND_WIN > 0
            ? '在渠道上仍以'+W_POST_TOP+'为主战场。'
            : (W_POST_TIED.length > 1
                ? '渠道上也没有单一主战场 —— 窗口内发帖最多的'+W_POST_TIED.join('、')+'各 '+
                  (W_CC[W_POST_TOP]||0)+' 条，量都很小。'
                : '渠道上仍以'+W_POST_TOP+'为主战场（窗口内发帖最多，'+(W_CC[W_POST_TOP]||0)+' 条）。'))+
          '建议把最高等级的值班与响应资源压在轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+
          '；渠道侧对'+PP_TOP+'按每帖被引而非发帖量配置监测资源'+
          (W_TOP_IND_WIN > 0 ? '，但主线处置仍放在'+W_POST_TOP : ''))+
      /* ★ 2026-10-14 第二十二轮：`PP_POSTS[1]` 在只有一个渠道发帖时为 undefined。 */
      /* ★ 第二十八轮：原句无条件断言「两处样本限制」——EVT-008 的两处基数分别是 198 条与 48 条帖，
         把 198 条帖说成样本限制并不成立。改为按最小基数是否真的偏小分两支。 */
      (PP_POSTS.length > 1
        ? (function(){
            /* ★ 第二十八轮：原先按「发帖基数最小的渠道」报样本限制 —— EVT-002 会点到小红书
               （3 条帖），而本段讲的是 PP_TOP 与关键时段主战场这两个渠道，小红书的样本量
               与这里的资源分配无关。改为只看**本段真正在用的那两个渠道**。 */
            var pair = [PP_TOP];
            if(W_CC_RANK[0] !== PP_TOP) pair.push(W_CC_RANK[0]);
            var th = pair.slice().sort(function(a, b){
              return (CO[a].posts - CO[b].posts) || (CO[a].posters - CO[b].posters); })[0];
            return ((CO[th].posts < 30 || CO[th].posters < 8)
              ? '。需说明样本限制：'+th+'全程只有 '+CO[th].posts+' 条帖（'+CO[th].posters+
                ' 个发帖账号），以此为唯一依据分配资源并不稳健。'
              : '。基数（'+pair.map(function(c){ return c+' '+CO[c].posts+' 条帖'; }).join('、')+
                '）足以支撑排序，但仍应逐轮复核，不宜按一次推演定终身。');
          })()
        : '。需说明样本限制：本场只有 '+PP_TOP+'一个渠道有内容产出（'+CO[PP_TOP].posts+
          ' 条帖），单一读数的稳健性有限。')));
});

/* ---- 3.2 关键时段 ---- */
SEC(2017, function(){
  setHTML('r33',
    p('按连续 4 轮（'+WIN_HOURS+' 小时）滑动窗口逐段比较，动作总量最大的窗口是轮 '+(WIN.lo+R0)+
      ' 至轮 '+(WIN.hi+R0)+'（'+S[WIN.lo].window[0]+' 至 '+S[WIN.hi].window[1]+'）。窗口内动作合计 <b>'+
      n0(WIN_ACT)+' 次</b> —— 发帖 '+sum(WSEG,function(x){return x.post;})+'、评论 '+
      sum(WSEG,function(x){return x.comment;})+'、转发 '+sum(WSEG,function(x){return x.repost;})+
      '、点赞 '+sum(WSEG,function(x){return x.like;})+' —— 占全程 '+n0(TOTAL_ACT)+' 次动作的 '+
      pct(W_ACTSH)+'，而时长只占推演区间的 '+pct(W_HR_SH)+'，密度为全程平均的 <b>'+
      W_DENS.toFixed(2)+' 倍</b>。四轮的动作量依次为 '+
      WSEG.map(function(x){ return x.post+x.comment+x.repost+x.like; }).join('、')+
      ' 次，'+
      /* ★ 2026-10-14 第二十二轮：「没有哪一轮单独冲高」原为无条件断言 —— 若窗口内确有一轮
         远高于其余三轮，句子反过来。改为按窗口内最高轮与其余轮的落差现判。 */
      (function(){
        var vs = WSEG.map(function(x){ return x.post+x.comment+x.repost+x.like; });
        var mx = Math.max.apply(null, vs), sm = vs.indexOf(mx);
        var rest = vs.filter(function(v, i){ return i !== sm; });
        var mxr = Math.max.apply(null, rest);
        return mx >= 1.5 * mxr
          ? '其中轮 ' + WSEG[sm].round + ' 一轮就占到 ' + mx + ' 次，热度集中在单轮冲高。'
          : '没有哪一轮单独冲高，热度是靠连续供给维持的。';
      })()+
      '作为对照，第二高的窗口是轮 '+
      (W_SEC.lo+R0)+' 至轮 '+(W_SEC.lo+WIN_N-1+R0)+'（'+W_SEC.s+' 次），与首位相差 '+(WIN_ACT-W_SEC.s)+' 次。') +
    /* ★ 2026-10-20 第二十九轮：原句无条件写「驱动来自新帖的连续供给」。
       EVT-002 本窗口只有 1 条新帖（其余 36 次评论），「连续供给」为假 ⇒ 按新帖数分档。 */
    p((function(){
        var tot = WSEG.reduce(function(a,x){ return a + x.post; }, 0);
        if(tot === 0) return '窗口内没有任何新帖，动作全部来自存量帖的互动';
        if(tot === 1) return '窗口内只有 1 条新帖，由'+Object.keys(W_RC)[0]+'发出，落在'+W_CC_RANK[0];
        return '窗口内热度的驱动来自新帖的连续供给：'+WPOSTS.length+' 条新帖由'+
          Object.keys(W_RC).sort(function(a2,b2){ return W_RC[b2]-W_RC[a2]; })
            .map(function(r2){ return r2+' '+W_RC[r2]+' 条'; }).join('、')+
          '构成，落在'+W_CC_RANK.map(function(c){ return c+' '+W_CC[c]+' 条'; }).join('、');
      })()+
      '；逐轮发帖量为 '+WSEG.map(function(x){ return x.post; }).join('、')+
      ' 条，发帖量峰值（轮 '+PEAK_POST.round+'，'+PEAK_POST.post+' 帖）'+
      /* ★ 2026-10-14 第二十二轮：原写死「正是窗口的第一轮」—— 滚动窗口由动作总量选出，
         与发帖量峰值日并无绑定关系（五跑里只有 EVT-001 恰好同轮）。 */
      (PEAK_POST.round === WIN.lo+R0 ? '正是窗口的第一轮。'
        : (PEAK_POST.round >= WIN.lo+R0 && PEAK_POST.round <= WIN.hi+R0
            ? '落在本窗口的第 '+(PEAK_POST.round - WIN.lo + 1)+' 轮。'
            : '落在本窗口之外。'))+
      /* ★ 2026-10-14 第二十二轮：「首轮集中投放铺开议题，随后三轮由评论维持热度」原为写死的
         形状描述，改为比较首轮与其余轮的**发帖量**（那才是「集中投放」的判据）。 */
      /* ★ 2026-10-14 第二十二轮：原先只有「首轮最密」与「四轮相近」两支，EVT-002 的窗口发帖是
         1、0、1、4（峰值在窗口第 4 轮）、EVT-004 是 4、0、2、5，两头都不是「首轮最密」，
         却被一律印成「四轮发帖量相近」。改按极差分三支：极差≤1 才算相近。 */
      (function(){
        var ps = WSEG.map(function(x){ return x.post; });
        var tot = ps.reduce(function(a,b){ return a + b; }, 0);
        var mx = Math.max.apply(null, ps), mn = Math.min.apply(null, ps), mi = ps.indexOf(mx);
        /* ★ 2026-10-20 第二十九轮：全窗口 0 或 1 条新帖时，「四轮相近、议题逐轮铺开」不成立。 */
        if(tot === 0) return '四轮都没有新帖，热度全靠评论与点赞维持';
        if(tot === 1) return '它出现在窗口第'+ORDINO(mi + 1)+'轮，议题不是靠连续发帖铺开的';
        if(mx - mn <= 1) return '四轮发帖量相近，议题是逐轮铺开的';
        if(mi === 0) return '首轮发帖最密、铺开议题，随后'+CN_NUM(ps.length - 1)+'轮由评论维持热度';
        return '发帖集中在窗口第'+ORDINO(mi + 1)+'轮（'+mx+' 帖），其余轮次由评论维持热度';
      })()+
      ' —— 整个窗口'+
      (sum(WSEG,function(x){return x.repost;}) === 0 ? '没有发生任何转发'
        : '只有 '+sum(WSEG,function(x){return x.repost;})+' 次转发')+
      '，'+
      (sum(WSEG,function(x){return x.repost;}) === 0
        ? '传播形态是讨论累积而不是跨平台搬运。'
        : '传播以平台内讨论累积为主。')) +
    /* ★ 2026-10-14 第二十二轮：本段原先三处写死 ——
       ①「负面占比的峰值也落在同一段」；②「是全程唯一一次全场智能体均处于负面」；
       ③「其中 N 轮落在本窗口内，是这几个峰值轮中最密集的一段」（EVT-005 的 N 为 0，
          印出「其中 0 轮落在本窗口内，是…最密集的一段」）。 */
    p((WIN_NEG_MAX >= PEAK_NEG.neg - 1e-9 ? '负面占比的峰值也落在同一段。'
                                          : '负面占比的峰值并不落在同一段，四轮依次为 ')+
      WSEG.map(function(x){ return pct(x.neg); }).join('、')+'，峰值 <b>'+pct(PEAK_NEG.neg)+
      '</b> 出现在轮 '+PEAK_NEG.round+'（'+PEAK_NEG.window[0]+'），'+
      (PEAK_NEG.neg >= 0.9999
        ? (NEG_FULL_N === 1 ? '是全程唯一一次全场智能体均处于负面。'
                            : '是全程 '+CN_NUM(NEG_FULL_N)+' 次全场智能体均处于负面之一（首次在轮 '+
                              NEG_FULL[0].round+'）。')
        : (WIN_NEG_MAX >= PEAK_NEG.neg - 1e-9
            ? '是全程最高的一次，但场内仍有 '+pct(1 - PEAK_NEG.neg)+' 的智能体未转为负面。'
            : '是本窗口之外的全程峰值。'))+
      '把两个峰值对齐可以看出错位：单轮互动量峰值在轮 '+PEAK_INT.round+'（'+PEAK_INT.window[0]+
      '，'+PEAK_INT.flow+' 次'+PEAK_INT_TIE+'），负面占比峰值'+(PEAK_NEG.round >= PEAK_INT.round ? '晚 ' : '早 ')+
      /* ★ 2026-10-14 第二十一轮：原先差值直接当「晚 N 轮」印，负面峰早于互动峰时会印出「晚 −9 轮、约 −36 小时」。 */
      Math.abs(PEAK_NEG.round-PEAK_INT.round)+' 轮、约 '+
      n0(Math.abs(PEAK_NEG.round-PEAK_INT.round)*spanOf(S[0]))+' 小时。单轮尺度的热度其实是平的：'+
      '单轮动作总量的最高值 '+ACT_MAX+' 次在全程出现 '+ACT_MAXR.length+' 轮（'+
      ACT_MAXR.map(function(x){ return '轮 '+x.round; }).join('、')+
      /* ★ 2026-10-20 第三十一轮：原写「无法据以定位」，但紧接着的分支又承认
         「这个峰值轮正落在本窗口内」，前后打架。改成只说单点不够界定时段。 */
      '），单点不足以界定风险时段；能区分出差异的是连续窗口 —— '+
      (ACT_WIN_N
        ? (ACT_MAXR.length === 1
            ? '这个峰值轮正落在本窗口内 —— 单轮尺度上看不出差异，能看出差异的是连续'+CN_NUM(WSEG.length)+'轮。'
            : '这 '+ACT_MAXR.length+' 个峰值轮里有 '+ACT_WIN_N+' 轮落在本窗口内，是全程峰值最密集的一段。')
        /* ★ 2026-10-14 第二十二轮：原写「四轮齐平」——「齐平」是断言，本支只证明「无单轮冲高」。
           ★ 2026-10-20 第二十八轮：全程只有 1 个峰值轮时（EVT-008 的轮 40）印「这几个峰值轮」
           与「没有任何一轮」都不成话 ⇒ 分单复数两支。 */
        : (ACT_MAXR.length === 1
            ? '而这个峰值轮（轮 '+ACT_MAXR[0].round+'）并不在本窗口内，本窗口的优势来自连续'+
              CN_NUM(WSEG.length)+'轮的中高位供给，而不是某一轮冲高。'
            : '这几个峰值轮没有任何一轮落在本窗口内，本窗口的优势来自连续'+CN_NUM(WSEG.length)+
              '轮的中高位供给，而不是某一轮冲高。'))+
      /* ★ 2026-10-20 第二十九轮：原句无条件写「在热度回落之后才完全显现」。
         EVT-002 的负面占比峰值在轮 0、互动峰值在轮 33 ⇒ 情绪恶化**早于**热度见顶，
         原句把因果方向写反了。改为按两个峰值的先后分三支。 */
      '也就是说，'+
      (PEAK_NEG.round < PEAK_INT.round
        ? '情绪恶化早于传播热度见顶完成 —— 热度冲高的那一段，不满已经不是最浓的时候；'+
          '把热度峰值当成情绪峰值处置，时点会整体错后。'
        : (PEAK_NEG.round === PEAK_INT.round
            ? '情绪恶化与传播热度在同一轮见顶，两者叠在同一点上。'
            : '情绪恶化并不随传播热度同时到达，而是在热度回落之后才完全显现，'+
              '并且此时已被负面内容自身推动着继续发酵。'))) +
    p('结论与行动建议。本次事件的时间风险集中在这个窗口，而它并不是情绪恶化的起因，'+
      '而是恶化已经完成的阶段 —— 窗口内 '+pct(WIN_NEG_MIN,0)+' 至 '+pct(WIN_NEG_MAX,0)+
      ' 的负面占比'+
      (WIN_NEG_MIN >= 0.5 ? '说明此时已没有中立的讨论空间。' : '显示此时负面已占主导。')+
      '建议把最高等级的值班与响应资源压在轮 '+
      (WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+'，监控系统在此期间提高采集频率；'+
      /* ★ 2026-10-14 第二十二轮：原写死「评论 N 次而转发为 0…客户诉求回应」——
         EVT-005 窗口内转发是 3 次（不是 0），「客户」也不是该窗口的发声主体。 */
      '由于窗口内评论 '+sum(WSEG,function(x){return x.comment;})+' 次、转发 '+
      sum(WSEG,function(x){return x.repost;})+' 次，处置重点应放在评论区的'+
      '事实澄清与发声主体的诉求回应，而不是渠道拦截。'));
});

mk('#chWin', {
  grid:{left:66, right:70, top:46, bottom:84},
  tooltip:{trigger:'axis', axisPointer:{type:'cross'}, formatter:function(ps){
    var x = S[ps[0].dataIndex];
    return '<b>轮 '+x.round+'</b>　'+x.window[0]+'<br>动作总量：<b>'+
      (x.post+x.comment+x.repost+x.like)+
      '</b>（发帖 '+x.post+' + 评论 '+x.comment+' + 转发 '+x.repost+' + 点赞 '+x.like+'）'+
      '<br>其中互动量 '+x.flow+'（评论+转发+点赞）'+
      '<br>负面占比：<b>'+pct(x.neg)+'</b><br>全场效价：'+f3(x.valence)+
      '<br>在线 '+x.online+' 人'; }},
  legend:{top:8, data:['动作总量','负面占比'], itemWidth:16, itemHeight:9, textStyle:{fontSize:13}},
  /* ★ 2026-10-12 第十一轮：加仿真日副轴（与图 2-1 同款）。柱状图类目轴 boundaryGap 默认为 true，
     副轴必须同为 true（dayAxis 第二参 true），否则 Day 标签会相对柱子错开半个格。 */
  xAxis:[{type:'category', data:S.map(function(x){ return '轮'+x.round; }),
    axisLabel:{fontSize:11.5, interval:1}, axisTick:{show:false}}, dayAxis(24, true)],
  yAxis:[
    {type:'value', name:'动作总量', nameTextStyle:{fontSize:12.5},
     splitLine:{lineStyle:{color:'#eef1f6'}}},
    /* ★ 2026-10-14 第二十二轮：轴区间原为 min:0.8 / max:1.02 —— EVT-001 的负面占比全程在 0.9 上下，
       故写了死数。换事件后若占比整体低于 0.8（EVT-005 全程 50%—58%），折线会整条落在坐标区之外，
       图上直接看不见。改为按本跑占比的实际范围取 10% 档并留边距。 */
    {type:'value', name:'负面占比',
     min:Math.max(0, Math.floor((NEG_MIN_V - 0.05) * 10) / 10),
     max:Math.min(1.02, Math.ceil((PEAK_NEG.neg + 0.01) * 10) / 10),
     axisLabel:{formatter:function(v){ return (v*100).toFixed(0)+'%'; }},
     nameTextStyle:{fontSize:12.5}, splitLine:{show:false}}
  ],
  series:[
    /* ★ 2026-10-12 第九轮：柱由「互动量（评+转+赞）」改为「动作总量（发帖+评论+转发+点赞）」——
       prompt 明确要求「柱状图展示每轮仿真窗口的互动总量（发帖+评论+转发+点赞）」，
       关键时段也正是按这个量取的 argmax（见 WIN 块），柱与阴影区因此同源。 */
    {name:'动作总量', type:'bar', barMaxWidth:24,
     data:S.map(function(x){
       var v = x.post + x.comment + x.repost + x.like;
       var hot = (x.round>=WIN.lo+R0 && x.round<=WIN.hi+R0);
       return {value:v, itemStyle:{color: hot ? '#d4380d' : '#a8c4e6',
         borderRadius:[3,3,0,0]}}; }),
     markArea:{silent:true, itemStyle:{color:'rgba(212,56,13,.09)'},
       label:{show:true, fontSize:12, color:'#a82a06', position:'top'},
       data:[[{name:'关键时段', xAxis:'轮'+(WIN.lo+R0)}, {xAxis:'轮'+(WIN.hi+R0)}]]}},
    {name:'负面占比', type:'line', yAxisIndex:1, smooth:true, symbol:'circle', symbolSize:5,
     data:S.map(function(x){ return x.neg; }),
     itemStyle:{color:'#4a90d9'}, lineStyle:{width:2.2, color:'#4a90d9'}}
  ]
});

/* ---- 3.3 渠道影响力 ---- */
SEC(2100, function(){
  setHTML('r34',
    p('按每帖平均被引次数（该渠道帖子被其他帖子引用的累计次数 ÷ 该渠道发帖数）排序，'+
      PP_TOP+'最高，为 <b>'+CO[PP_TOP].out_per_post.toFixed(3)+' 次/帖</b>：'+CO[PP_TOP].posts+
      ' 条帖共获得 '+CO[PP_TOP].out+' 次引用，是'+CN_NUM(PP_POSTS.length)+'个有内容产出的渠道中最高的'+
      /* ★ 2026-10-14 第二十一轮：原先无条件再与「发帖量最大的渠道」比一次量级 ——
         ①「第二位」与「发帖量最大的渠道」同指一个渠道时会把同一事实印两遍（EVT-004 旧版即如此）；
         ②效率冠军本身就是发帖量最大的渠道时，这句会变成「为发帖量最大的 X 的 1.00 倍」（EVT-002）。
         另：发帖量口径另取 `POST_VOL_C`，不再借用 `OUT_VOL_C`（那是被引总量最大的渠道）。 */
      (PP_TOP === POST_VOL_C
        ? '，它同时也是发帖量最大的渠道 —— 效率与体量这一次落在同一个渠道上。'
        : '，为第二位'+PP_POSTS[1]+'（'+CO[PP_POSTS[1]].out_per_post.toFixed(3)+'）的 '+
          /* ★ 2026-10-20 第三十一轮：除数为 0 时原式算出 Infinity（EVT-001 印出「微博（0.000）
             的 Infinity 倍」）。两处比值都改为先判分母。 */
          (CO[PP_POSTS[1]].out_per_post > 0
            ? (CO[PP_TOP].out_per_post/CO[PP_POSTS[1]].out_per_post).toFixed(2)+' 倍'
            : '无法比值（第二位每帖被引为 0）')+
          (POST_VOL_C === PP_POSTS[1]
            ? '；发帖量最大的渠道正是这第二位'+POST_VOL_C+'。'
            : (CO[POST_VOL_C].out_per_post > 0
                ? '，为发帖量最大的'+POST_VOL_C+'（'+CO[POST_VOL_C].out_per_post.toFixed(3)+'）的 '+
                  (CO[PP_TOP].out_per_post/CO[POST_VOL_C].out_per_post).toFixed(2)+' 倍。'
                : '；发帖量最大的'+POST_VOL_C+'（'+CO[POST_VOL_C].posts+
                  ' 条帖）每帖被引为 0，与前者不在同一量级。')))+
      /* ★ 2026-10-20 第二十九轮：末位并列时原句只点一个渠道（EVT-002 的新闻门户与抖音
         都是 0.000，只印「最弱的是新闻门户」，把同样 8 条/1 条帖、同样零被引的抖音隐掉）。 */
      (function(){
        var lv = CO[PP_LAST].out_per_post;
        var ties = PP_POSTS.filter(function(c){ return Math.abs(CO[c].out_per_post - lv) < 5e-4; });
        return '传播力最弱的是'+ties.join('与')+'（每帖被引 '+lv.toFixed(3)+' 次'+
          (ties.length > 1
            ? '，'+ties.map(function(c){ return c+' '+CO[c].posts+' 条帖、被引 '+CO[c].out+' 次'; })
                    .join('，')+'，并列末位'
            : '')+'）；';
      })()+
      (CH_ZERO_JS.length ? CH_ZERO_STR+'全程无发帖，不存在被引，其读数不参与排序。'
                         : '其读数不参与排序。')) +
    /* ★ 2026-10-20 第二十八轮：原先「其一」无条件点名 `PP_POSTS[1]`（**效率第二名**）并断言
       「样本过小」—— 样本大小与排名无关。EVT-002 的次位恰好是抖音（5 账号 / 8 条帖），
       所以从未显形；EVT-008 的次位是微博（53 账号 / 198 条帖），当场印出「微博只有 198 条帖，
       样本过小，不足以支撑资源分配」。现改为按**发帖基数最小**的渠道判：真的小才说，
       不小就只保留「领先高度集中」这一处。 */
    (function(){
      /* 「样本过小」只该落在**拿来当对照组**的那个渠道上（`PP_POSTS[1]`，正文刚引过它的读数）；
         它不小的话，再退回「有引用、且发帖基数最小」的那个。全程零被引的渠道不参与
         —— 拿「0.000 次/帖建立在 0 次引用上」说样本小是同义反复（EVT-002 的小红书）。 */
      var SMALL = function(c){ return !!(c && CO[c]) && (CO[c].posts < 30 || CO[c].posters < 8); };
      var cand = null;
      if(PP_POSTS.length > 1 && SMALL(PP_POSTS[1])) cand = PP_POSTS[1];
      else {
        var pool = PP_POSTS.filter(function(c){ return CO[c].out > 0; })
                           .sort(function(a, b){ return (CO[a].posts - CO[b].posts) ||
                                                        (CO[a].posters - CO[b].posters); });
        if(pool.length && SMALL(pool[0])) cand = pool[0];
      }
      /* ★ 2026-10-20 第二十八轮：原文把「每帖不足 1 次」写死 —— EVT-002 成立（5 次 / 19 帖），
         EVT-008 当场为假（其余 47 帖合计 111 次被引，每帖 2.36 次），后半句「不是整个渠道的
         内容效率」随之站不住。改为现算其余帖的每帖均值，结论按均值分两支。 */
      var restN = CO[PP_TOP].posts - 1;
      var restOut = CO[PP_TOP].out - PP_TOP_D.indeg;
      var restAvg = restN > 0 ? restOut / restN : 0;
      /* ★ 2026-10-20 第三十轮：该渠道全程只有 1 条帖时，原句会印成
         「其余 0 条帖合计获得 0 次引用，每帖 0.00 次，不足 1 次」（EVT-001 的黑猫投诉）。 */
      var conc = (restN > 0
        ? PP_TOP+'的领先高度集中：其 '+CO[PP_TOP].out+' 次被引中有 '+PP_TOP_D.indeg+
          ' 次来自同一条帖（轮 '+PP_TOP_D.r+'，'+PP_TOP_D.name+'），其余 '+
          restN+' 条帖合计获得 '+restOut+' 次引用，每帖 '+restAvg.toFixed(2)+' 次'+
          (restAvg < 1 ? '，不足 1 次' : '')+'。也就是说，'+PP_TOP+'的「强」'+
          (restAvg < 1
            ? '是少数帖的单帖穿透力，不是整个渠道的内容效率。'
            : '固然有单帖穿透力的成分，但剩下那 '+restN+' 条帖本身也不弱 —— 不能只按「一条爆款」来读它的传播力。')
        : PP_TOP+'全程只有这 1 条帖（轮 '+PP_TOP_D.r+'，'+PP_TOP_D.name+'），'+CO[PP_TOP].out+
          ' 次被引全部来自它 —— 谈不上「渠道内容效率」，只能说这一条帖本身穿透力强，'+
          '1 条帖的样本也不足以据此分配资源。');
      var smallTxt = cand ? (cand+'只有 '+CO[cand].posters+' 个发帖账号、'+CO[cand].posts+' 条帖，'+
        CO[cand].out_per_post.toFixed(3)+' 的读数建立在 '+CO[cand].out+' 次引用上，'+
        '样本过小，不足以支撑资源分配。') : '';
      if(cand && cand !== PP_TOP)
        return p('排序中有两处样本问题必须与数值同时说明。其一，'+smallTxt+'其二，'+conc);
      if(cand)
        return p('排序中有一处样本问题必须与数值同时说明。'+smallTxt+conc);
      return p('排序中有一处样本问题必须与数值同时说明。'+conc);
    })() +
    /* ★ 2026-10-14 第二十一轮：「与之相对，VOL 的强是总量而非效率」隐含**每帖被引最高的渠道 ≠ 引用总量最大的渠道**。
       EVT-002 上两者同为微博（31 / 34 次引用、20 条帖），这句会紧跟「微博每帖被引最高」印出「与之相对，微博的强是总量而非效率」，
       自己打自己；且「被摊薄到 1.550 次」里的 1.550 恰是全场最高值。改为两支：重合时讲「一身二任」。 */
    /* ★ 2026-10-20 第三十轮：「它的介数合计 N 也远高于其他渠道」是写死的方向。
       EVT-001 的引用总量最大者是小红书、介数合计 **0**（各渠道最低），原句印成
       「介数合计 0 也远高于其他渠道，说明传播链大量经过小红书内的节点」——当场自相矛盾。
       改为与其余渠道现比，并按是不是枢纽决定后半句怎么落。 */
    (function(){
      var betwCmp = function(c){
        var others = PP_RANK.filter(function(x){ return x !== c; })
                            .sort(function(a2,b2){ return CO[b2].betw - CO[a2].betw; });
        if(CO[c].betw <= 0) return '，在各渠道中最低，传播链并不经过它的节点';
        if(!others.length || CO[c].betw > CO[others[0]].betw)
          return '，在各渠道中最高，说明传播链大量经过'+c+'内的节点';
        var hi = others.filter(function(x){ return CO[x].betw > CO[c].betw; });
        return '，低于'+hi.join('、')+'，说明传播链并非大量经过'+c+'内的节点';
      };
      if(OUT_VOL_C === PP_TOP)
        return p('这一处「强」同时也是总量上的强：'+PP_TOP+'承担了全程 '+pct(CO[PP_TOP].out/META.edges)+
          ' 的引用份额（'+CO[PP_TOP].out+' / '+META.edges+'），发帖基数 '+CO[PP_TOP].posts+
          ' 条（占全池 '+pct(CO[PP_TOP].posts/META.posts)+'）—— 体量大、每帖被引仍是最高，'+
          '说明它没有出现「靠海量低效帖摊薄效率」的情况；它的介数合计 '+CO[PP_TOP].betw+betwCmp(PP_TOP)+
          '。'+(CO[PP_TOP].betw > 0
            ? '内容层面的穿透力与结构层面的枢纽地位这一次落在同一个渠道上，治理动作也必须合并：'+
              '既做单帖穿透的内容处置，也做枢纽节点的结构处置。'
            : '内容层面的穿透力集中在它身上，但它并不处在传播链的咽喉位置 —— 治理动作落在内容处置与发声主体，'+
              '不要在结构上做无谓的切断。'));
      return p('与之相对，'+OUT_VOL_C+'的强是总量而非效率：它承担了全程 '+pct(CO[OUT_VOL_C].out/META.edges)+
          ' 的引用份额（'+CO[OUT_VOL_C].out+' / '+META.edges+'），但发帖基数达 '+CO[OUT_VOL_C].posts+
          ' 条（占全池 '+pct(CO[OUT_VOL_C].posts/META.posts)+'），每帖被引因此被摊薄到 '+
          CO[OUT_VOL_C].out_per_post.toFixed(3)+' 次；它的介数合计 '+CO[OUT_VOL_C].betw+betwCmp(OUT_VOL_C)+'。'+
          (CO[OUT_VOL_C].betw > 0
            ? '两种「强」对应两种不同的治理动作：前者是内容层面的穿透力，后者是结构层面的枢纽地位。'
            : '也就是说，它的强只在被引次数这一层，结构层面并不处在枢纽位置 —— 治理动作落在内容处置与发声主体，'+
              '而不是在它身上做结构切断。'));
    })() +
    p('结合关键时段看，<b>高传播力渠道'+
      (AMP_FLAG ? '在该时段内同样产生了放大效应' : '并未在该时段内产生放大效应')+'</b>。轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+
      ' 的 '+WPOSTS.length+' 条新帖分布在'+W_CC_RANK.map(function(c){ return c+' '+
        W_CC[c]+' 条'; }).join('、')+'；'+
      /* ★ 2026-10-14 第二十一轮：原本无条件写「PP_TOP 在这一窗口内只有 N 条帖…低于其在全期发帖中的占比」——
         PP_TOP 在该窗口内一条帖都没有时（EVT-003 黑猫投诉），占比一栏会算出 **NaN%**。改为按有无发帖分两支。 */
      /* ★ 2026-10-20 第二十八轮：「低于其在全期发帖中的占比」是写死的方向。EVT-008 实测
         窗口被引占其全程 28.1%、窗口发帖只占其全程 12.5% —— 被引更集中，比发帖更集中，
         原文却印「低于」，与紧邻的「同样产生了放大效应」自相矛盾。改为两侧并列现算。 */
      (W_CC[PP_TOP]
        ? (function(){
            var shC = WIND_TOP_IND / Math.max(1, CO[PP_TOP].out);
            var shP = W_CC[PP_TOP] / Math.max(1, CO[PP_TOP].posts);
            return PP_TOP+'在这一窗口内有 '+W_CC[PP_TOP]+' 条帖（占其全程发帖的 '+pct(shP)+
              '）、'+WIND_TOP_IND+' 次被引（占其全程被引的 '+pct(shC)+'）'+
              (shC >= shP
                ? '，被引比发帖更向这一段集中'
                : '，被引比发帖更分散 —— 这一段里它出了声，却没被接住');
          })()
        : PP_TOP+'在这一窗口内没有发帖，其全程 '+CO[PP_TOP].out+' 次被引全部落在窗口之外')+'；'+
      /* ★ 2026-10-14 第二十二轮：`PP_POSTS[1]`（每帖被引第二名的渠道）在只有一个渠道发帖时
         取到 undefined，会印出「undefined在该窗口内没有发帖」。改为按有无第二名分两支。 */
      (PP_POSTS.length > 1
        ? (W_CC[PP_POSTS[1]] ? PP_POSTS[1]+'在该窗口内有 '+W_CC[PP_POSTS[1]]+' 条帖'
                             : PP_POSTS[1]+'在该窗口内没有发帖')
        : '本场只有一个渠道有内容产出')+'。'+
      (W_INDEG > 0
        ? '窗口内的 '+W_INDEG+' 次引用集中在'+W_INDEG_CH.top+'的 '+
          WPOSTS.filter(function(p2){ return p2.ch === W_INDEG_CH.top && p2.indeg > 0; }).length+
          ' 条帖上，被引最高的'+CN_NUM(W_TOP2POST.length)+'条是'+W_TOP2POST.map(function(p2){
            return p2.name+'（轮 '+p2.r+'，被引 '+p2.indeg+' 次）'; }).join('与')+'；'
        : '窗口内没有任何帖子获得引用；')+
      /* ★ 2026-10-14 第二十二轮：原先无条件断言「介数最高的三个**都是** X 渠道上的 Y 角色账号」——
         该身份取自榜首一个账号，前三名只要不完全同渠道同角色即为假。改为先判再写。 */
      (W_TOPACC.length
        ? '窗口内发帖账号中，'+
          (function(){
            var t3 = W_TOPACC.slice(0, 3);
            var top = '全程介数最高的'+CN_NUM(t3.length)+'个';
            /* ★ 2026-10-20 第三十轮：全为 0 时「介数最高的前三个（介数 0、0、0）」是废话，
               直接说没有咽喉账号。前缀「窗口内发帖账号中，」必须在分支里各自收尾，
               否则会印成「…最高的三个窗口内发帖账号…」。 */
            if(t3.every(function(z){ return !(z.betw > 0); })){
              return top+'也全是 0，没有哪一个处在咽喉位置。';
            }
            var same = t3.length > 1 && t3.every(function(z){
              return z.ch === t3[0].ch && z.role === t3[0].role; });
            return top+(same
              ? '都是'+t3[0].ch+'上的'+t3[0].role+'账号（介数 '+
                t3.map(function(z){ return z.betw; }).join('、')+'）。'
              : '分别落在'+t3.map(function(z){
                  return z.ch+'·'+z.role+'（介数 '+z.betw+'）'; }).join('、')+'。');
          })()
        : '窗口内没有任何账号发帖。')) +
    p('渠道治理建议。资源不应按发帖量平均分配。'+
      /* ★ 2026-10-14 第二十一轮：原先一律「PP_TOP 不做主战场、VOL 做主战场」——EVT-002 上两者同为微博，
         会印出「微博…但不把它作为本次事件的主战场。微博的体量决定了主线处置必须落在这里」，自相矛盾。 */
      (OUT_VOL_C === PP_TOP
        ? PP_TOP+'按每帖被引配置：维持全量抽检，对其中的高被引账号单独建档；它同时也是本场引用总量最大的渠道，'+
          '体量决定了主线处置必须落在这里 —— 两重身份叠在同一个渠道上，'+
          '重点对象是窗口内活跃、且在全程传播链上处在咽喉位置的那几个'+W_TOP_ROLE_STR+'。'
        : PP_TOP+'按每帖被引配置：维持全量抽检，'+
          '对其中的高被引账号单独建档，但不把它作为本次事件的主战场。'+OUT_VOL_C+
          /* ★ 2026-10-30 第三十轮：原写「体量」，但 OUT_VOL_C 是**引用总量**最大的渠道，
             发帖量未必最大（EVT-001 小红书 8 条 vs 微博 25 条）。改为照实说。 */
          '的引用总量（'+CO[OUT_VOL_C].out+' 次）决定了主线处置必须落在这里，'+
          '重点对象是窗口内活跃、且在全程传播链上处在咽喉位置的那几个'+W_TOP_ROLE_STR+'。')+
      (function(){
        var rest = PP_POSTS.filter(function(c){ return c !== PP_TOP && c !== OUT_VOL_C; });
        /* ★ 2026-10-20 第三十轮：原先不分样本量一律叫「高频低穿透」——EVT-002 里抖音
           只有 1 条帖也被写成"高频"。
           ★ 2026-10-20 第三十一轮：只按发帖量 ≥3 分档仍然印错 —— EVT-001 的抖音（4 条、
           每帖 2.000）与新闻门户（4 条、每帖 1.500）都被归进「接得少」，与同句里的数字
           当场矛盾。改为**发帖量 ≥3 且每帖被引低于全池均值**才算高频低穿透，其余的大体量
           渠道另说「体量与穿透兼有」。 */
        var poolPer = (function(){
          var p0 = 0, o0 = 0;
          PP_POSTS.forEach(function(c){ p0 += CO[c].posts; o0 += CO[c].out; });
          return p0 ? o0 / p0 : 0;
        })();
        var big     = rest.filter(function(c){ return CO[c].posts >= 3; });
        var bigLow  = big.filter(function(c){ return CO[c].out_per_post <  poolPer - 1e-9; });
        var bigHigh = big.filter(function(c){ return CO[c].out_per_post >= poolPer - 1e-9; });
        var small   = rest.filter(function(c){ return CO[c].posts <  3; });
        /* ★ 2026-10-20 第三十一轮：小样本里原先把「每帖被引并不低」当结论写死，
           EVT-002 实测抖音 1 条帖、每帖被引 0.000（与末位并列）也被说成「并不低」。
           拆成两档：小样本但读数不低的、小样本且读数也低的。 */
        var smallHigh = small.filter(function(c){ return CO[c].out_per_post >= poolPer - 1e-9; });
        var smallLow  = small.filter(function(c){ return CO[c].out_per_post <  poolPer - 1e-9; });
        var one   = function(c){ return c+' '+CO[c].posts+' 条帖换来 '+CO[c].out+
            ' 次引用（每帖 '+CO[c].out_per_post.toFixed(3)+' 次）'; };
        if(!rest.length) return '';
        var sent = [rest.map(one).join('、')];
        if(bigLow.length) sent.push('其中发帖量大、每帖被引又低于全池均值 '+poolPer.toFixed(3)+
            ' 的'+bigLow.join('、')+'属于发得多、接得少的高频低穿透，宜用服务触点逐条回应，'+
            '而不是追加公关投放');
        if(bigHigh.length) sent.push(bigHigh.join('、')+'的发帖量同样不小，但每帖被引不低于全池均值，'+
            '属于体量与穿透兼有，宜按高价值渠道配置');
        if(smallHigh.length) sent.push(smallHigh.join('、')+'的每帖被引并不低，问题只在样本量小，宜逐条回应');
        if(smallLow.length) sent.push(smallLow.join('、')+'的帖子不到 3 条、每帖被引也低于全池均值，'+
            '读数不足以支撑判断，先逐条观察、不单独配置资源');
        return sent.join('；')+'。';
          (CH_ZERO_JS.length
            ? CH_ZERO_STR+'虽然无发帖，本轮仍'+
              (CH_ZERO_JS.length > 1 ? '各有 ' : '有 ')+
              CH_ZERO_JS.map(function(c){ return COof(c).agents; }).join(' 个与 ')+
              ' 个智能体在关注，需维持在监控范围内。'
            : '');
      })()));
});

mk('#chChanInt', {
  grid:{left:84, right:76, top:26, bottom:44},
  tooltip:{trigger:'axis', axisPointer:{type:'shadow'}, formatter:function(ps){
    var c = PP_PLOT[ps[0].dataIndex], o = CO[c];
    return '<b>'+c+'</b><br>每帖被引：<b>'+o.out_per_post.toFixed(3)+'</b><br>发帖 '+o.posts+
      '　被引合计 '+o.out+'<br>发帖账号 '+o.posters+' / 活跃账号 '+o.agents+
      '<br>零被引账号 '+o.zero+' / '+o.agents+
      (o.posts ? '' : '<br>（本渠道全程无发帖，读数为 0）'); }},
  xAxis:{type:'value', name:'次/帖', nameTextStyle:{fontSize:12},
    splitLine:{lineStyle:{color:'#eef1f6'}}},
  yAxis:{type:'category', data:PP_PLOT, axisLabel:{fontSize:14, fontWeight:600}, axisTick:{show:false}},
  series:[{type:'bar', barMaxWidth:28,
    data:PP_PLOT.map(function(c){
      return {value: CO[c].out_per_post,
        itemStyle:{color: CO[c].posts ? CH_COLOR[c] : '#c2cbd6', borderRadius:[0,4,4,0]}}; }),
    label:{show:true, position:'right', fontSize:12.5, color:'#33455c',
      formatter:function(pr){ return pr.value.toFixed(3); }}}]
});

/* ---- 表 3-2（渠道维度明细；行序改为每帖被引降序） ---- */
SEC(2177, function(){
  var h = '<thead><tr><th>渠道</th><th class="num">发帖数</th><th class="num">占比</th>'+
    '<th class="num">发帖账号</th><th class="num">活跃账号</th><th class="num">零被引账号</th>'+
    '<th class="num">被引合计</th><th class="num">每帖被引</th><th class="num">介数合计</th>'+
    '<th class="num">互动合计</th></tr></thead><tbody>';
  PP_RANK.forEach(function(c){
    var o = CO[c], hi = (c === PP_TOP || c === OUT_VOL_C);
    h += '<tr'+(hi?' class="hot"':'')+'><td>'+chChip(c)+'</td>'+
      '<td class="num">'+o.posts+'</td><td class="num">'+(o.posts?pct(o.posts/META.posts):'—')+'</td>'+
      '<td class="num">'+o.posters+'</td><td class="num">'+o.agents+'</td>'+
      '<td class="num">'+o.zero+'</td><td class="num">'+o.out+'</td>'+
      '<td class="num"><b>'+o.out_per_post.toFixed(3)+'</b></td>'+
      '<td class="num">'+o.betw+'</td><td class="num">'+o.inter+'</td></tr>';
  });
  setHTML('tbChan', h+'</tbody>');
});

/* ================= 模块四 ================= */

/* ---- 模块总结（★ 2026-10-13 第十七轮新增；此前模块四**没有**模块总结） ----
   格式对齐模块一/二/三：结论先行、约 450 字、全部现算，不誊写任何轮号或数值。 */
SEC(2198, function(){
  /* ★ 2026-10-14：原为 `AL.rows[AL.first_round['红色']]`。种子里换成 `AL.rows[0]` 后，
     下一行的全局取最大值扫描结果**完全相同**（从任一轮起扫，最大值一样），
     但没有红色轮时不再取到 undefined。EVT-001 输出逐位不变。 */
  var peak = AL.rows[0];
  AL.rows.forEach(function(a){ if(a.score > peak.score) peak = a; });
  var redFirst = AROW(AL.first_round['红色']);
  var lvN = {};
  AL.rows.forEach(function(a){ lvN[a.level] = (lvN[a.level]||0) + 1; });
  function range(g){ var v = AL.rows.map(function(x){ return x[g]; });
    return Math.max.apply(null, v) - Math.min.apply(null, v); }
  var top5 = AL.rows.slice().sort(function(a,b){ return b.score - a.score; }).slice(0,5)
      .sort(function(a,b){ return a.round - b.round; });
  var hToRed = HAS_RED ? (wsec(redFirst.window[0]) - wsec(META.t0)) / 3600e3 : 0;
  /* ★ 2026-10-14 第二十一轮：峰值落在末轮时，这一块的「见顶回落 / 末段回落 / 能退回来」
     三处措辞全部为假（EVT-004 峰值轮 41 = 末轮）。三处改为读这两个现算量。 */
  var LV_ORDER   = ['蓝色','黄色','橙色','红色'];
  var peakIsLast = (peak.round === AL.rows[AL.rows.length-1].round);
  var lvDrops    = 0;
  AL.rows.forEach(function(a, i){
    if(i && LV_ORDER.indexOf(a.level) < LV_ORDER.indexOf(AL.rows[i-1].level)) lvDrops += 1;
  });
  setHTML('r40',
    (HAS_RED
     ? p('风险等级的走势是「低开、快速爬升、高位震荡、逐步退潮」。全程 '+META.rounds+
      ' 轮里蓝色只有 '+lvN['蓝色']+' 轮、黄色 '+lvN['黄色']+' 轮、橙色 '+(lvN['橙色']||0)+
      ' 轮、红色 '+(lvN['红色']||0)+' 轮；最高点落在轮 '+peak.round+'（'+peak.window[0]+
      '，S = '+peak.score.toFixed(3)+'），红色首次触发于轮 '+redFirst.round+'（'+
      redFirst.window[0]+'，t0 后约 '+n0(hToRed)+' 小时）。'+
      /* ★ 2026-10-14 第二十二轮：原先写死「橙色自轮 X 起成为常态」。橙色只出现过一两轮时
         「常态」为假；橙色完全没出现时该轮号取到 undefined。改为按实际轮数分档。 */
      ((lvN['橙色']||0) >= 2
        ? '橙色自轮 '+AL.first_round['橙色']+' 起'+
          ((function(){ var o = AL.rows.filter(function(a){ return a.level==='橙色'; });
             return o.every(function(x,i){ return i===0 || x.round===o[i-1].round+1; })
                    ? '连续出现' : '断续出现'; })())+
          '（共 '+lvN['橙色']+' 轮），'
        : ((lvN['橙色']||0) === 1
            ? '橙色只在轮 '+AL.first_round['橙色']+' 出现过一次，'
            : '橙色全程未被触发，'))+
      '红色断续出现在轮 '+
      AL.rows.filter(function(a){ return a.level==='红色'; }).map(function(a){ return a.round; }).join('、')+
      /* ★ 2026-10-14 第二十二轮：原先此处拿「改版前『自轮 15 起连续 27 轮红色、一次没退过』」
         作对照 —— 那是上一版分级口径的读数，不是本事件本跑的产物。改为用本跑现算的回落次数。 */
      '，末段回落到'+AL.rows[AL.rows.length-1].level+
      ' —— 全程共出现 '+lvDrops+' 次等级回落，红色并非只升不退，预警等级<b>能退回来</b>。')
     : p('风险等级的走势是「低开、爬升、见顶'+(peakIsLast ? '后未及回落' : '回落')+'」。全程 '+META.rounds+
      ' 轮里蓝色 '+lvN['蓝色']+' 轮、黄色 '+(lvN['黄色']||0)+' 轮、橙色 '+(lvN['橙色']||0)+
      ' 轮、红色 '+(lvN['红色']||0)+' 轮；等级路径为'+
      /* ★ 2026-10-20 第三十一轮：同 r43 —— 按首次到达轮次排，不再按严重度机械拼接。 */
      REACHED.slice().reverse().slice().sort(function(a,b){
        return (AL.first_round[a]||0) - (AL.first_round[b]||0); }).map(function(L){
        return L+'（轮 '+AL.first_round[L]+'）'; }).join(' → ')+
      /* ★ 2026-10-20 第二十八轮：原文「只到 <b>黄色</b>（共 N 轮，首次出现于轮 3（时点），
         t0 后约 X 小时）」—— 中文里挤进一个空格、括号里再套一对括号。改为不套。 */
      '，本跑最高只到<b class="k">'+TOP_LV+'</b>（共 '+TOP_N+' 轮，首次出现于轮 '+
      TOP_ROW.round+'，即 '+TOP_ROW.window[0]+'、t0 后约 '+n0(H_TO_TOP)+' 小时），'+
      '全程未触及红色阈值（S ≥ '+AL.cut['红色'].toFixed(2)+'）。最高点落在轮 '+
      peak.round+'（'+peak.window[0]+'，S = '+peak.score.toFixed(3)+
      /* ★ 2026-10-14 第二十一轮：原先无条件说「末段回落到 X…本口径下的等级同样是能退回来的」——
         最高点本身落在末轮时（EVT-004 峰值轮 41 = 末轮），这句会自相矛盾。改成两支各说各的。 */
      (peakIsLast
        ? '），最高点即末轮 —— 本跑的等级'+
          (lvDrops ? '全程出现过 '+lvDrops+' 次回落，' : '自轮 0 起逐档抬升、全程没有回落，')+
          '结束时仍停在最高档。'
        : '），末段回落到'+AL.rows[AL.rows.length-1].level+
          ' —— 本口径下的等级同样是<b>能退回来的</b>。'))) +
    p('等级高低几乎完全由这一段里<b>有多少人在动、动得多重</b>决定。三个维度里，参与强度 G3 的全程极差 '+
      range('G3').toFixed(3)+'、传播广度 G1 '+range('G1').toFixed(3)+
      '，而情绪态势 G2 只有 '+range('G2').toFixed(3)+
      /* ★ 2026-10-14 第二十二轮：原先写死「负面情绪自轮 2 起就长期贴在负区间」（EVT-001 的读数）。
         EVT-005 自 t0 起效价即为负，这一句当场为假。改为按效价序列现算首个「从此不再回正」的轮。 */
      ' —— '+(V_NEG_FROM.anyNeg
        ? (V_NEG_FROM.allNeg
            ? '负面情绪自推演起点（t0）起就没有回过正区间，'
            : (V_NEG_FROM.ok
                ? '负面情绪自轮 '+V_NEG_FROM.r+' 起就长期贴在负区间、再没回到非负，'
                : '情绪只在末段回到非负，其余全程贴在负区间，'))
        : '全程效价从未跌破 0，')+
      '水平本身几乎不再提供区分度；真正把等级推上去的是发声人数、'+
      '动作强度加权互动与新生帖介数峰值三项的同步抬升。总分最高的'+CN_NUM(top5.length)+'轮是轮 '+
      top5.map(function(a){ return a.round; }).join('、')+'，全部落在轮 '+top5[0].round+'—'+
      top5[top5.length-1].round+' 这一段。') +
    p('处置资源应压在轮 '+top5[0].round+'—'+top5[top5.length-1].round+' 这一段（S 前五：'+
      top5.map(function(a){ return '轮 '+a.round+' '+a.score.toFixed(3); }).join('、')+
      /* ★ 2026-10-14 第二十一轮：原先「六个」是字面量、轮号列表却是现算的 ——
         EVT-001 恰为 6 个所以从不显形，换事件就印出「六个…（轮 2、14、20、38、40）」这种 5 个。 */
      '）。这里有一处口径差需要点明：模块三的 '+EVENTS.length+' 个关键影响节点'+
      (EVENTS.length ? '（轮 '+EVENTS.map(function(d){ return d.round; }).join('、')+'）' : '')+
      '认的是结构转折，'+
      '风险等级认的是当期量能，两者未必重合 —— '+
      /* ★ 2026-10-20：EVENTS 为空时原先印「0 个关键影响节点（轮 ）」—— 括号里没轮号。 */
      (!EVENTS.length
        ? '本跑没有任何结构转折轮，等级完全由当期量能决定；最高等级「'+TOP_LV+'」首现于轮 '+
          TOP_ROW.round+'（'+TOP_ROW.window[0]+'）。'
        : HAS_RED
       /* ★ 2026-10-20 第三十轮：原句无条件断言「首红轮不在关键影响节点里」。
          EVT-001 的首红轮 5 恰恰就是关键影响节点（见模块三 3.1），两句直接打架。 */
       ? (EVENTS.map(function(d){ return d.round; }).indexOf(redFirst.round) >= 0
          ? '首红轮 '+redFirst.round+' 同时也是关键影响节点 —— 结构转折与量能触顶落在同一轮。'
          : '首红轮 '+redFirst.round+' 不在关键影响节点里。')
       : '最高等级「'+TOP_LV+'」的首现轮 '+TOP_ROW.round+
         (EVENTS.map(function(d){ return d.round; }).indexOf(TOP_ROW.round) >= 0
          ? ' 同时也是关键影响节点，两类口径这一次重合。'
          : ' 不在关键影响节点里。')+
         '本跑全程未触及红色（阈值 S ≥ '+AL.cut['红色'].toFixed(2)+'，实测最高 S = '+
         AL.peak.score.toFixed(3)+'），这本身是一个结论：按现行渠道权重与分级阈值判，'+
         '本事件的量能上限不足以触发最高级响应。')+
      (EVENTS.length
        ? '处置上宜合并使用：结构转折轮做定性判断，高量能轮做资源投放。'
        : '处置上直接按高量能轮投放资源即可。')));
});

/* ---- 表 4-1 预警规则常量（★ 2026-10-13 第十七轮新增） ----
   仿真侧：「渠道权重不是仅仅为了本轮，而是基础的预警规则，和 3.3 无关，这里也需要」。
   六个渠道、六类动作全部列出，**本跑没触发的档也照列**，并把本跑的实测用量写在右侧，
   读者才能判断哪几档这次真的起作用。两侧数值全部现算，不写死。 */
SEC(2260, function(){
  var ACTN = {};
  S.forEach(function(x){ var a = x.act || {};
    Object.keys(a).forEach(function(k){ ACTN[k] = (ACTN[k]||0) + a[k]; }); });
  var CHW = AL.chan_weights, ACW = AL.action_weights;
  var chOrder = CHS.slice().sort(function(a,b){ return CHW[b] - CHW[a]; });
  var chTxt = chOrder.map(function(c){ return c+' '+CHW[c].toFixed(1); }).join(' · ');
  var chHit = chOrder.filter(function(c){ return CO[c] && CO[c].posts; });
  var chUse = chOrder.map(function(c){
    return c+' '+(CO[c] ? CO[c].posts : 0)+' 帖'+(CO[c] && CO[c].posts ? '' : '（未触发）');
  }).join('、');
  var actMap = [['NOTICE','通报'],['COMPLAINT','投诉引用'],['QUOTE_POST','引用'],
                ['REPOST','转发'],['CREATE_COMMENT','评论'],['LIKE_POST','点赞']];
  var actTxt = actMap.map(function(p){ return p[1]+' '+ACW[p[0]].toFixed(0); }).join(' · ');
  var actUse = actMap.map(function(p){
    return p[1]+' '+(ACTN[p[0]]||0)+' 次'+(ACTN[p[0]] ? '' : '（未触发）'); }).join('、');
  var dimTxt = ['G1','G2','G3'].map(function(g){
    return AL.dim_names[g]+' '+g+' '+AL.weights[g].toFixed(2); }).join(' · ');
  var cutTxt = ['红色','橙色','黄色'].map(function(L){
    return L+' ≥ '+AL.cut[L].toFixed(2); }).join(' · ')+
    ' · 蓝色 < '+AL.cut['黄色'].toFixed(2);
  var lvHit = ['红色','橙色','黄色','蓝色'].filter(function(L){
    return AL.first_round[L] !== undefined; }).join(' / ');
  var rows = [
    ['窗口长度','每轮各取 5 轮滑动窗口（约 20 小时），末尾对齐当前轮','全程 '+META.rounds+' 个窗口'],
    ['渠道权重', chTxt, '六个渠道中 '+chHit.length+' 个有内容产出：'+chUse],
    ['动作强度', actTxt, '六类动作中实际出现 '+
      actMap.filter(function(p){ return ACTN[p[0]]; }).length+' 类：'+actUse],
    ['维度权重', dimTxt, '三项维度各自归一化后加权求和'],
    ['分级阈值', cutTxt, '本跑出现 '+lvHit]
  ];
  var h = '<thead><tr><th>规则项</th><th>取值</th><th>本跑作用面</th></tr></thead><tbody>';
  rows.forEach(function(r){
    h += '<tr><td><b>'+r[0]+'</b></td><td>'+r[1]+'</td><td>'+r[2]+'</td></tr>'; });
  setHTML('tbRule', h+'</tbody>');
});

SEC(2297, function(){
  var last = AL.rows[N-1];
  var h = '<thead><tr><th>维度</th><th>对应结果模块</th><th>观测量</th>'+
    '<th class="num">截止轮取值</th><th class="num">归一化</th><th class="num">维度分</th>'+
    '<th class="num">权重</th><th class="num">加权贡献</th></tr></thead><tbody>';
  /* ★ 2026-10-13 第十七轮：六项观测量改为**5 轮窗口口径**（另两项 ④效价强度 / ⑤负面人数占比 取当轮即时值）；维度三由「节点集中度」
     （最大介数 + 前 5 帖被引份额）改为「参与强度」（发声人数 + 动作强度加权互动 +
     新生帖介数峰值）——「前 5 帖被引份额」按仿真侧意见删除。窗口取截止轮所在的那 5 轮，
     具体轮次由载荷的 win_lo / win_hi 给出，不写死。 */
  var defs = [
    ['传播广度','传播路径图谱','轮 '+last.win_lo+'—'+last.win_hi+' 窗口内渠道加权帖量　'+last.w_postw+' / '+AL.norm.postw, last.o1_postw, 'G1'],
    ['','','窗口内发帖渠道种数（去重）　'+last.w_nch+' / '+AL.norm.nch, last.o1_nch, 'G1'],
    ['','','窗口内新增跨渠道边　'+last.w_cross+' / '+AL.norm.cross, last.o1_cross, 'G1'],
    ['情绪态势','情绪演化曲线','|全场效价| / 0.50　—— '+f3(S[N-1].valence), last.o2_val, 'G2'],
    ['','','全场负面人数占比（该轮即时值）　'+pct(S[N-1].neg), last.o2_neg, 'G2'],
    ['参与强度','传播路径图谱','窗口内发声人数　'+last.w_nag+' / '+AL.norm.nag, last.o3_num, 'G3'],
    ['','','窗口内动作强度加权互动　'+last.w_actw+' / '+AL.norm.actw, last.o3_act, 'G3'],
    ['','','窗口内新生帖介数峰值　'+last.w_betw+' / '+AL.norm.betw, last.o3_betw, 'G3']
  ];
  var seen = {};
  defs.forEach(function(d){
    var g = d[4], w = AL.weights[g], first = !seen[g];
    h += '<tr><td>'+(first?'<b>'+d[0]+'</b>':'')+'</td><td>'+(first?d[1]:'')+'</td>'+
      '<td>'+d[2]+'</td><td class="num">'+d[3]+'</td>'+
      '<td class="num">'+((+d[3])*100).toFixed(1)+'%</td>'+
      '<td class="num">'+(first?'<b>'+last[g]+'</b>':'')+'</td>'+
      '<td class="num">'+(first?'<b>'+w+'</b>':'')+'</td>'+
      '<td class="num">'+(first?'<b>'+(w*last[g]).toFixed(4)+'</b>':'')+'</td></tr>';
    seen[g] = 1;
  });
  h += '<tr class="hot"><td colspan="6" style="text-align:right"><b>综合风险总分 S</b></td>'+
    '<td class="num"><b>1.000</b></td><td class="num"><b>'+last.score.toFixed(4)+'</b></td></tr>';
  setHTML('tbDim', h+'</tbody>');
});

mk('#chAlert', {
  grid:{left:66, right:66, top:50, bottom:56},
  tooltip:{trigger:'axis', axisPointer:{type:'cross'}, formatter:function(ps){
    var a = AL.rows[ps[0].dataIndex];
    return '<b>轮 '+a.round+'</b>　'+a.window[0]+'<br>风险总分 S：<b>'+a.score.toFixed(4)+
      '</b>　'+lvChip(a.level)+'<br>G1 广度 '+a.G1.toFixed(3)+
      '　G2 情绪 '+a.G2.toFixed(3)+'　G3 参与 '+a.G3.toFixed(3)+
      '<br>窗口 轮 '+a.win_lo+'—'+a.win_hi+'：加权帖量 '+a.w_postw+'　发帖渠道 '+a.w_nch+
      '　发声人数 '+a.w_nag+'<br>动作强度加权互动 '+a.w_actw+'　新生帖介数峰值 '+a.w_betw; }},
  legend:{top:8, textStyle:{fontSize:13}, itemWidth:14, itemHeight:9},
  xAxis:{type:'category', data:S.map(function(x){ return '轮'+x.round; }),
    axisLabel:{fontSize:11.5, interval:1}},
  yAxis:[
    {type:'value', name:'风险总分', min:0, max:1, nameTextStyle:{fontSize:12.5},
     splitLine:{lineStyle:{color:'#eef1f6'}}},
    {type:'value', name:'维度分', min:0, max:1, nameTextStyle:{fontSize:12.5}, splitLine:{show:false}}
  ],
  series:[
    {name:'风险总分 S', type:'bar', barMaxWidth:22,
     data:AL.rows.map(function(a){
       return {value:a.score, itemStyle:{color:LV_COLOR[a.level], borderRadius:[3,3,0,0]}}; }),
     markLine:{silent:true, symbol:'none', lineStyle:{width:1.4}, data:[
       {yAxis:0.80, lineStyle:{color:'#d4380d', type:'dashed'},
        label:{formatter:'红色 ≥0.80', fontSize:11.5, color:'#d4380d'}},
       {yAxis:0.65, lineStyle:{color:'#fa8c16', type:'dashed'},
        label:{formatter:'橙色 ≥0.65', fontSize:11.5, color:'#fa8c16'}},
       {yAxis:0.50, lineStyle:{color:'#c9a500', type:'dashed'},
        label:{formatter:'黄色 ≥0.50', fontSize:11.5, color:'#c9a500'}}]}},
    {name:'G1 传播广度', type:'line', yAxisIndex:1, smooth:true, symbol:'none',
     data:AL.rows.map(function(a){ return a.G1; }),
     itemStyle:{color:'#4a90d9'}, lineStyle:{width:1.8, color:'#4a90d9'}},
    {name:'G2 情绪态势', type:'line', yAxisIndex:1, smooth:true, symbol:'none',
     data:AL.rows.map(function(a){ return a.G2; }),
     itemStyle:{color:'#d4380d'}, lineStyle:{width:1.8, color:'#d4380d'}},
    {name:'G3 参与强度', type:'line', yAxisIndex:1, smooth:true, symbol:'none',
     data:AL.rows.map(function(a){ return a.G3; }),
     itemStyle:{color:'#8a6ad4'}, lineStyle:{width:1.8, color:'#8a6ad4'}}
  ]
});

SEC(2372, function(){
  var lv = ['蓝色','黄色','橙色','红色'];
  var h = '<thead><tr><th>等级</th><th class="num">首次出现轮次</th><th>首次出现时点</th>'+
    '<th class="num">持续轮数</th><th class="num">该级平均总分</th><th>判定阈值</th></tr></thead><tbody>';
  lv.forEach(function(L){
    var rows = AL.rows.filter(function(a){ return a.level===L; });
    if(!rows.length) return;
    var avg = sum(rows, function(a){ return a.score; }) / rows.length;
    h += '<tr'+(L==='红色'?' class="hot"':'')+'><td>'+lvChip(L)+'</td>'+
      '<td class="num"><b>'+rows[0].round+'</b></td>'+
      '<td class="mono">'+rows[0].window[0]+'</td>'+
      '<td class="num">'+rows.length+'</td><td class="num">'+avg.toFixed(4)+'</td>'+
      '<td class="mono">S ≥ '+(AL.cut[L]||0).toFixed(2)+'</td></tr>';
  });
  setHTML('tbLevel', h+'</tbody>');
});

SEC(2389, function(){
  var redRows = AL.rows.filter(function(a){ return a.level==='红色'; });
  /* ★ 2026-10-14 第二十二轮：r43 原先写死「由蓝色（轮 0，…）经黄色（轮 X）与橙色（轮 Y）逐级升至红色」——
     轮 0、蓝→黄→橙→红四级齐全都是 EVT-001 的形状。若某跑未经过橙色（直接黄→红），
     `AL.first_round['橙色']` 为 undefined，会印出「橙色（轮 undefined）」。
     `REACHED` 是按等级高→低排列的到达序列，反排即得本跑的上升路径。 */
  var P_ASC = REACHED.slice().reverse();

  /* ★ 2026-10-14：本跑没到过红色时，`redFirst` 为 undefined，下面 `prevRow`/`comp`/
     `redFirst.*` 全线崩溃（这也正是本报告此前只剩骨架的第二处崩点）。此时改用
     **实际到达的最高等级** TOP_ROW 作同一套叙述的主体（触发项、前后轮对比、时间点排序
     三种算法一字不改，只把「红色」换成 TOP_LV），并如实写明未触及红色。
     HAS_RED 为真时**直接走下面原有代码，一行未动** ⇒ EVT-001 逐位不变。 */
  if(!HAS_RED){
    var tRow  = TOP_ROW;
    /* 上一轮的行；tRow 已是首行时钳到首行（与老跑 `Math.max(0, round-1)` 同一语义）。 */
    var tPrev = AROW(Math.max(R0, tRow.round - 1));
    var tComp = [
      {n:'传播广度（G1）', v:(tRow.G1 - tPrev.G1)*AL.weights.G1},
      {n:'情绪态势（G2）', v:(tRow.G2 - tPrev.G2)*AL.weights.G2},
      {n:'参与强度（G3）', v:(tRow.G3 - tPrev.G3)*AL.weights.G3}
    ].sort(function(a,b){ return b.v - a.v; });
    var tLead = tComp[0].n.indexOf('G1')>0
        ? '说明这一段里推动等级上行的是传播面在扩张。'
        : (tComp[0].n.indexOf('G2')>0
           ? '说明这一段里推动等级上行的不是传播面扩张，而是整体情绪本身的继续恶化。'
           : '说明这一段里推动等级上行的是参与强度的抬升（发声人数、动作强度加权互动与新生帖介数峰值同步上台阶）。');
    var tCE = CE.filter(function(e){ return e.round === tRow.round; });
    var tPeak = AL.rows.slice().sort(function(a,b){ return b.score - a.score; })[0];
    /* ★ 尾段要按 TOP_LV **最后一次**出现来算，不是第一次 —— 否则会印出
       「自轮 3 之后再未回到黄色，此后 38 轮一直在黄色与蓝色之间」这种自相矛盾的句子
       （黄色在轮 13/15/16… 还反复出现过）。下面与红色分支的 redLast 是同一套做法。 */
    var tRows = AL.rows.filter(function(a){ return a.level === TOP_LV; });
    var tLast = tRows[tRows.length - 1];
    var tTail = AL.rows.filter(function(a){ return a.round > tLast.round; });
    var tTailLv = tTail.map(function(a){ return a.level; })
        .filter(function(v,i,arr){ return arr.indexOf(v) === i; }).join('与');
    var tSeq = [
      {n:'预警升'+TOP_LV, r:tRow.round},
      {n:'情绪最低点', r:LOW_V.round},
      {n:'负面占比峰值', r:PEAK_NEG.round},
      {n:'互动量峰值', r:PEAK_INT.round}
    ].sort(function(a,b){ return a.r - b.r; });
    setHTML('r43',
      p('本次事件在推演区间内由'+(function(){
          /* 只列**低于**最高等级的那些等级（最高等级由下半句单独点名），
             免得写成「经黄色走到最高等级黄色」。 */
          var below = REACHED.slice(1);
          if(!below.length){ below = REACHED.slice(); }
          return below.reverse().map(function(L){
            return (L === AL.rows[0].level
                    ? L+'（轮 '+AL.rows[0].round+'，'+AL.rows[0].window[0]+'）'
                    : L+'（轮 '+AL.first_round[L]+'）');
          }).join('经');
        })()+'走到本跑的最高等级「<b class="k">'+TOP_LV+'</b>」，'+
        '首次出现于<b class="k">轮 '+tRow.round+'（'+tRow.window[0]+'）</b>，'+
        '即 t0 后约 '+n0(H_TO_TOP)+' 小时。<b>全程未触及红色阈值</b>（S ≥ '+
        AL.cut['红色'].toFixed(2)+'）：'+TOP_LV+'全程共 '+TOP_N+' 轮（占 '+
        (TOP_N/AL.rows.length*100).toFixed(1)+'%），最高总分落在轮 '+tPeak.round+
        '（'+tPeak.window[0]+'，S = '+tPeak.score.toFixed(4)+'），距红色阈值还差 '+
        (AL.cut['红色'] - tPeak.score).toFixed(4)+'。'+
        /* ★ 2026-10-20 第二十九轮：原先只要出现 ≥2 轮就写「断续出现…中间有回落」——
           EVT-002 的橙色是轮 2、3、4 **连续**三轮，句子却说自己断续。改为先判连续性。 */
        (tRows.length > 1
          ? (function(){
              var cont = tRows.every(function(x, i){ return i === 0 || x.round === tRows[i-1].round + 1; });
              return TOP_LV + (cont ? '连续出现在轮 ' : '断续出现在轮 ') +
                tRows.map(function(x){ return x.round; }).join('、') +
                (cont ? '，中间没有回落，是一段连续窗口；' : '，中间有回落，不是一路维持；');
            })()
          : '')+
        (tTail.length
          ? '自轮 '+tLast.round+' 之后再未回到'+TOP_LV+'，此后 '+tTail.length+' 轮一直'+
            (tTail.length === 1 || tTailLv.indexOf('与') < 0 ? '是' : '在')+tTailLv+
            (tTailLv.indexOf('与') < 0 ? '' : '之间')+'。'
          : '自轮 '+tLast.round+' 起'+TOP_LV+'持续到推演结束，未再回落。')) +
      p('推上'+TOP_LV+'的直接贡献项是<b>'+tComp[0].n+'</b>。轮 '+tPrev.round+' 至轮 '+
        tRow.round+' 的风险总分由 '+tPrev.score.toFixed(4)+
        ' 升至 '+tRow.score.toFixed(4)+'，上升 '+(tRow.score - tPrev.score).toFixed(4)+
        '，三项贡献依次为'+tComp.map(function(c){ return c.n+' '+c.v.toFixed(4); }).join('、')+
        '（该轮新增跨渠道传播 '+tCE.length+' 次'+
        (tCE.length ? '：'+tRow.window[0]+' 由 '+tCE.map(function(e){
          return e.from.join('+')+' → '+e.to.join('+'); }).join('、') : '')+
        '）；该轮窗口内发声人数 '+tRow.w_nag+'、动作强度加权互动 '+tRow.w_actw+
        '、渠道加权帖量 '+tRow.w_postw+'，归一值（该量 / 全程窗口最大）分别为 '+
        tRow.o3_num.toFixed(3)+' / '+tRow.o3_act.toFixed(3)+' / '+
        tRow.o1_postw.toFixed(3)+'，'+tLead) +
      p('与情绪曲线的对应关系：把四个时间点按先后排开 —— '+
        tSeq.map(function(x){ return x.n+'（轮 '+x.r+'）'; }).join(' → ')+
        '。'+(tSeq[0].n === '预警升'+TOP_LV
          ? '等级触顶站在最前面，比情绪触底早 '+n0(LOW_V.round-tRow.round)+
            ' 轮、比互动见顶早 '+n0(PEAK_INT.round-tRow.round)+
            ' 轮 —— 预警系统具备明显的领先量。'
          : '等级触顶并非四个时间点里最早的，说明这一段里预警指标与情绪、互动接近同步，前置量有限。')+
        '（情绪最低点取全场效价均值最低轮，负面占比与互动量取对应单轮峰值。）'));
    return;
  }

  var redFirst = AROW(AL.first_round['红色']);
  /* ★ 2026-10-13 第十七轮：全部现算。改版前那句「自该轮起持续 N 轮直达推演结束，未见回落」
     在**累计量口径**下是必然结果（三项观测量全单调 ⇒ S 只升不降）；改成 5 轮窗口后 S 有升有降，
     那句话已不成立。现在能退就写退、不能退才写不能退。原先誊写式的句子同样不再出现。 */
  var prevRow = AROW(Math.max(R0, redFirst.round - 1)), p16 = redFirst;
  var dS = redFirst.score - prevRow.score;
  var comp = [
    {n:'传播广度（G1）', v:(redFirst.G1 - prevRow.G1)*AL.weights.G1},
    {n:'情绪态势（G2）', v:(redFirst.G2 - prevRow.G2)*AL.weights.G2},
    {n:'参与强度（G3）', v:(redFirst.G3 - prevRow.G3)*AL.weights.G3}
  ].sort(function(a,b){ return b.v - a.v; });
  var leadTxt = comp[0].n.indexOf('G1')>0
      ? '说明决定预警升级的是这一段里传播面在扩张。'
      : (comp[0].n.indexOf('G2')>0
         ? '说明这一次跨过红线的不是传播面扩张，而是整体情绪本身的继续恶化。'
         : '说明这一次跨过红线的是这一段里参与强度的抬升（发声人数、动作强度加权互动与新生帖介数峰值同步上台阶）。');
  var ceAtRed = CE.filter(function(e){ return e.round===redFirst.round; });
  var hToRed = (wsec(redFirst.window[0]) - wsec(META.t0)) / 3600e3;
  /* 红色是否连续、退没退下来、最高峰在哪轮 —— 全部现算 */
  var peakRow = AL.rows.slice().sort(function(a,b){ return b.score - a.score; })[0];
  var redLast = redRows[redRows.length-1];
  var redContig = redRows.every(function(a,i){
    return i===0 || a.round === redRows[i-1].round + 1; });
  var tail = AL.rows.filter(function(a){ return a.round > redLast.round; });
  /* ★ 2026-10-20 第二十八轮：三个等级并列时原文印出「橙色与黄色与蓝色」（EVT-012），
     改用「橙色、黄色与蓝色」。下面判「是否只有一个等级」也要跟着改判据。 */
  var tailLvArr = tail.map(function(a){ return a.level; })
      .filter(function(v,i,arr){ return arr.indexOf(v) === i; });
  var tailLv = tailLvArr.slice(0, -1).join('、') +
               (tailLvArr.length > 1 ? '与' : '') + (tailLvArr[tailLvArr.length - 1] || '');
  /* 四个时间点按先后排开 —— 结论句由排列本身决定，不预先断言 */
  var seq = [
    {n:'预警升红', r:redFirst.round},
    {n:'情绪最低点', r:LOW_V.round},
    {n:'负面占比峰值', r:PEAK_NEG.round},
    {n:'互动量峰值', r:PEAK_INT.round}
  ].sort(function(a,b){ return a.r - b.r; });
  setHTML('r43',
    p('本次事件在推演区间内由'+P_ASC[0]+'（轮 '+AL.first_round[P_ASC[0]]+'，'+AL.rows[0].window[0]+'）'+
      /* ★ 2026-10-20 第三十一轮：中间等级原先按**严重度**顺序（黄→橙）机械拼接 ——
         EVT-001 的实际到达顺序是 橙（轮 1）→ 红（轮 4）→ 黄（轮 6），原句却印成
         「经黄色（轮 6）与橙色（轮 1）升至红色（轮 4）」，时间顺序当场颠倒。
         改为按首次到达轮次排序；单调上升的老跑次顺序不变 ⇒ 逐字不变。 */
      (function(){
        var mid = P_ASC.slice(1, P_ASC.length - 1).filter(function(L){
          /* 首次到达晚于红色首现的等级不属于「升至红色」的路径（EVT-001 的黄色在轮 6，
             即红色退下来之后才首次出现），剔掉；只留红色之前真正经过的等级。 */
          return (AL.first_round[L]||0) <= redFirst.round;
        }).sort(function(a,b){
          return (AL.first_round[a]||0) - (AL.first_round[b]||0); });
        return mid.length
          ? '经'+mid.map(function(L){ return L+'（轮 '+AL.first_round[L]+'）'; }).join('与')
          : '直接';
      })()+
      '升至<b class="k">红色</b>，'+
      '红色首次触发于<b class="k">轮 '+redFirst.round+'（'+redFirst.window[0]+'）</b>，'+
      /* ★ 2026-10-14 第二十二轮：原句为「与改版前最大的不同是红色退得下来」——「改版前」是
         报告层分级口径的一次内部修订，读者拿不到那个版本，无从对照，故不再引用它。 */
      '即 t0 后约 '+n0(hToRed)+' 小时。<b>红色退得下来</b>：全程红色共 '+
      redRows.length+' 轮（占 '+(redRows.length/AL.rows.length*100).toFixed(1)+'%），'+
      '最高总分落在轮 '+peakRow.round+'（'+peakRow.window[0]+'，S = '+peakRow.score.toFixed(4)+
      '，'+peakRow.level+'）。'+
      (redContig ? '' : '红色出现于轮 '+
        redRows.map(function(x){ return x.round; }).join('、')+'，中间有回落，不是一路维持；')+
      /* ★ 第二十八轮：原先无条件写「一直在 X 之间」——`tailLv` 只有一个等级时印出
         「此后 4 轮一直在橙色之间」（EVT-008），不是一句话。 */
      (tail.length
        ? '自轮 '+redLast.round+' 之后再未回到红色，此后 '+tail.length+' 轮一直'+
          (tailLv.indexOf('与') < 0 ? '是'+tailLv : '在'+tailLv+'之间')+'。'
        : '自轮 '+redLast.round+' 起红色持续到推演结束，未再回落。')) +
    p('升到红色的直接触发项是<b>'+comp[0].n+'</b>。轮 '+prevRow.round+' 至轮 '+redFirst.round+
      ' 的风险总分由 '+prevRow.score.toFixed(4)+
      ' 升至 '+p16.score.toFixed(4)+'，上升 '+dS.toFixed(4)+
      '，三项贡献依次为'+comp.map(function(c){ return c.n+' '+c.v.toFixed(4); }).join('、')+
      '（该轮新增跨渠道传播 '+ceAtRed.length+' 次'+
      (ceAtRed.length ? '：'+redFirst.window[0]+' 由 '+ceAtRed.map(function(e){
        return e.from.join('+')+' → '+e.to.join('+'); }).join('、') : '')+
      '）；该轮窗口内发声人数 '+redFirst.w_nag+'、动作强度加权互动 '+redFirst.w_actw+
      '、渠道加权帖量 '+redFirst.w_postw+'，归一值（该量 / 全程窗口最大）分别为 '+
      redFirst.o3_num.toFixed(3)+' / '+redFirst.o3_act.toFixed(3)+' / '+
      redFirst.o1_postw.toFixed(3)+'，'+
      leadTxt) +
    p('与情绪曲线的对应关系：把四个时间点按先后排开 —— '+
      seq.map(function(x){ return x.n+'（轮 '+x.r+'）'; }).join(' → ')+
      '。'+(seq[0].n==='预警升红'
        ? '预警升级站在最前面，比情绪触底早 '+n0(LOW_V.round-redFirst.round)+' 轮、比互动见顶早 '+
          n0(PEAK_INT.round-redFirst.round)+' 轮 —— 预警系统具备明显的领先量。'
        : '预警升级并非四个时间点里最早的，说明这一段里预警指标与情绪、互动接近同步，前置量有限。')+
      '（情绪最低点取全场效价均值最低轮，负面占比与互动量取对应单轮峰值。）'));
});

/* ================= 模块五 · 事件类型应对主线 =================
   ★ 2026-10-21 第三十二轮（仿真侧指令）：模块五原先 9 条建议**全部由观测值触发**，
     每条都在说「监测谁、监测多久、资源怎么配」，没有一条说「**这件事本身该怎么了结**」。
     EVT-001 是营销类事件，它的终局不是监管介入，而是**涉事企业公开道歉、下线素材、
     终止与投放方合作** —— 这件事在仿真里演示不出来（引擎动作集只有
     引用 / 转发 / 评论 / 点赞 / 沉默 + 投诉，没有「官方回应」），只能由建议库给。

   所以这里加一张**纯领域知识**的表：按采集侧 8 类事件类型，各给一条「处置主线」。
   它不参与任何现算，只按 `event_type` 查表；类型不在表里 ⇒ 不出主线（不编）。
   8 类与引擎 `COMPLAINT_POLICY_BY_TYPE`（t1_runner.py:96-105）**同一套**，
   不另造分类 —— 两套分类打架比没有分类更糟。 */
var TYPE_PLAYBOOK = {
  '理赔类': {
    name: '个案先行赔付 + 公布理赔时效口径',
    who: '理赔条线负责人 / 客服中心',
    why: '理赔类舆情的争点是「该赔不赔」与「拖多久」。公开解释条款只会把争点抬到合同解释层面，'
       + '先赔个案、再公布时效，才能把争点从「是不是赖账」拉回「怎么赔」。',
    steps: ['对曝光个案先行赔付并公示结果（不附加删帖条件）',
            '公布该类案件的平均结案时效与超期占比',
            '把「提交材料清单」前置到报案页，减少二次往返'],
    kpi: '同类投诉的二次曝光在 2 轮内不再新增；客服进线中「拒赔」关键词占比下降'
  },
  '销售与投保类': {
    name: '暂停相关销售话术 + 回溯已售保单',
    who: '销售管理部 / 合规部',
    why: '销售环节的争议一旦扩散，公众关心的是「还有多少人被同样话术卖过」，'
       + '只回应个案会被继续追问覆盖范围。',
    steps: ['暂停涉事话术与素材，全渠道下线',
            '回溯同批次话术覆盖的保单，主动告知并给出退保 / 改保选项',
            '公布销售人员话术培训与抽检机制'],
    kpi: '退保咨询量在 3 轮内见顶回落；回溯名单覆盖率 100% 并有公示'
  },
  '产品与费率类': {
    name: '公布费率构成 + 向监管报备',
    who: '精算 / 产品部 + 政府事务',
    why: '费率争议的实质是「凭什么这么贵」。没有构成说明就只能被动挨打，'
       + '主动报备可以把「被查」变成「已报」。',
    steps: ['公布费率构成与近三年调整记录',
            '向属地监管报备调整依据与消费者告知方案',
            '对存量客户给出费率切换的过渡期'],
    kpi: '「乱收费」类发帖占比在 3 轮内下降；监管问询次数不增加'
  },
  '服务态度类': {
    name: '涉事网点整改 + 一对一回复',
    who: '区域服务负责人 / 客服中心',
    why: '服务态度类事件的情绪强度来自「被冒犯感」，公关口径的统一回复会加重这种感觉，'
       + '必须由具体网点对具体人回复。',
    steps: ['对曝光当事人一对一回复并公开处理结果',
            '公布涉事网点 / 客服的整改与培训安排',
            '开放该类投诉的直达通道，避免二次发酵'],
    kpi: '同类投诉的重复发帖归零；当事人公开发帖转为中性或撤回'
  },
  '营销类': {
    name: '公开致歉 + 下线问题素材 + 终止与投放方合作',
    who: '涉事企业公关部 + 合规部',
    why: '营销类舆情的争点不是「赔不赔」，而是「**态度**」——公众要的是承认冒犯，'
       + '而不是解释流程。把责任推给第三方会被读成「甩锅」，反而延长事件。',
    steps: ['发布公开致歉（先承认冒犯，不先解释流程）',
            '全渠道下线问题素材，并公示下线时间',
            '终止与投放方 / 第三方的合作，公布问责结果',
            '公布素材审核流程的整改项（含人工复核与 AI 审核）'],
    kpi: '致歉发布后 3 轮内群体效价止跌；红色不再二次触发；「甩锅」类质疑帖占比下降'
  },
  '公司经营类': {
    name: '统一信息披露口径，不做舆论对冲',
    who: '董办 / 投资者关系 + 公关部',
    why: '经营类事件的核心风险是信息不对称引发的猜测，任何「对冲式」表述都会被读成掩饰。',
    steps: ['以公告 / 定期报告口径统一对外表述',
            '对不实传言逐条书面澄清（不评价动机）',
            '停止一切营销投放直至事件平息'],
    kpi: '传言类发帖不再出现新的事实性主张；合作方问询不再新增'
  },
  '司法与维权类': {
    name: '以司法结论为准，不提前定性',
    who: '法务部 + 公关部',
    why: '进入司法程序的争议，任何前置定性都会成为后续庭审的不利材料；'
       + '此类的正确动作是「少说、说准」。',
    steps: ['对外只表述程序进展，不评价是非',
            '对已生效判决主动履行并公示',
            '同步准备不利结果下的补救方案'],
    kpi: '不出现与司法结论冲突的公开表述；履行完毕有公示'
  },
  '数据与隐私类': {
    name: '停止数据使用 + 用户告知 + 监管报备',
    who: '数据保护负责人（DPO）+ 法务',
    why: '数据类事件的法律风险高于舆论风险，先止损再解释；'
       + '用户告知本身也是免责要件。',
    steps: ['立即停止涉事数据的收集与使用',
            '向受影响用户逐一告知范围与补救措施',
            '向网信 / 行业主管部门报备'],
    kpi: '受影响用户告知覆盖率 100%；监管处罚风险评级不上升'
  }
};
/* 这三个是**全局**（模块五与摘要都要用，摘要段在另一个 SEC 闭包里拿不到模块五的局部变量）。 */
var EVT_TYPE  = String((META && META.event_type) || '').trim();
var EVT_SUBJ  = String((META && META.subject) || '').trim();
var EVT_PLAY  = TYPE_PLAYBOOK[EVT_TYPE] || null;

/* ================= 模块五 ================= */
SEC(2540, function(){
  var redFirst = AROW(AL.first_round['红色']);
  /* ★ 2026-10-21 第三十二轮：模块五现在第一条是**类型主线**（领域预案，不是读数），
     原句「下列建议均由上述三项结果中的具体观测值触发」会把它说成读数的产物。两支共用。 */
  var ADV_LEAD = (EVT_PLAY
    ? '模块五先给出一条按本事件类型查表的<b>处置主线</b>（领域预案，不来自上述读数），'+
      '再给由上述三项结果中的具体观测值触发的分项建议，每条均标注触发它的证据及其在报告中的位置，可逐条回溯。'
    : '下列建议均由上述三项结果中的具体观测值触发，'+
      '每条均标注触发它的证据及其在报告中的位置，可逐条回溯。');
  setHTML('r50',
    (HAS_RED
     ? '生成口径：本事件类型为<b>__EVENT_TYPE__</b>。'+
       '本报告的红色预警自<b class="k">轮 '+redFirst.round+'（'+redFirst.window[0]+'）</b>起生效——'+
       '即 t0 后约 '+n0((wsec(redFirst.window[0]) - wsec(META.t0))/3600e3)+' 小时，事件才到达红色级别。'+
       /* ★ 2026-10-20 第三十轮：原先一律写「断续分布在轮 N」——只有 1 轮红色时
          （EVT-001 的轮 5）「断续」二字无从谈起。改为按连续段数分两支。 */
       (function(){
         var rr = AL.rows.filter(function(x){ return x.level==='红色'; })
                          .map(function(x){ return x.round; });
         var runs = 0; rr.forEach(function(r2,i){ if(i === 0 || r2 !== rr[i-1]+1) runs++; });
         return '红色全程共出现 '+rr.length+' 轮，'+
           (runs > 1 ? '断续分布在轮 '+rr.join('、') : '集中在轮 '+rr.join('、'))+
           '，并非一路维持到推演结束。';
       })()+
       ADV_LEAD
     : '生成口径：本事件类型为<b>__EVENT_TYPE__</b>。'+
       '本报告的预警等级全程最高只到<b class="k">'+TOP_LV+'</b>，自<b class="k">轮 '+
       TOP_ROW.round+'（'+TOP_ROW.window[0]+'）</b>首次出现——'+
       '即 t0 后约 '+n0(H_TO_TOP)+' 小时。全程<b>未触及红色阈值</b>（S ≥ '+
       AL.cut['红色'].toFixed(2)+'；本跑最高 S = '+AL.peak.score.toFixed(4)+
       '，出现在轮 '+AL.peak.round+'，距阈值还差 '+
       (AL.cut['红色'] - AL.peak.score).toFixed(4)+'）。'+TOP_LV+'全程共出现 '+TOP_N+
       ' 轮，出现在轮 '+AL.rows.filter(function(x){ return x.level===TOP_LV; })
         .map(function(x){ return x.round; }).join('、')+
       '，并非一路维持到推演结束。'+ADV_LEAD));

  var low = LOW_V;
  /* ★ 2026-10-21 第三十二轮：类型 / 涉事主体 / 主线（见文件上方 TYPE_PLAYBOOK） */
  var ET    = EVT_TYPE;
  var SUBJ  = EVT_SUBJ;
  var PLAY  = EVT_PLAY;
  /* ★ 红色有两种成因：广度型（G1 主导）与强度型（G3 主导）。原来一律写「G1 + G2 双升」，
     与数据对不上 —— EVT-001 轮 5 由 G1 = 0.920 主导，轮 17（峰值 0.8375）由 G3 = 0.963
     主导，且那一轮 G1 反而从 1.000 降到 0.730。这里按主导项把红色轮分组。 */
  var RED_PATHS = (function(){
    var rows = AL.rows.filter(function(x){ return x.level === '红色'; });
    if(!rows.length) return null;
    function lead(r){ return (r.G1 >= r.G2 && r.G1 >= r.G3) ? 'G1'
                            : (r.G2 >= r.G3 ? 'G2' : 'G3'); }
    var by = {};
    rows.forEach(function(r){ var k = lead(r); (by[k] = by[k] || []).push(r); });
    var order = Object.keys(by).sort(function(a, b){ return by[b].length - by[a].length; });
    return {rows: rows, by: by, order: order, multi: order.length > 1};
  })();
  /* ★ 跨渠道传播 ≤3 起时，建议 2/3/4 讲的是同一件事，合并成一条。 */
  var CE_SMALL = (!NO_CE && CE.length <= 3);
  /* ★ 2026-10-14 第二十二轮：adv[0] 原先无条件写「三个后续信号都晚于红色触发」。三个参照信号
     （情绪最低点 / 负面占比峰值 / 互动量峰值）与首红轮的先后是数据决定的，不是文案决定的 ——
     EVT-001 恰好三个都晚，这句话恒真才没显形；换事件就可能出现早于首红轮的信号。
     非红色事件上 redFirst 为 undefined，故用三元惰性求值兜住。 */
  var AFTR_N = HAS_RED ? [LOW_V.round, PEAK_NEG.round, PEAK_INT.round].filter(function(r){
    return r > redFirst.round; }).length : -1;
  /* ★ R-50 后：下列建议原先把「轮 5 / 10 小时 / 4 次 / 5 次 / 轮 11 至轮 17」等
     **上一次运行的观测值誊写进了文案**，换轮次表后不会报错、只会印错。以下全部现算。 */
  /* ★ 2026-10-15 第二十六轮：`CE[0]` 在零转载时是 undefined ⇒ 这里**抛异常**，
     整块建议框（含其后的 8 条建议）就此不再渲染（EVT-011 实测：advBox 全程未被写入）。 */
  var hCE0 = NO_CE ? null : (wsec(CE[0].window[0]) - wsec(META.t0)) / 3600e3;
  /* ★ 第二十八轮：监测窗口的上界原先是 `ceil(hCE0/4)*4*3`（首次跨渠道时刻的 3 倍）——
     这个「3 倍」没有任何数据依据，EVT-008 印出「覆盖 0—36 小时」，而末次转载在轮 36（= 144 小时）。
     改为按**末次跨渠道所在的轮**取整到轮边界。 */
  var hCE1 = NO_CE ? null : Math.ceil(((wsec(CE[CE.length-1].window[0]) - wsec(META.t0)) / 3600e3 + 1e-9) / 4) * 4;
  var arrN = {}, depN = {};
  CE.forEach(function(e){
    e.to.forEach(function(c){ arrN[c] = (arrN[c]||0) + 1; });
    depN[e.origin] = (depN[e.origin]||0) + 1;
  });
  var arrTop = Object.keys(arrN).sort(function(a,b){ return arrN[b] - arrN[a]; })[0];
  var depTop = Object.keys(depN).sort(function(a,b){ return depN[b] - depN[a]; })[0];
  var ceRoundSpan = Object.keys(CE.reduce(function(m,e){ m[e.round]=1; return m; }, {})).length;
  /* 最密集的 12 小时窗口（按各起事件的起始钟点算） */
  /* ★ 2026-10-15 第二十六轮：初值原先取 `CE[0].round`，零转载时同样是 undefined 取值 ⇒ 抛异常。
     非空时下面的循环第一圈必然覆盖它（n≥1 > 0），故改成 null 对既有各场输出**逐位无影响**。 */
  var best12 = {n:0, lo:null, hi:null};
  CE.forEach(function(e, i){
    var j = i, n = 0;
    while(j < CE.length && (wsec(CE[j].window[0]) - wsec(e.window[0])) < 12*3600e3){ n++; j++; }
    if(n > best12.n){ best12 = {n:n, lo:CE[i].round, hi:CE[j-1].round}; }
  });
  /* ★ 2026-10-14 第二十二轮：建议 6 / 8 / 9 原先把角色写死为 客户 / 媒体 / KOL / 监管 ——
     那是 EVT-001 的构成。EVT-005 上「客户」只发了 2 条帖（占 5.3%），却仍被印成
     「客户群体按**高频发帖** + 零被引筛出长尾清单」；建议 6 里 KOL 的情绪低点是 **+0.007**（正数），
     却写成「避免客户与 KOL 在信息真空期自行沉底」。改为按载荷现算三类角色：
       ADV6.tone 定调方＝每帖被引最高的在场角色；ADV6.win 窗口截止方＝情绪见底最晚的**另一个**角色；
       LONGTAIL_ROLE 长尾方＝全程零被引账号最多的角色。 */
  var ADV6 = (function(){
    var has = ROLES.filter(function(r){ return ROof(r).posts > 0; });
    if(!has.length) return null;
    /* ★ 2026-10-21 第三十二轮：**定调方必须有最小样本**。
       原式直接取「每帖被引最高」的角色 —— EVT-001 于是选中网民（每帖被引 2.25），
       但 147 个网民账号全程只发了 4 条帖、介数合计 0。拿 4 条帖的代表去「定调」，
       是小样本假象被写成了结论。改为：帖数 ≥ TONE_MIN 才参与，不足则退回全集。 */
    var TONE_MIN = 5;
    var solid = has.filter(function(r){ return ROof(r).posts >= TONE_MIN; });
    var pool  = solid.length ? solid : has;
    var tone = pool.slice().sort(function(a,b){
      return ROof(b).out_per_post - ROof(a).out_per_post; })[0];
    /* ★ 2026-10-14 第二十二轮：此处原先只按「见底轮最晚」排序。EVT-001 的客户与网民都在末轮
       见底（轮 41），并列时顺序不定，会把「截止方」从客户（-0.636）换成网民（-0.423）——
       与原意不符。并列时再按「跌得更深」决出先后。 */
    var order = has.slice().sort(function(a,b){
      var ra = minOf(RM(a)).round, rb = minOf(RM(b)).round;
      if(ra !== rb) return rb - ra;
      return minOf(RM(a)).v - minOf(RM(b)).v; });
    var win = order.filter(function(r){ return r !== tone; })[0] || tone;
    return {tone:tone, win:win, toneLow:minOf(RM(tone)), winLow:minOf(RM(win)),
            tonePosts:ROof(tone).posts, min:TONE_MIN,
            thin:has.filter(function(r){ return ROof(r).posts < TONE_MIN; })};
  })();
  /* ★ 2026-10-14 第二十二轮：长尾角色原先取「零被引账号数最多」的角色 —— EVT-001 会选中网民
     （54 个从不被引的账号，但全程只发 17 条帖，与「发帖集中、被引为零」这句标题不符）。
     标题与建议说的是「发帖不少、却没人引」的那一类，故改为取**零被引帖子数最多**的角色；
     并列时取发帖量大的。这样 EVT-001 回到客户（60 条帖里大量零被引），EVT-005 仍取客户。
     另附 `LT_ZP`（该角色零被引帖数），供建议正文使用。 */
  var LT_ZP = 0;
  var LONGTAIL_ROLE = (function(){
    var best = null;
    ROLES.forEach(function(r){
      if(ROof(r).posts <= 0) return;
      var zp = POSTS.filter(function(p){ return p.role === r && !p.indeg; }).length;
      if(!best || zp > best.zp || (zp === best.zp && ROof(r).posts > best.posts)){
        best = {r:r, zp:zp, posts:ROof(r).posts};
      }
    });
    if(!best) return ROLES[0] || null;
    LT_ZP = best.zp;
    return best.r;
  })();
  var g3Adv = (function(){
    var mx = Math.max.apply(null, AL.rows.map(function(x){ return x.G3; }));
    var hit = AL.rows.filter(function(x){ return x.G3 >= mx - 1e-9; });
    return {max:mx, first:hit[0].round, last:hit[hit.length-1].round,
            persist:(hit[hit.length-1].round === AL.rows[AL.rows.length-1].round)};
  })();
  var adv = [
    (HAS_RED
     ? {t:'预警在轮 '+redFirst.round+'（'+redFirst.window[0]+'）升级为红色，处置预案应在此前激活',
        b:'红色预警首次触发于<b>轮 '+redFirst.round+'（'+redFirst.window[0]+'）</b>，该轮情绪态势 G2 已达 '+
          redFirst.G2.toFixed(3)+'；同期情绪最低点在轮 '+low.round+'（'+f3(low.valence)+
          '）、负面占比峰值在轮 '+PEAK_NEG.round+'、互动量峰值在轮 '+PEAK_INT.round+'，'+
          (AFTR_N === 3 ? '三个后续信号都晚于红色触发。'
            : (AFTR_N === 0 ? '三个参照信号全部早于红色触发，预警升级是滞后确认而非领先信号。'
               : CN_NUM(AFTR_N)+'个参照信号晚于红色触发，另'+CN_NUM(3-AFTR_N)+'个早于它。'))+
          /* ★ 2026-10-21 第三十二轮：红色不止一种成因。原句一律写「G1 + G2 双升」，
             与数据对不上：EVT-001 轮 17（峰值）由 G3 主导、G1 反而下降。 */
          (RED_PATHS
            ? (RED_PATHS.multi
                ? '<b>但红色的成因不止一种</b>：'+RED_PATHS.order.map(function(k){
                    var rs = RED_PATHS.by[k];
                    return k+' 主导 '+rs.length+' 轮（'+rs.map(function(r){
                      return '轮 '+r.round+'：'+k+' '+r[k].toFixed(3); }).join('、')+'）';
                  }).join('；')+' —— 成因不同，用同一套判据会漏掉后一种。'
                : '全程红色均由 <b>'+RED_PATHS.order[0]+'</b> 主导。')
            : ''),
        who:'舆情应急负责人（值班指挥）',
        /* ★ 2026-10-21 第三十二轮：原先多减了一轮（`- 4`）——「轮 4 结束前」写成了
           t0+16h，而轮 4 的窗口是 t0+16h → t0+20h，结束于 t0+20h。去掉多余的减号。 */
        when:'轮 '+(redFirst.round-1)+' 结束前（t0 后约 '+
             n0((wsec(redFirst.window[0]) - wsec(META.t0))/3600e3)+' 小时）完成预案启动',
        what:(RED_PATHS && RED_PATHS.multi
          ? RED_PATHS.order.map(function(k){
              var mx = RED_PATHS.by[k].reduce(function(m, r){ return Math.max(m, r[k]); }, 0);
              return k+' 主导型：'+k+' 单轮 ≥ '+mx.toFixed(2)+
                     '（本跑实测峰值）⇒ 启动红色预案';
            }).concat(['两套判据同时挂牌，命中任一条即启动；阈值取自本跑读数，下次复盘校准'])
          : [(RED_PATHS ? RED_PATHS.order[0]+' 单轮 ≥ '+
               RED_PATHS.rows.reduce(function(m, r){
                 return Math.max(m, r[RED_PATHS.order[0]]); }, 0).toFixed(2)+' ⇒ 启动红色预案'
             : 'S ≥ '+AL.cut['红色'].toFixed(2)+' ⇒ 启动红色预案'),
             '阈值取自本跑实测读数，下次复盘校准']),
        kpi:'红色首次触发不晚于轮 '+redFirst.round+'；红色总轮数不超过 '+
            (RED_PATHS ? RED_PATHS.rows.length : 0)+' 轮',
        pos:'模块四 图 4-1 / 表 4-3'}
     : {t:'全程未触发红色，响应级别按「'+TOP_LV+'」配置即可',
        b:'全程最高只到<b>'+TOP_LV+'</b>，自<b>轮 '+TOP_ROW.round+'（'+TOP_ROW.window[0]+'）</b>首次出现；'+
          '最高总分 S = '+AL.peak.score.toFixed(4)+'（轮 '+AL.peak.round+'），未达红色阈值 '+
          AL.cut['红色'].toFixed(2)+'，仍差 '+(AL.cut['红色'] - AL.peak.score).toFixed(4)+
          '。同期情绪最低点在轮 '+low.round+'（'+f3(low.valence)+
          '）、负面占比峰值在轮 '+PEAK_NEG.round+'、互动量峰值在轮 '+PEAK_INT.round+'。',
        who:'舆情应急负责人（值班指挥）',
        when:'全程（无红色轮次）',
        what:['按 '+TOP_LV+' 级配置响应资源，不启动红色预案',
              '把「S 距红色阈值的差值」列为逐轮观测量，差值收窄即升级关注'],
        kpi:'全程 S 不超过 '+AL.cut['红色'].toFixed(2)+'；无新增红色轮次',
        pos:'模块四 图 4-1 / 表 4-3'}),
    /* ★ 2026-10-15 第二十六轮：下面**三条**建议（本条的标题/依据/动作）主语全是**转载**造成的
       跨渠道事件。零转载事件上它们要么为假（「全部 0 次跨渠道传播均由发起」、`CE_ROLE_STR` 是空串），
       要么直接抛异常（`CE[0].round` / `CO[undefined].posts`）—— 后者会掐断整个 advBox，
       使排在它们后面的六条建议一并消失（EVT-011 实测 advBox 全程未被写入）。
       ⇒ NO_CE 下三条整体不生成，换成紧随其后那条按「跨渠道引用」写的口径修正建议。 */
    (NO_CE ? null :
     CE_SMALL ?
     /* ★ 2026-10-21 第三十二轮：跨渠道传播 ≤3 起时，原先的 2/3/4 三条讲的是**同一件事**。
        单起事件上等于把一句话拆成三段，还各自用「建名单 / 排窗口」的口吻，
        把 1 个样本说成了一条搬运链。改为合并成一条，并把「不必建名单」本身作为结论。 */
     {t:'本跑仅 '+CE.length+' 起跨渠道传播（'+CE.map(function(e){
          return e.from.join('+')+' → '+e.to.join('+'); }).join('、')+
        '），按单起事件处置即可，不必建监测名单',
      b:'首次跨渠道在<b>轮 '+CE[0].round+' / '+CE[0].window[0]+'</b>（t0 后约 '+n0(hCE0)+' 小时），由'+
        CE[0].role+'帖 '+CE[0].post+' 从'+CE[0].origin+'进入'+CE[0].to.join('与')+'；'+
        '全程共 '+CE.length+' 起、只涉及 '+CE_AG_N+' 个账号（'+CE_ROLE_AG_STR+'），'+
        '末起在轮 '+CE[CE.length-1].round+'（t0 后约 '+n0(hCE1)+' 小时）；'+
        '<b>样本只有 '+CE.length+' 起</b>，它说明的是「这类内容有被跨平台搬运的可能」，'+
        '不足以支撑「已经形成搬运链条」的判断。',
      who:'平台内容运营 + 品牌监测岗',
      when:'轮 '+CE[0].round+' 至轮 '+CE[CE.length-1].round+'（t0 后 '+n0(hCE0)+'—'+n0(hCE1)+' 小时）',
      what:['对涉及的 '+CE_AG_N+' 个账号主页做一次留存取证（截图 + 链接）',
            '把「落地渠道数是否继续增长」作为唯一复核判据：增长即核验，不增长不处置',
            '不新增常设监测名单 —— 本跑样本不支持'],
      kpi:'跨渠道传播起数不再增长；无新增落地渠道',
      pos:'模块一 表 1-3'}
     :
    {t:'跨渠道管控只需锁定 '+CE_AG_N+' 个账号（'+CE_ROLE_AG_STR+'）',
     b:'全部 <b>'+CE.length+' 次跨渠道传播均由'+CE_ROLE_STR+'发起</b>（'+CE_ROLE_EV_STR+'）；'+
       '客户与网民帖占全部帖子的 '+pct((ROof('客户').posts+ROof('网民').posts)/META.posts)+
       (CE_NO_CN ? '，却从未成为跨渠道传播的载体' : '，其中一部分同时也是跨渠道传播的载体')+
       '。涉及的具体账号只有 '+CE_AG_N+' 个（'+CE_ROLE_AG_STR+'）。',
     who:'平台内容运营 + 品牌监测岗',
     when:'轮 '+CE[0].round+' 起持续至推演结束（每轮复核一次）',
     what:['建立跨平台转载监测名单，按帖子 ID 逐一跟踪其落地渠道数变化',
           '落地渠道数一旦增长即触发核验，不增长不处置',
           '名单只覆盖这 '+CE_AG_N+' 个发起账号，不必对全部 '+META.agents+' 个智能体做等量监测'],
     kpi:'跨渠道传播起数不再增长；名单外账号零新增转载',
     pos:'模块一 表 1-3'}),
    (NO_CE || CE_SMALL ? null :
    {t:'首次跨渠道发生在 t0 后 '+n0(hCE0)+' 小时，监测窗口须在此前到位',
     b:'首次跨渠道在<b>轮 '+CE[0].round+' / '+CE[0].window[0]+'</b> 发生，即 t0 后约 '+n0(hCE0)+' 小时，由'+CE[0].role+'帖 '+CE[0].post+
       ' 从'+CE[0].origin+'进入'+CE[0].to.join('与')+'；'+
       (best12.n > 1
         ? (best12.lo === best12.hi
            ? '最密集的一段集中在轮 '+best12.lo+' 一轮之内，12 小时内共 '+best12.n+' 起。'
            : '全程 '+CE.length+' 起里最密集的一段是轮 '+best12.lo+' 至轮 '+best12.hi+' 的 12 小时内共 '+best12.n+' 起。')
         : '全程 '+CE.length+' 起分散在 '+ceRoundSpan+' 个不同轮次，<b>没有出现 12 小时内 ≥2 起的密集窗口</b>。'),
     who:'平台内容运营岗',
     when:'t0 后 0—'+n0(hCE1)+' 小时（首起在前，末起所在轮收口）',
     what:['以一轮（'+n0(spanOf(S[0]))+' 小时）为颗粒度比对各 t0 原帖的落地渠道数',
           '首次跨渠道之前到位，覆盖到末次跨渠道所在的轮为止',
           '窗口结束后不再逐轮比对，转入常规监测'],
     kpi:'首次跨渠道发生时已在监测窗口内；无遗漏的落地渠道增长',
     pos:'模块一 表 1-3'}),
    /* ★ 2026-10-15 第二十六轮：本条标题里的 `arrTop` = 「被到达次数最多的渠道」，零转载时
       `arrN` 为空 ⇒ 它是 undefined，`CO[arrTop].posts` 当场抛异常。NO_CE 下换成
       按「跨渠道引用」提的口径修正建议。 */
    (NO_CE
      ? {t:'本跑跨平台流动全部由 '+QX.length+' 条跨渠道引用完成，监测口径须从「渠道边」换成「引用账号」',
         b:META.rounds+' 轮逐轮记录里引擎自记的 <span class="mono">new_cross_channel_edges</span> 合计为 <b>0</b>'+
           ' —— 没有任何一条原帖的落地渠道集合发生过增长，因此<b>按「渠道边是否增长」布点会整场落空</b>；'+
           '但另起一帖、落到引用者自己渠道的<b>跨渠道引用</b>有 '+QX.length+' 条，'+
           '分布在 '+Object.keys(QX_BY_R).length+' 个轮次里，内容确实到过别的平台。',
         who:'平台内容运营 + 品牌监测岗',
         when:'全程；每轮复核一次引用来源',
         what:['把监测口径从「渠道边是否增长」换成「跨渠道引用的发起账号」',
               '按引用链接逐条复盘跨平台曝光的来源',
               '对反复发起引用的账号做重点盯防，不等渠道边增长'],
         kpi:'跨渠道引用来源可逐条回溯；无遗漏的跨平台曝光',
         pos:'模块一 表 1-3'}
      : (CE_SMALL ? null :
        {t:'把'+arrTop+'作为转载落地端监测，而非原创发声端',
         /* ★ 第二十八轮：「只有 N 条自产帖」的「只有」原是无条件写的 —— EVT-008 的落地端
            新闻门户全程 97 条自产帖，算不上少。改为按基数决定用不用「只」。 */
         b:arrTop+'全程'+(CO[arrTop].posts < 20 ? '只有 ' : '有 ')+CO[arrTop].posts+' 条自产帖，却是<b>跨渠道传播中被到达次数'+
           (arrN[arrTop] > (arrN[Object.keys(arrN).sort(function(a,b){ return arrN[b]-arrN[a]; })[1]] || 0) ? '最多' : '并列最多')+
           '的渠道（'+arrN[arrTop]+' 次）</b>；'+
           /* ★ 2026-10-14 第二十一轮：原先把起点渠道写死为「微博」—— 换事件后（EVT-004）
              会印出「单条通道中最频繁的是「微博 → 微博」，共 0 次」这种既无意义也不成立的句子。
              改为在「终点为 arrTop」的转载里现取出现次数最多的那条通道。 */
           (function(){
             var cand = CE.filter(function(e){ return e.to.indexOf(arrTop) >= 0; });
             var pc = {};
             cand.forEach(function(e){ e.from.forEach(function(f){
               var k = f + ' → ' + arrTop; pc[k] = (pc[k] || 0) + 1; }); });
             var ks = Object.keys(pc).sort(function(a, b){ return pc[b] - pc[a]; });
             return ks.length ? '单条通道中最频繁的是「' + ks[0] + '」，共 ' + pc[ks[0]] + ' 次。' : '';
           })(),
         who:'品牌监测岗（对接媒体关系）',
         when:'全程；'+arrTop+'出现本事件相关转载即触发',
         what:['对'+arrTop+'按「是否出现本事件相关转载」设单点监测',
               '出现即视为事件已进入大众媒体视野，同步升级对外口径与响应级别',
               '不把'+arrTop+'当作原创发声端做资源投放'],
         kpi:'进入大众媒体视野的时点可被识别；升级口径不滞后于落地',
         pos:'模块一 表 1-3'})),
    /* ★ 2026-10-14 第二十一轮：这条建议原先通篇写死「抖音」——它是 EVT-001 里每帖被引最高的
       渠道，而换事件后最高的可能是任何一个（EVT-004 是微博，抖音反而垫底），
       写死会印出与 3.3 表格直接冲突的结论。现改为取 `PP_TOP` / `PP_POSTS` 现算。 */
    {t:PP_TOP+(PP_LEAN ? '内容少但转化强' : '的发帖量并不小，但每帖被引仍是全池最高')+
       '，监测资源应按「每帖被引」而非发帖量分配',
     b:PP_TOP+(PP_LEAN
         ? '仅 '+COof(PP_TOP).posts+' 条帖，却获得 '
         : '共 '+COof(PP_TOP).posts+' 条帖（不低于有内容产出渠道的平均发帖量 '+
           (META.posts/Math.max(1, PP_POSTS.length)).toFixed(1)+' 条），获得 ')+
       COof(PP_TOP).out+' 次引用，<b>每帖被引 '+COof(PP_TOP).out_per_post.toFixed(2)+' 次</b>，'+
       (PP_POSTS.length < 2
         ? '为唯一有内容产出的渠道；'
         : '为'+CN_NUM(PP_POSTS.length)+'渠道之首（第二位'+PP_POSTS[1]+' '+COof(PP_POSTS[1]).out_per_post.toFixed(2)+'）；')+
       /* ★ 2026-10-15 第二十六轮：零转载时 `depTop` 是 undefined，原先会印出
          「起点全部集中在undefined，共 undefined 次」。 */
       (NO_CE
         ? '本跑没有任何跨渠道转载，'+PP_TOP+'作为落地端还是源头无法由渠道边判断 —— '+
           '按站内数据显示它被引 '+COof(PP_TOP).out+' 次、每帖 '+COof(PP_TOP).out_per_post.toFixed(2)+' 次。'
         : (depTop===PP_TOP
            ? '同时'+PP_TOP+'是 <b>'+depN[PP_TOP]+' 次跨渠道事件的起点渠道</b>，占全部 '+CE.length+' 次的 '+
              pct(depN[PP_TOP]/CE.length, 0)+'。'
            : ((arrN[PP_TOP]||0) > 0
               ? '它<b>不是</b>跨渠道扩散的起点渠道（起点集中在'+depTop+'，共 '+depN[depTop]+' 次），'+
                 '但作为落地端被到达 '+arrN[PP_TOP]+' 次。'
               /* ★ 2026-10-21 第三十二轮：原句用的是排中律 ——「不是起点 ⇒ 就是落地端」。
                  这是错的：EVT-001 只有 1 起跨渠道（小红书 → 微博），抖音既不是起点也不是终点，
                  它的 13 次被引全部发生在站内。建议 4 说微博是落地端、建议 5 说抖音是落地端，
                  同一个列表里两条建议各自宣布一个落地端。现按「是否出现在 ce.to 里」实判。 */
               : '不过本期它<b>既不是</b>跨渠道扩散的起点（起点集中在'+depTop+'，共 '+depN[depTop]+' 次），'+
                 '<b>也不是</b>任何一起跨渠道传播的落地端 —— 它的 '+COof(PP_TOP).out+
                 ' 次被引全部发生在站内。所以「每帖被引最高」说明的是<b>站内转化强</b>，'
                 +'不能据此把它当成跨平台扩散的枢纽。'))),
     who:'内容审核岗（'+PP_TOP+'渠道负责人）',
     when:'全程；'+(PP_LEAN ? '不因发帖量小而降级' : '与按发帖量配置并行，取每帖被引口径'),
     what:['对'+PP_TOP+'账号实行全量人工抽检（不抽样）',
           '抽检项：是否出现本事件相关表述、是否已进入大众媒体口径',
           (PP_LEAN ? '不因该渠道发帖量小而下调监测等级'
                    : '该渠道发帖量在全池并不小，「按发帖量配资源」与「按每帖被引配资源」本场不冲突，取后者')],
     kpi:PP_TOP+'的高被引帖在下一轮内被识别；无漏检的本事件相关表述',
     pos:'模块三 3.3 / 模块一 表 1-2'},
    (ADV6
      ? (function(){
          /* ★ 2026-10-20 第三十一轮：两者**同轮**见底时原式一律印「晚于」——
             EVT-001 客户与媒体的低点都在轮 0，报告却写「媒体的定调晚于它」，而正文同句
             里的轮次就是 0 与 0。改为三支：早于 / 晚于 / 同轮（并说明幅度谁更深）。 */
          var same = ADV6.toneLow.round === ADV6.winLow.round;
          var rel  = same ? null : (ADV6.toneLow.round < ADV6.winLow.round ? '早于' : '晚于');
          return {
            t:'情绪干预窗口以'+ADV6.win+'情绪见底（轮 '+ADV6.winLow.round+'）为截止，'+ADV6.tone+'的定调'+
              (same ? '与它同轮' : rel+'它'),
            b:ADV6.tone+'的情绪低点出现在轮 '+ADV6.toneLow.round+'（'+f3(ADV6.toneLow.v)+'），'+
              (same
                ? '<b>与'+ADV6.win+'同轮</b>（轮 '+ADV6.winLow.round+'，'+f3(ADV6.winLow.v)+'），幅度'+
                  (ADV6.toneLow.v > ADV6.winLow.v ? '更浅' : '更深')
                : '<b>'+rel+'</b>'+ADV6.win+'（轮 '+ADV6.winLow.round+'，'+f3(ADV6.winLow.v)+'）')+
              '；'+ADV6.tone+'每帖被引 '+
              ROof(ADV6.tone).out_per_post.toFixed(2)+' 次、介数合计 '+ROof(ADV6.tone).betw+
              /* ★ 2026-10-14 第二十二轮：原句「全场内容的主穿透力集中在它身上」是断言；
                 `tone` 的定义就是「每帖被引最高的在场角色」，改为把这一定义写出（可复核）。 */
              '，为在场角色中每帖被引最高者。'+
              /* ★ 2026-10-21 第三十二轮：把「谁被剔除」写出来。
                 原先无门槛时，EVT-001 会选网民（147 个账号只发 4 条帖、介数 0）当定调方，
                 读者无从知道这个「最高」建立在 4 条帖上。 */
              (ADV6.thin.length
                ? '<b>（定调方按帖数 ≥ '+ADV6.min+' 条评选；已剔除样本不足的 '+
                  ADV6.thin.map(function(r){ return r+'（'+ROof(r).posts+' 条帖）'; }).join('、')+
                  ' —— 每帖被引在个位数帖上不稳定，不参与评选。）</b>'
                : ''),
            who:ADV6.tone+'的发言人 / 官方账号 + 企业 PR',
            when:'轮 '+Math.max(ADV6.toneLow.round, 1)+' 至轮 '+ADV6.winLow.round+
                 '（'+ADV6.tone+'情绪低点 → '+ADV6.win+'见底）',
            what:['在'+ADV6.tone+'的表达窗口收窄之前，完成事实澄清与整改说明的同步投放',
                  '同一份口径同时投放到'+ADV6.tone+'与'+ADV6.win+'两个角色可见的渠道',
                  '把定调权留在己方，不等'+ADV6.win+'自行沉底'],
            kpi:'轮 '+ADV6.winLow.round+' 之前完成投放；'+ADV6.tone+'的情绪低点不再深于 '+
                f3(ADV6.toneLow.v),
            pos:'模块二 2.2'};
        })()
      : {t:'情绪干预窗口以全场效价见底（轮 '+LOW_V.round+'）为截止',
         b:'本跑没有任何角色发出帖子，情绪序列由全场均值单独表征，最低点出现在轮 '+LOW_V.round+
           '（'+f3(LOW_V.valence)+'）。',
         who:'企业 PR + 官方账号',
         when:'轮 '+Math.max(LOW_V.round, 1)+' 之前（全场效价谷底为截止）',
         what:['以全场效价谷底为截止日，完成一轮事实澄清与整改说明的同步投放',
               '本跑没有角色级定调方能参照，口径只能按全场均值判断投放时点'],
         kpi:'轮 '+Math.max(LOW_V.round, 1)+' 之前完成投放；全场效价不再深于 '+f3(LOW_V.valence),
         pos:'模块二 2.2'}),
    {t:'关键时段（轮 '+(WIN.lo+R0)+'—'+(WIN.hi+R0)+'）优先配置资源，前期与长尾可降配',
     b:'轮 '+(WIN.lo+R0)+' 至轮 '+(WIN.hi+R0)+' 共 '+WIN_HOURS+' 小时，动作合计（发帖＋评论＋转发＋点赞）<b>'+
       n0(WIN_ACT)+' 次</b>，占全期总量的 '+pct(WIN_ACT/TOTAL_ACT)+'，而时长只占推演区间的 '+
       pct(WIN_HOURS/__HOURS__)+'，密度是全程平均的 '+(WIN_ACT/TOTAL_ACT/(WIN_HOURS/__HOURS__)).toFixed(2)+
       ' 倍，为全程连续 4 轮窗口中最高；该时段负面占比在 '+pct(WIN_NEG_MIN)+'—'+pct(WIN_NEG_MAX)+
       ' 之间（峰值 '+pct(PEAK_NEG.neg)+' 出现在轮 '+PEAK_NEG.round+'），'+
       /* ★ 2026-10-14 第二十二轮：「（媒体发帖 N 条）」里的角色写死为媒体；改取 `WIN_BODY` 现算的
          「窗口内发帖最多的角色」及其帖数（并列时给出并列写法）。 */
       '发声主体为'+WIN_BODY.label+'（窗口内发帖 '+WIN_BODY.n+' 条）。',
     who:'舆情应急负责人（值班指挥）+ 客服 / 内容值班组',
     when:'轮 '+(WIN.lo+R0)+'—'+(WIN.hi+R0)+'（'+WIN_HOURS+' 小时）；起点前一轮完成值班表与口径包下发',
     what:['该时段配置最高等级值班与响应资源（全员值守，不设轮休）',
           '时段之外按长尾标准执行，不做同等级投入',
           '值班表、口径包、升级联系人三件套在时段起点前一轮到位'],
     kpi:'该时段每轮动作无积压；动作密度回落到全程平均 '+
         (WIN_ACT/TOTAL_ACT/(WIN_HOURS/__HOURS__)).toFixed(2)+' 倍以下后方可降级',
     pos:'模块三 3.2'},
    {t:LONGTAIL_ROLE+'群体按「'+
       (ROof(LONGTAIL_ROLE).posts >= META.posts / ROLES.length ? '发帖集中、' : '')+
       '被引为零」筛出长尾清单，用对口触达而非公关口径化解',
     b:LONGTAIL_ROLE+'类账号发帖 '+ROof(LONGTAIL_ROLE).posts+' 条（占全池 '+
       pct(ROof(LONGTAIL_ROLE).posts/META.posts)+'）'+
       /* ★ 2026-10-14 第二十二轮：原先一律写「高频发帖」；EVT-005 的长尾角色只发 2 条帖（5.3%），
          与「高频」直接矛盾。改为按该角色发帖量是否达到角色均值（全池帖数 ÷ 在场角色数）分两支。 */
       (ROof(LONGTAIL_ROLE).posts >= META.posts / ROLES.length
         ? '，是发声量最大的角色之一' : '，发帖量并不突出')+
       /* ★ 2026-10-14 第二十二轮：改列本角色**零被引帖数**（选择该角色的判据本身），
          并去掉写死的「仅」——「每帖被引仅 1.71 次」在 1.71 并不低时读着别扭。 */
       '，其中 <b>'+LT_ZP+' 条全程零被引</b>（'+ROof(LONGTAIL_ROLE).zero+' 个'+LONGTAIL_ROLE+
       '类账号从未被引用），每帖被引 '+ROof(LONGTAIL_ROLE).out_per_post.toFixed(2)+' 次；'+
       /* ★ 2026-10-14 第二十二轮：原句无条件写「体现在全程持续维持的低效价（末轮仍为 X）」——
          换事件后「低」字当场为假。EVT-002 的长尾角色末轮 +0.012，虽低于全场末轮均值 +0.048，
          但它仍在**正区间**，说「低效价」是错的；EVT-005 的 +0.029 更是高于均值 -0.009。
          改为看**符号**决定说哪一头，再看与均值的大小决定后半句怎么落（三支）。 */
       (function(v, m){
         if(v === null || v === undefined){ return '其影响不体现在扩散上，而体现在账号数量与持续发声上。'; }
         if(v < 0){
           /* ★ 2026-10-20 第二十九轮：负值但仍**高于**均值时，原句读作
              「体现在持续维持的负效价（末轮 -0.022，并未低于均值 -0.040）」——
              前半句说它压着情绪，后半句说它比平均还温和，自相矛盾（EVT-002 实测）。
              这一支改成承认它在负区间、但不把它说成更深的拖累。 */
           return (v < m
             ? '其影响不体现在扩散上，而体现在全程持续维持的负效价（末轮 '+f3(v)+
               '，低于全场末轮均值 '+f3(m)+'）。'
             : '其影响不体现在扩散上，而体现在全程持续维持的负效价：末轮 '+f3(v)+
               ' 仍在负区间（虽高于全场末轮均值 '+f3(m)+'，但始终没有转正）。');
         }
         return '其影响不体现在扩散上，而体现在账号数量与持续发声上（末轮效价 '+f3(v)+
                (v < m ? '，仍在正区间但低于全场末轮均值 ' : '，并未低于全场末轮均值 ')+f3(m)+'）。';
       })(RM(LONGTAIL_ROLE)[N-1], S[N-1].valence),
     /* ★ 2026-10-14 第二十二轮：原写「用退保、理赔等服务触点逐条化解」——退保/理赔是
        EVT-001（保险事件）的行业话术，换事件后角色可能是媒体/网民，这句当场不成立。
        改为不提具体行业，只保留「长尾逐条对口触达」这个动作本身。 */
     who:'客服 / 社区运营（按账号所属渠道分工，不设统一回复组）',
     when:'全程；长尾清单每轮更新一次',
     what:['以「全程零被引」为条件建立长尾清单，逐条对口触达化解',
           '不用公关口径统一回复 —— 统一回复本身会被截屏二次传播',
           '清单按轮滚动：被引量转正的账号移出，新出现的零被引账号并入'],
     kpi:'清单内每个账号都有触达记录；零被引账号数逐轮不增',
     pos:'模块二 2.2 / 模块三 3.3'}
  ].concat(
    /* ★ 2026-10-14 第二十二轮：原先无条件写「监管未介入」；换事件后监管若已发帖，标题与依据同时为假。 */
    ROof('监管').posts === 0
      ? [{t:'监管未介入，处置窗口仍然敞开',
          b:'全部 '+ROof('监管').agents+' 个监管类账号在 '+META.rounds+' 轮内发帖 <b>'+ROof('监管').posts+
            /* ★ 2026-10-14 第二十二轮：原写「监管类账号的效价也最为轻微」——「最为」是未经比较的
               最高级断言。改为把末轮读数与全场末轮均值并列写出，让比较可由读者自己复核。 */
            ' 条</b>，均处于观察状态；其末轮效价为 '+f3(RM('监管')[N-1])+
            '（全场末轮均值 '+f3(S[N-1].valence)+'）。',
          who:'企业政府事务 / 公共事务负责人',
          when:TOP_LV+'预警期内（轮 '+TOP_I+' 起）至末轮',
          what:['在'+TOP_LV+'预警期内完成主动沟通与整改说明',
                '避免事件被抬升为监管议题',
                '沟通口径与对外口径同源，不留二次解释的空间'],
          kpi:'监测期内监管类账号发帖数保持 0；无监管议题被抬升的迹象（监管类账号首次发帖）',
          pos:'模块二 2.2 / 模块三 3.3'}]
      : [{t:'监管账号已经入场，本级处置须并入监管口径',
          b:'全部 '+ROof('监管').agents+' 个监管类账号在 '+META.rounds+' 轮内发帖 <b>'+ROof('监管').posts+
            ' 条</b>、每帖被引 '+ROof('监管').out_per_post.toFixed(2)+' 次，不再是纯观察状态'+
            (ROof('监管').zero < ROof('监管').agents
              ? '（其中 '+(ROof('监管').agents - ROof('监管').zero)+' 个账号的帖子已被引用）' : '')+
            '；末轮效价 '+f3(RM('监管')[N-1])+'。',
          who:'企业政府事务 / 公共事务负责人 + 法务',
          when:'监管类账号首次发帖当轮起，至末轮',
          what:['把对外口径与监管已披露的口径对齐后再发布整改说明',
                '避免二次解释 —— 同一事实出现两个版本会被当作新证据再传播',
                '按监管披露的整改条目逐条回填进度，回填口径与对外公告一致'],
          kpi:'后续轮次不再出现与监管口径冲突的表述；监管披露的整改条目回填率 100%',
          pos:'模块二 2.2 / 模块三 3.3'}])
    /* ★ 2026-10-15 第二十六轮：零转载时上面有三条建议被置成 null（它们的主语全是转载），
       在这里滤掉。非零转载的各场没有任何 null ⇒ 这一步是**恒等变换**，输出逐位不变。 */
    .filter(function(x){ return !!x; });

  /* ★ 2026-10-21 第三十二轮（模块五改造 · 主线）：置顶一张「事件类型主线」。
     原来八条建议全是**读数派生**的（「谁发帖多就监测谁」「哪轮密度高就配资源」），
     读完不知道这件事**最终该往哪儿收**。事件类型决定处置主线：营销类 = 公开致歉 +
     下线素材 + 终止与投放方合作；理赔类 = 快赔 + 个案回访。主线不是读数，是领域预案，
     因此它排在**所有读数建议之前**，并写明读数建议服从主线。
     `TYPE_PLAYBOOK` 缺该类型时不插卡（旧行为），只多一句「本事件类型未配预案」。 */
  if (PLAY) adv.unshift({
    mainline:true,
    t:PLAY.name,
    b:'本事件被判定为<b>'+ET+'</b>'+
      (META.event_type_source ? '（事件类型来源：'+META.event_type_source+'）' : '')+
      '。同类事件的处置主线是「'+PLAY.name+'」—— 下面各条读数建议都服务于这条主线；'+
      '与主线冲突的分项动作以主线为准。'+
      (SUBJ ? '<br>涉事主体：<b>'+SUBJ+'</b>。' : ''),
    who:PLAY.who+(SUBJ ? '（'+SUBJ+'）' : ''),
    when:'轮 '+AL.rows[0].round+' 起（不等预警升级），至'+TOP_LV+'解除后 2 轮',
    what:PLAY.steps,
    kpi:PLAY.kpi,
    pos:'模块五 5.0 事件类型预案'});

  var hasMain = !!(adv.length && adv[0].mainline);
  setHTML('advBox', adv.map(function(x, i){
    /* 序号：主线占「主线」标，其余从 1 起编 —— 副标题的「第 N 条」与实际卡号不会错位。 */
    var no = x.mainline ? '<span class="tag">主线</span>'
                        : '<span class="no">'+(i + (hasMain ? 0 : 1))+'</span>';
    var plan;
    if (x.what || x.who || x.when || x.kpi) {
      var rows = '';
      if (x.who)  rows += '<tr><th>主体</th><td>'+x.who+'</td></tr>';
      if (x.when) rows += '<tr><th>时点</th><td>'+x.when+'</td></tr>';
      if (x.what) rows += '<tr><th>动作</th><td>'+
        (Object.prototype.toString.call(x.what) === '[object Array]'
          ? '<ol>'+x.what.map(function(s){ return '<li>'+s+'</li>'; }).join('')+'</ol>'
          : x.what)+'</td></tr>';
      if (x.kpi)  rows += '<tr><th>验收</th><td>'+x.kpi+'</td></tr>';
      plan = '<table class="plan"><tbody>'+rows+'</tbody></table>';
    } else {
      /* 兼容：早期建议只有一句 `a`（本文件的八条已全部换成四要素，这条分支只兜外部载荷）。 */
      plan = '<div class="basis" style="background:#f6fff9;border-left:3px solid #18a058">'+
             '<b>落地动作：</b>'+(x.a || '')+'</div>';
    }
    return '<div class="adv'+(x.mainline ? ' mainline' : '')+'">'+
      '<div class="h">'+no+x.t+'</div>'+
      '<div class="basis"><b>依据：</b>'+x.b+'<br><span style="color:#8a97a8">出处：'+x.pos+'</span></div>'+
      plan+'</div>';
  }).join('') + llmBlock('L3', '综合研判'));
});
/* `bi` 是数组下标；返回的 `round` 是**轮号**（两者差不一定是 0，见顶部 R0）。
   调用方只用它做排序与显示，不当下标用。 */
function minOf(a){ var bi=-1, bv=Infinity;
  a.forEach(function(v,i){ if(v!==null && v!==undefined && v<bv){ bv=v; bi=i; } });
  return {round:bi < 0 ? bi : bi + R0, v:bv}; }

/* ================= 附录 A ================= */
SEC(2675, function(){
  var h = '<thead><tr><th class="num">轮</th><th>窗口起</th><th>时长段</th>'+
    '<th class="num">全场效价</th><th class="num">在线效价</th><th class="num">负面占比</th>'+
    '<th class="num">在线人数</th><th class="num">新帖</th><th class="num">评论</th>'+
    '<th class="num">转发</th><th class="num">点赞</th><th class="num">沉默</th>'+
    '<th class="num">跨渠道新增</th><th class="num">事件池</th><th>预警等级</th></tr></thead><tbody>';
  S.forEach(function(x, i){
    /* ★ R-50：原先写死 `x.round<=11 / <=23` 与 '2h'/'4h'/'8h'。改成按 `BANDS`
       现推（见脚本上方）—— 等长轮次表下 `BANDS` 退化为自然日，行底色即日分带。*/
    var seg = BANDS[BAND_OF[x.round]].seg;
    var segT = spanOf(x) + 'h';
    var a = AL.rows[i];
    h += '<tr class="'+seg+(a.level==='红色'?' hot':'')+'">'+
      '<td class="num"><b>'+x.round+'</b></td><td class="mono">'+x.window[0]+'</td><td>'+segT+'</td>'+
      '<td class="num">'+f3(x.valence)+'</td><td class="num">'+f3(x.valence_online)+'</td>'+
      '<td class="num">'+pct(x.neg)+'</td><td class="num">'+x.online+'</td>'+
      '<td class="num">'+x.post+'</td><td class="num">'+x.comment+'</td>'+
      '<td class="num">'+x.repost+'</td><td class="num">'+x.like+'</td>'+
      '<td class="num">'+x.silent+'</td>'+
      '<td class="num">'+(x.newcross?'<b>'+x.newcross+'</b>':'0')+'</td>'+
      '<td class="num">'+x.pool+'</td><td>'+lvChip(a.level)+'</td></tr>';
  });
  setHTML('tbRounds', h+'</tbody>');
});

/* ================= 附录 B ================= */
SEC(2701, function(){
  var rows = [
    ['每日发帖量与互动量','发帖量取 <span class="mono">posts[].publish_time</span>；互动量取逐轮记录 <span class="mono">round_snapshot.jsonl</span> 的 <span class="mono">comment_cnt + repost_cnt + like_cnt</span>（三者之和，按轮窗口小时数分摊到自然日）。<b>不使用</b> <span class="mono">interaction_total</span>——那是「当前视野内帖子的累计被赞+评论」，是<b>存量</b>、逐轮并不单调、不可加总','模块一 图 1-1 / 表 1-1'],
    ['每日渠道分布','状态快照 <span class="mono">posts[].channel</span>（原发渠道）按发布时间归日','模块一 图 1-2 / 表 1-2'],
    ['渠道覆盖里程碑与跨渠道传播','逐轮快照 <span class="mono">posts[].channel</span> 与 <span class="mono">cross_events[]</span> 各按自身发生的轮次累计（一条渠道被记为「已触达」，当且仅当有帖子落在它上面、或有某次跨渠道传播把它列为到达方）；末列另数 <span class="mono">posts[].q</span> 解出的跨渠道引用（原帖 <span class="mono">ch</span> ≠ 本条 <span class="mono">ch</span>）','模块一 表 1-3'],
    ['情绪演化曲线与负面占比','逐轮记录 <span class="mono">valence_mean / valence_mean_online / negative_ratio</span>','模块二 图 2-1'],
    ['角色与渠道情绪矩阵','逐轮记录 <span class="mono">role_slices{}</span> 与 <span class="mono">scope_type=="channel"</span> 行','模块二 图 2-2 / 2-3'],
    ['情绪拐点与钟点节律的甄别','逐轮 Δ效价与 <span class="mono">cross_events[]</span>（跨渠道传播）交叉验证；钟点增益取各轮快照自记的 <span class="mono">params.gbar</span>，<b>不在报告层复刻钟点表</b>（复刻即多一处随规则漂移的硬编码）','模块二 2.1 拐点判定（节末公式）'],
    ['角色 × 渠道 交叉情绪','逐轮<b>轮末</b>快照 <span class="mono">agents[].role_type</span> × <span class="mono">primary_channel</span> 分组求均值；与 <span class="mono">role_slices{}</span> 口径互校（现算结果与载荷逐格一致）','模块二 2.2 交叉分析'],
    ['关键事件识别','五个维度全部由载荷现算：① 逐轮 <span class="mono">post_cnt + comment_cnt + repost_cnt + like_cnt</span>（动作总量）相对<b>同钟点槽位</b>（轮次 % 6）中位数的偏离；② 逐轮 Δ<span class="mono">valence_mean</span>；③ <span class="mono">posts[].channel</span> 的首次出现轮 + <span class="mono">cross_events[]</span>；④ 逐轮按 <span class="mono">role_type</span> 聚合的 <span class="mono">posts[]</span> 计数；⑤ 逐轮发声渠道数。命中 ≥2 个维度记为关键事件；「影响倍数」＝后 3 轮与<b>__PREWIN_APP__</b>的前 3 轮 <span class="mono">comment_cnt + repost_cnt + like_cnt</span> 均值之比','模块三 表 3-1'],
    ['关键时段识别','逐轮记录 <span class="mono">post_cnt / comment_cnt / repost_cnt / like_cnt</span> 之和（动作总量，命题口径的「互动总量」），取<b>连续 4 轮滑动窗口</b>的 argmax；负面占比取逐轮 <span class="mono">negative_ratio</span>','模块三 图 3-1'],
    ['渠道每帖平均被引次数','由 <span class="mono">quoted_id</span> 构引用森林后现算各渠道被引合计，除以该渠道发帖数（<span class="mono">posts[].channel</span> 聚合）','模块三 图 3-2 / 表 3-2'],
    ['风险等级与预警时点','报告层合成：三项维度、八项观测量 —— <b>其中六项</b>在 <b>5 轮滑动窗口</b>上聚合后各自除以本跑全程窗口最大值，<b>另两项</b>（效价强度、负面人数占比）取该轮即时值（效价除以规则常量 0.50，负面人数占比本身即 0–1 比例）；再乘以 0.35 / 0.30 / 0.35 求和；<b>渠道权重</b>与<b>动作强度</b>为规则级常量（六个渠道、六类动作全定义，见表 4-1）','模块四 图 4-1 / 表 4-3']
  ];
  var h = '<thead><tr><th class="num">序</th><th>报告结论</th><th>数据来源与字段</th><th>所在位置</th></tr></thead><tbody>';
  rows.forEach(function(r, i){
    h += '<tr><td class="num">'+(i+1)+'</td><td><b>'+r[0]+'</b></td><td>'+r[1]+'</td><td>'+r[2]+'</td></tr>';
  });
  setHTML('tbSrc', h+'</tbody>');
});

SEC(2722, function(){
  setHTML('calib',
    '<li><b>状态快照口径：</b>'+META.rounds+' 轮一律取<b>轮末</b>状态。'+
      /* ★ 2026-10-21 第 29 轮：老跑 series 从轮 0 起，第 0 点是 t0轮末状态.json；
         新跑 series 从轮 1 起、没有轮 0 这一行（t0 是初始态窗口，不进 series），
         再写「轮 0 取 t0轮末状态.json」就是指着一条不存在的点说话。按首轮号分两支。 */
      (__SERIES0__ === 0
      /* ★ 判「有没有轮 0」要看 **series 首行自己的轮号**（`__SERIES0__`），
         不是 `__FIRSTROUND__` —— 后者是抬头「轮 N—M」里的 N，老跑也被钳到 1。 */
        ? '轮 0 取 <span class="mono">t0轮末状态.json</span>，'
        : '首轮即轮 '+S[0].round+'（t0 为初始态窗口，不单列一轮），')+
      '与逐轮记录同源（其 <span class="mono">valence_mean</span> 为 '+
      String((+S[0].valence).toFixed(4)).replace('-','−')+
      '），保证 '+META.rounds+' 个点口径一致。</li>'+
    '<li><b>跨渠道定义：</b>某条<b>已存在</b>的帖子，其落地渠道集合在某一轮新增了渠道，记为该轮一次跨渠道传播，'+
      '新增的渠道个数计入「新增渠道边」。因此 '+CE.length+' 次事件对应 '+META.cross_n+' 条边'+
      /* ★ 2026-10-15 第二十六轮：零转载时 `CE[0]` 是 undefined ⇒ 整段口径说明渲染失败
         （EVT-011 实测：calib 从未被写入）。 */
      (NO_CE
        ? '（本跑 '+META.rounds+' 轮的 <span class="mono">new_cross_channel_edges</span> 合计为 <b>0</b> —— '+
          '没有任何一条原帖的落地渠道集合增长过；另起一帖、落到引用者自己渠道的<b>跨渠道引用</b>'+
          '<b>不产生渠道边</b>，故不计入本口径，本跑有 '+QX.length+' 条），'+
          '渠道覆盖的逐轮推进见表 1-3（本跑没有「来源渠道 × 到达渠道」组合可拆）。</li>'
        /* ★ 第二十八轮：这个括注是用来解释「事件数 ≠ 边数」的 —— 只在**首次事件确实一次
           新增了多个渠道**时才成立。EVT-002/008 的首次事件各只新增 1 个渠道、事件数又恰好等于
           边数，却照样印出「（轮 3 一次事件同时新增新闻门户 1 个渠道）」，读者读不出它想解释什么。 */
        : (CE[0].to.length > 1
            ? '（轮 '+CE[0].round+' 一次事件同时新增'+CE[0].to.join('与')+' '+CE[0].to.length+' 个渠道），'
            : '，')+
          '渠道覆盖的逐轮推进与逐次明细见表 1-3（按「来源渠道 × 到达渠道」拆开共 '+ARC_N+' 对组合）。</li>')+
    /* ★ 2026-10-12 第九轮：模块三的节点指标排序与「按角色」两节删除后，出度 / 中心性两项
       在报告里已无呈现，定义随之删去；保留的**被引次数**（图 3-2 的「被引合计」）与
       **介数**仍是报告里在用的量。
       ★ 2026-10-13 第十七轮：模块四的观测量全部改为 5 轮窗口口径，G3 取「窗口内新生帖
       介数峰值」而不再是「全程最大介数」，末句随之改写。 */
    '<li><b>被引与介数：</b>被引次数＝一条帖被其他帖引用的次数，在 '+META.rounds+
      ' 轮内累计（图 3-2 的「被引合计」）。介数＝经过该节点的「祖先→后代」传播链条数'+
      '（严格祖先数 × 严格后代数）；模块四的参与强度 G3 取其<b>窗口内新生帖峰值</b>。</li>'+
    '<li><b>角色与渠道归属：</b>账号角色取画像库 <span class="mono">role_type</span>，渠道取 '+
      '<span class="mono">primary_channel</span>。媒体（中央）与媒体（新闻）在仿真层已统一映射为<b>媒体</b>。</li>'+
    '<li><b>每日量口径：</b>发帖量按帖子实际发布时间归属自然日；互动量＝<b>评论 + 转发 + 点赞的动作数</b>，'+
      '因交互记录为逐轮汇总、无逐条时间戳，按各轮窗口小时数分摊到自然日（故会出现 .5），全期合计 '+
      n0(TOTAL_INTER)+' 次。t0 落在 '+META.t0+'，当日仅覆盖 '+
      /* ★ 2026-10-15 第二十五轮：原先取 `24 − t0 的小时`（EVT-010 的 t0 = 20:38 ⇒ 印「4 小时」），
         与表 1-1 图注的 `payload.daily[0].hours`（3.4）、1.1 正文的同一读数（3）**一个量三个数**；
         t0 落在整点的 EVT-001 恰好三者相同（6/6/6），所以从没露过。
         改为与表 1-1 图注同源：Python 侧 `CAP_FIRSTH`（`"%g" % payload.daily[0].hours`）。 */
      '__CAP_FIRSTH__'+' 小时。</li>'+
    '<li><b>口径修正留痕：</b>本报告此前的「互动总量」曾直接取逐轮记录的 '+
      '<span class="mono">interaction_total</span>。该字段是「<b>当前在视野内的帖子累计被赞+评论</b>」的'+
      '<b>存量</b>快照（<span class="mono">recorder_a.py</span>），帖子离开视野时会下降——本跑 '+META.rounds+
      ' 轮中有 <b>'+N_STOCK_DROP+' 轮</b>低于上一轮——把它按轮/按日求和等于把同一批互动反复重计（求和得 '+
      n0(TOTAL_INTER_STOCK)+'，而真实互动动作只有 '+n0(TOTAL_INTER)+' 次，虚增约 '+
      (TOTAL_INTER_STOCK/TOTAL_INTER).toFixed(1)+' 倍）。现已一律改用上面的流量口径。</li>'+
    '<li><b>风险等级：</b>报告层合成口径。三项维度、八项观测量 —— '+
      '<b>六项</b>取 <b>5 轮滑动窗口</b>（约 20 小时，末尾对齐当前轮）的值，各自除以本跑全程窗口最大值；'+
      '<b>另两项</b>（全场效价强度、负面人数占比）取<b>该轮即时值</b>，前者除以规则常量 0.50、后者本身即 0–1 比例；'+
      '两部分再按 0.35 / 0.30 / 0.35 加权求和；<b>渠道权重</b>与<b>动作强度排行</b>是规则级常量，六个渠道、六类动作全部有定义。'+
      '公式与规则见表 4-1 / 表 4-2，阈值见图 4-1。'+
      '<b>仿真层不输出绝对投诉量</b>，本报告全部指标为指数、占比与计数，不含投诉量预测。</li>'+
    /* 这里说的是「快照文件几份」，不是「推演几轮」—— 新跑两份数不相等（见 meta.snapshots）。 */
    '<li><b>数据来源：</b>状态快照 <span class="mono">__RUNDIR_NAME__\\__SNAP_PATTERN__</span>（'+(META.snapshots || META.rounds)+' 份）、'+
      '逐轮记录 <span class="mono">__RUNDIR_NAME__\\__RECORDER_FILE__</span>、'+
      '画像库 <span class="mono">__RUNDIR_NAME__\\__PERSONA_FILE__</span>。</li>');
});

/* ---------------- 摘要 ---------------- */
/* ★ 2026-10-13 第十八轮：摘要由「四段（模块一/二/四）」扩为**全篇五模块**摘要 ——
   与正文五个模块一一对应，每个模块给一句由载荷现算的核心结论，顺序与正文一致。
   两处硬编码一并去掉：「由 3 条帖子注入」里的 3 改为现算轮 0 帖数；
   「全部由媒体类账号发起」早在第九轮已改为按 CE 现算（此处沿用）。
   ★ 本块原先挂在脚本前部（模块一之前），只能取到此前已赋值的变量；扩为五模块后需引用
     模块三的现算量（EVENTS / WIN / PP_TOP），故**整块下移至全套脚本末尾**（附录 B 之后、
     末尾 echarts 兜底之前），此时所有顶层 `var` 均已赋值。渲染先后对结果无影响。
   ★ 加粗只留「贴进规模」与「红色」两处；五模块句一律不加粗。 */
SEC(2772, function(){
  var redRow = AROW(AL.first_round['红色']);
  var nRed   = AL.rows.filter(function(x){ return x.level==='红色'; }).length;
  /* ★ 2026-10-20 第二十九轮：原取「轮 0 的全部帖子」—— 轮 0 里还包含**引用帖**，
     EVT-002 印出「由 7 条帖子注入」，而 t0 快照与 r11 都是 **4 条原生帖**。
     注入量只能数根帖（无被引父帖）。 */
  var nSeed  = ROOTS.length;
  var nChPub = PP_POSTS.length;                                        /* 有内容产出的平台数 */
  setHTML('abstract',
    '<b>报告摘要。</b>本次事件于 '+META.t0+' 由 '+nSeed+' 条原帖注入，在 '+META.rounds+
    ' 轮、__HOURS__ 小时的推演中扩散为 <b class="k">'+META.posts+' 条帖、'+META.agents+
    ' 个智能体、'+META.edges+' 条引用边</b>，最大引用链 '+META.max_depth+' 层。'+
    '内容落在 '+nChPub+' 个平台，'+
    /* ★ 2026-10-15 第二十六轮：零转载时 `CE[0]` 是 undefined ⇒ 摘要段渲染失败
       （EVT-011 实测：abstract 从未被写入）。 */
    (NO_CE
      ? '全程<b class="k">没有发生跨渠道转载</b>（新增渠道边 0 条），跨平台流动由 '+QX.length+
        ' 条跨渠道引用完成。'
      : '全程记录到 '+CE.length+' 次跨渠道传播（'+META.cross_n+
        ' 条新增渠道边），全部由'+CE_ROLE_STR+'发起，首次发生在轮 '+CE[0].round+'（'+CE[0].window[0]+
        '），由'+CE[0].from.join('与')+'进入'+CE[0].to.join('与')+'。')+
    '情绪最低点出现在轮 '+LOW_V.round+'（'+LOW_V.window[0]+'），全场效价 '+f3(LOW_V.valence)+
    '；全期互动量共 '+n0(TOTAL_INTER)+' 次，单轮峰值在轮 '+PEAK_INT.round+'（'+PEAK_INT.window[0]+
    '）'+PEAK_INT_TIE+'，负面占比峰值在轮 '+PEAK_NEG.round+'（'+PEAK_NEG.window[0]+'）。'+
    '全程识别出 '+EVENTS.length+' 个关键影响节点（命中 ≥2 个识别维度），关键时段为轮 '+
    (WIN.lo+R0)+'—'+(WIN.hi+R0)+'，每帖被引最高的平台是'+PP_TOP+'（'+CO[PP_TOP].out_per_post.toFixed(2)+
    ' 次/帖）。'+
    (HAS_RED
     ? '预警等级自轮 '+AL.rows[0].round+' 的'+AL.rows[0].level+'逐级升至<b class="k">红色</b>，首次触发于轮 '+redRow.round+
       '（'+redRow.window[0]+'）；红色全程共出现 '+nRed+
       ' 轮，最高总分在轮 '+AL.peak.round+'（S = '+AL.peak.score.toFixed(3)+
       '），'+(LV_DROPS ? '此后等级回落，并未一路维持到推演结束。'
                       : '此后等级一直维持在最高档，全程没有回落。')+
       /* ★ 2026-10-21 第三十二轮：摘要原先把建议说成「全部由上述结果触发」——
          现在模块五置顶了一条来自事件类型的**领域主线**，不是读数的产物。 */
       (EVT_PLAY ? '应对建议以「'+EVT_PLAY.name+'」为主线（'+EVT_TYPE+'预案），分项按上述结果逐条触发：'
                 : '应对建议按上述结果逐条触发：')+
       '红色触发前激活预案、'+
       /* ★ 2026-10-15 第二十六轮：零转载时 `CE_AG_N` 恒为 0 ⇒ 原先会印
          「锁定 0 个跨渠道账号建立转载监测」，本跑根本无转载可监测。 */
       (NO_CE
         ? (QX.length ? '把 '+QX_AG_TXT+'列入监测（本跑无转载、渠道边 0 条，监测口径落在引用账号而非渠道边）、'
                      : '本跑无任何跨渠道流动，监测重点落在原发平台内的账号、')
         : '锁定 '+CE_AG_N+' 个跨渠道账号建立转载监测、')+
       '把轮 '+(WIN.lo+R0)+'—'+(WIN.hi+R0)+' 列为最高等级响应时段。'
     : '预警等级自轮 '+AL.rows[0].round+' 的'+AL.rows[0].level+'最高升至<b class="k">'+TOP_LV+'</b>，首次触发于轮 '+TOP_ROW.round+
       '（'+TOP_ROW.window[0]+'）；'+TOP_LV+'全程共出现 '+TOP_N+
       ' 轮，最高总分在轮 '+AL.peak.round+'（S = '+AL.peak.score.toFixed(3)+
       '），全程未触及红色阈值 '+AL.cut['红色'].toFixed(2)+'，'+
       (LV_DROPS ? '此后等级回落，并未一路维持到推演结束。'
                 : '此后等级一直维持在最高档，全程没有回落。')+
       (EVT_PLAY ? '应对建议以「'+EVT_PLAY.name+'」为主线（'+EVT_TYPE+'预案），分项按上述结果逐条触发：'
                 : '应对建议按上述结果逐条触发：')+
       '按'+TOP_LV+'级配置响应资源、'+
       /* ★ 2026-10-15 第二十六轮：同上一支，零转载时 `CE_AG_N` 为 0。 */
       (NO_CE
         ? (QX.length ? '把 '+QX_AG_TXT+'列入监测（本跑无转载、渠道边 0 条，监测口径落在引用账号而非渠道边）、'
                      : '本跑无任何跨渠道流动，监测重点落在原发平台内的账号、')
         : '锁定 '+CE_AG_N+' 个跨渠道账号建立转载监测、')+
       '把轮 '+(WIN.lo+R0)+'—'+(WIN.hi+R0)+' 列为最高等级响应时段。'));
});
/* ================= 各模块左下角的灰字公式（★ 2026-10-20 第二十七轮新增） =================
   仿真侧：「每个模块的左下角…用灰色的小字去把这个公式是什么东西说一下 —— 显示可信度与透明性」。
   全部现算：τ、门槛、归一化分母都印本跑的实际值，换事件自动跟着变，不写死任何常数。
   任一模块取不到量就留空，不印半截公式（宁可不出，也不印错）。 */
SEC(4130, function(){
  var FX = function(s){ return '<span class="fx">'+s+'</span>'; };
  /* ---- 模块一 ---- */
  setHTML('fnM1',
    '<b>口径</b>　发帖量 '+FX('w_d = Σ_{r∈d} post_r')+'（帖子按实际发布时间归属自然日）；'+
    '互动量 '+FX('I_d = Σ_r flow_r × h(r∩d) / H_r')+
    '（评论＋转发＋点赞的动作数，跨轮的按窗口小时数分摊到自然日），全程合计 '+n0(TOTAL_INTER)+' 次。'+
    '跨渠道边 '+FX('E = E_转载 + E_跨渠道引用')+'，全程 '+CE.length+' 起。'+
    '「已触达渠道数」= 有帖子落点或有跨渠道边到达的渠道去重计数。');
  /* ---- 模块二 ---- */
  setHTML('fnM2',
    '<b>拐点</b>　'+FX('v6(k) = mean(valence[k … k+5])')+'（6 轮 = 24 小时，滑窗抵消日节律）　'+
    FX('s(k) = v6(k+1) − v6(k)')+'　'+FX('τ = max(0.05 × (max v6 − min v6), 0.0005)')+'，本跑 τ = '+
    /* `|s| < τ` 里的 < 必须转义成 &lt; —— 这段是 innerHTML，裸 < 会被当成标签吃掉，
       渲染出来会变成「|s| 记走平」（2026-10-20 实测踩过）。 */
    (+PIV_CALC.tau).toFixed(4)+'。'+FX('s ≥ τ')+' 记回升、'+FX('|s| &lt; τ')+' 记走平、'+FX('s ≤ −τ')+' 记下跌；'+
    '状态切换处取相邻两窗交界轮 '+FX('k+3')+' 为拐点，每段状态须持续 ≥ 3 步。'+
    '<br><b>自然平息</b>　'+FX('H = clamp((1 − W_末6 / max W6) / 0.50)')+'　'+
    FX('V = 0.6·V_up + 0.4·V_lvl')+'，其中 '+FX('V_up = clamp((v_末6 − min v) / max(极差, 0.02))')+'，'+
    FX('V_lvl')+' 按末段所在档取 0 / 0.25 / 0.50 / 0.75 / 1.00（强负 / 负 / 中 / 正 / 强正）；'+
    FX('N = clamp((n_峰6 − n_末6) / 0.10)')+'。门槛 '+FX('H ≥ 0.50')+' 且 '+FX('V ≥ 0.35')+'，'+
    '两项都过后再按 '+FX('N ≥ 0.50')+' 分「已平息 / 趋于平息」。'+
    '<br><b>修复通道</b>　6 轮（24 小时）窗口同时满足三条：'+
    FX('Δv ≥ max(0.02, 0.10 × 极差)')+'（本跑门槛 '+f3(REPAIR.floor)+'）、'+
    FX('Δneg ≤ −0.02')+'、'+FX('Σ 窗口内 newcross = 0')+'。三条件缺一不算。');
  /* ---- 模块三 ---- */
  setHTML('fnM3',
    '<b>关键影响节点</b>　同一轮命中 <b>≥ 2 个</b>识别维度即记为关键事件，全程共 '+EVENTS.length+' 个。'+
    '<br><b>影响倍数</b>　'+FX('M = mean(act[t+1 … t+3]) / mean(act[t−3 … t−1])')+'，前窗一律__PREWIN_SHORT__；'+
    '前窗为空记「—」，不以 0 或 ∞ 冒充。'+
    '<br><b>关键时段</b>　'+FX('argmax_w Σ_{r∈w} act_r')+'，窗口宽 4 轮，本跑落在轮 '+(WIN.lo+R0)+'—'+(WIN.hi+R0)+'。'+
    '<br><b>渠道影响力</b>　'+FX('每帖被引 = 该渠道帖子被引用的累计次数 / 该渠道发帖数'));
  /* ---- 模块四：权重、归一化分母、阈值全部取自数据包 alert 块，不重抄常数 ---- */
  (function(){
    if(typeof AL === 'undefined' || !AL) return;
    var dn = AL.dim_names || {}, w = AL.weights || {}, sw = AL.sub_weights || {}, nm = AL.norm || {};
    var label = {postw:'渠道加权帖量', nch:'发帖渠道种数', cross:'新增跨渠道边',
                 val:'效价强度', neg:'负面人数占比',
                 num:'发声人数', act:'动作强度加权互动', betw:'新生帖介数峰值'};
    var denom = {postw:nm.postw, nch:nm.nch, cross:nm.cross, val:nm.val_cap,
                 neg:nm.nag, num:nm.nag, act:nm.actw, betw:nm.betw};
    var sub = function(g){
      var s = sw[g] || {}, ks = Object.keys(s);
      return ks.map(function(k){ return (+s[k]).toFixed(2)+'·'+FX(label[k] || k); }).join(' + ');
    };
    var dims = ['G1','G2','G3'].filter(function(g){ return w[g] !== undefined; });
    var th = AL.cut || {};
    setHTML('fnM4',
      '<b>合成</b>　'+FX(AL.formula || 'S = Σ w_i·G_i')+'　'+
      dims.map(function(g){ return FX(g+' = '+sub(g))+'（'+(dn[g] || g)+'）'; }).join('　')+'<br>'+
      '<b>归一化</b>　'+FX('x̂ = x / max_w x')+'，分母取<b>本跑全程</b>的窗口最大值：'+
      ['postw','nch','cross','num','act','betw'].filter(function(k){ return denom[k] !== undefined; })
        .map(function(k){ return label[k]+' '+n0(denom[k]); }).join('、')+'；'+
      '另有封顶量 '+FX('效价强度 = min(|v| / '+(nm.val_cap !== undefined ? nm.val_cap : '—')+', 1)')+'。'+
      '<br><b>阈值</b>　'+['红色','橙色','黄色','蓝色'].filter(function(k){ return th[k] !== undefined; })
        .map(function(k){ return k+' ≥ '+(+th[k]).toFixed(2); }).join('　')+'，窗口宽 '+AL.window+' 轮。');
  })();
  /* ---- 模块五 ---- */
  setHTML('fnM5',
    /* ★ 2026-10-21 第三十二轮（模块五改造）：本段原先写「每条建议绑定一个触发条件，
       条件全部来自前四个模块的现算结果」并声称「按严重度降序排」——
       加了主线卡之后前半句不再成立（主线是领域预案、不来自任何现算），
       后半句本来就与实际的固定顺序不符。改成与页面一致的两套来源 + 固定顺序。 */
    '<b>结构</b>　先一条「事件类型主线」，再若干条「读数建议」。'+
    '主线按采集侧事件类型查表（八类，与引擎投诉政策同一套分类），只给领域预案、不参与现算；'+
    '类型不在表内时不出主线。读数建议的触发条件全部来自前四个模块的现算结果，不另设常数：'+
    FX('等级')+' → 响应级别与资源投放；'+FX('关键时段')+' → 重点布防窗口；'+
    FX('关键事件')+' → 监测对象与话术口径；'+FX('渠道被引')+' → 平台侧处置优先级；'+
    FX('角色负面集中度')+' → 沟通对象与发声主体。'+
    '<br><b>四要素</b>　每条建议都给「主体 / 时点 / 动作 / 验收」四项，一线拿到即可照做。'+
    '<br><b>排序</b>　固定顺序，不按分数排：主线 → 等级与预案激活 → 跨渠道处置 → '+
    '渠道资源分配 → 情绪干预窗口 → 关键时段 → 长尾 → 监管。某条不适用时该条不出，其余顺序不变。'+
    '<br><b>不写死的部分</b>　每条建议的正文措辞可交由 LLM 依据上述触发条件生成，'+
    '但触发条件本身、涉及的数字与结论均由本报告现算，LLM 不改数、不改判定。');
});
if(SEC_ERR.length){
  var _se = document.getElementById('secErr');
  if(_se){
    _se.innerHTML = '⚠ 本报告有 <b>' + SEC_ERR.length + '</b> 个段落未能渲染（其余段落正常）：' +
      SEC_ERR.map(function(x){ return '<span class="mono">' + x + '</span>'; }).join('；');
    _se.style.display = 'block';
  }
}
if(typeof echarts === 'undefined'){ document.getElementById('warn').style.display = 'block'; }
</script>
</body>
</html>
"""

# ★ R-50（2026-10-12）：下面这一组原先是**写死的常量**（`HOURS = 168`、正文里的
#   「36 轮 / 轮 1—35 / 653 次 / 15 条弧线」）。轮次表一改（36 → 42）它们**不会报错**，
#   只会**静默印旧数** —— 报告看上去完好，数字全是上一版的。
#   一律改成从 `_viz_data.json` 的 `meta` 现推，跟 `rerun_recorder.py` 同一个做法。
_ts = lambda s: datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
# ★ 2026-10-21 第 29 轮：改成 **series 各轮窗口时长之和**，不再是 t0→end 的墙钟差。
#   老跑两者相等（t0 就是第 0 轮的起点，42×4h = 168h），改后逐字不变；
#   新跑（t0 = 初始态、轮号 1…42）t0→end 是 172h（多算了 t0 注入窗口那 4 小时），
#   而正文说的是「N 轮、M 小时」、表 1-1 的合计行又把它跟逐日小时数对账 ——
#   用墙钟差会让「42 轮 / 172 小时」与「合计 168」当场打架。以轮为准。
HOURS = int(round(sum((_ts(x["window"][1]) - _ts(x["window"][0])).total_seconds()
                      for x in payload["series"]) / 3600.0))
DAYS = HOURS // 24
# ★ 2026-10-21 第 29 轮：轮号不再假定从 0 起、也不再假定「轮数 - 1 = 末轮号」。
#   老跑 series 轮号 0…41 ⇒ FIRSTROUND=1、LASTROUND=41，与改前逐位相同；
#   新跑（t0 = 初始态）轮号 1…42 ⇒ 1 与 42。
_RS = payload["series"]
FIRSTROUND = max(1, _RS[0]["round"]) if _RS else 1
SERIES0 = _RS[0]["round"] if _RS else 0        # series 首行自己的轮号（老跑 0、新跑 1）
LASTROUND = _RS[-1]["round"] if _RS else payload["meta"]["rounds"]
NACT = payload["meta"]["actions"]
ARCN = sum(len(e["from"]) * len(e["to"]) for e in payload["cross_events"])

# ★ 2026-10-21 第 29 轮：表 3-1 的「前窗剔轮 0」三处说明按**首轮号**分两支。
#   老跑首轮 = 轮 0（t0 残缺轮，零互动）⇒ 原文一字不改；
#   新跑首轮 = 轮 1（完整一轮）⇒ 没有可剔的残缺轮，再写「剔轮 0」就是指着不存在的点说话。
_T0ROW = (SERIES0 == 0)
PREWIN_NOTE = ((u'一律剔除轮 0</b> —— 轮 0 是 t0 残缺轮（当日只覆盖 2 小时、零互动），'
                u'计入会把首个事件的倍数额外放大一倍以上。首个事件的前窗剔完为空，'
                u'倍数记「—」，不以 0 或 ∞ 冒充。')
               if _T0ROW else
               (u'不剔任何一轮</b> —— 本次 series 从轮 1 起（t0 为初始态窗口，不单列一轮），'
                u'没有需要剔除的残缺轮；前窗不足 3 轮时倍数记「—」，不以 0 或 ∞ 冒充。'))
PREWIN_APP = u'剔除轮 0 后' if _T0ROW else u'按轮号（t0 不单列一轮）'
PREWIN_SHORT = u'剔轮 0' if _T0ROW else u'按轮号取满'

# ★ 2026-10-12 模块一改版：自然日序列（原为「逐日并列小图网格」的格数）。
#   天数 = **自然日序列的长度**，而**不是** HOURS//24：t0 落在 22:00，168 小时
#   覆盖会跨到第 8 个自然日（05-22 22:00 → 05-29 22:00 共触及 8 个日历日）。
#   序列与 JS 侧 `DAYS` **完全同源**（帖子实际发布日去重排序），两边逐位对齐。
CDAYS = sorted({p["t"][:10] for p in payload["posts"]})
NCDAYS = len(CDAYS)
# ★ 2026-10-12 第三轮改版：逐日拓扑网格（`__GRIDDAYS__`）随拓扑一起删除，
#   但 `CDAYS` / `__NCDAYS__` **保留** —— 模块二 2.2 的图注仍在用这个词数。
# ★ 2026-10-12 第四轮：桑基图撤掉后 __CEN__ / __CEAGN__ / __ARCLINK__ 三个占位符
#   在整个模板里都不再出现，故连计算一起删（跨渠道起数/账号数改由 JS 侧现算）。
TOTALFLOW = payload["meta"]["inter_flow"]

# ★ 2026-10-13 第十八轮：C 组「本跑专属论断」一律**现算**，不再写死在图注里。
#   改前这几句是拿 EVT-001 的事实（两个渠道全程 0 帖）当通用话术写死的，
#   换一个事件就是假话。此处统一从载荷推，再经占位符注入模板 —— 与既有 `__NROUNDS__` 同一套做法。
_CHS  = payload["chans"]
_T0   = payload["meta"]["t0"]
# ★ 2026-10-14 第二十一轮：与 JS 侧 `CO` 同口径 —— 渠道「有没有内容产出」按**帖的原发渠道**判，
#   不再按载荷 `ch_stat`（发帖人所属渠道）。两口径在 EVT-001 上一致故无变化；EVT-004 上
#   `ch_stat` 说监管通报有 4 条帖，按帖口径实为 0 条 ⇒ 图注据此正确判为「无内容产出」。
_PCH = {}
for _p in payload["posts"]:
    _PCH[_p["ch"]] = _PCH.get(_p["ch"], 0) + 1
CH_ZERO  = [c for c in _CHS if not _PCH.get(c)]   # 无内容产出的渠道
CH_LIVE  = [c for c in _CHS if _PCH.get(c)]       # 有内容产出的渠道
# 堆叠顺序：配置里的 `channel_order` 优先（只保留有帖的），其余有帖渠道按载荷顺序追加
_PREF    = [c for c in (CFG.get("channel_order") or []) if c in CH_LIVE]
CH_ORDER = _PREF + [c for c in CH_LIVE if c not in _PREF]
ZERO_STR = "与".join(CH_ZERO)
_CNA = {1: "一", 2: "两", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八"}
# ★ 2026-10-14 第二十二轮：原先写 `24 - int(_T0[11:13])`，把 t0 的**分钟**丢了 ——
# EVT-004 的 t0 是 22:59，表 1-1 图注算成 2 小时，而数据包 `daily[0].hours` 是 1.0 小时
# （viz_extract 按真实时间边界切日），正文 r12 立刻就会印出与之矛盾的「1 小时」。
# 改为直接读数据包里的同一个字段，图注与正文从此同源。
FIRSTDAY_HOURS = payload["daily"][0]["hours"]

CAP_CHORDER = "、".join(CH_ORDER)
CAP_T0      = _T0[5:16]
CAP_FIRSTH  = ("%g" % FIRSTDAY_HOURS)
# ★ 2026-10-15 第二十五轮：图 2-1 图注里的「共 7 日」原先也是写死的 —— 它是
# `轮数 × 4 小时 / 24` 的现推值（42 轮 = 7 日整），换轮数即印错。改从 `meta` 现算。
CAP_EMODAYS = ("%g" % round(HOURS / 24.0, 1))
CAP_Z12 = (ZERO_STR + "全程 0 条，不出现在图中。") if CH_ZERO else "全部渠道全程均有内容产出。"
CAP_Z13 = ("其中" + ZERO_STR + "全程没有内容产出。") if CH_ZERO else "全部渠道均有内容产出。"
CAP_Z32 = ((ZERO_STR + "无发帖产生，其「每帖被引」为 0 表示无内容可被引，不代表传播力为零。")
           if CH_ZERO else
           "全部渠道均有发帖；「每帖被引」为 0 表示无内容可被引，不代表传播力为零。")

# 表 4-1 图注：0 帖渠道与 0 次动作两条都现算
_ad = payload["meta"].get("action_dist") or {}
_ACT_CN = {"NOTICE": "通报", "COMPLAINT": "投诉引用"}          # 平台口径（与 viz_alert 的 ACT_W 同源）
_zero_acts = [_ACT_CN[k] for k in ("NOTICE", "COMPLAINT") if not _ad.get(k)]
_ch_txt = ("%s%s个渠道 0 帖" % (ZERO_STR, _CNA.get(len(CH_ZERO), str(len(CH_ZERO))))
           if CH_ZERO else "全部渠道均有发帖")
if _zero_acts:
    CAP_41 = ("本跑" + _ch_txt + "，" +
              "%s%s类动作 0 次" % ("与".join(_zero_acts), _CNA.get(len(_zero_acts), str(len(_zero_acts)))) +
              "，权重已定义但未触发。")
elif CH_ZERO:
    CAP_41 = "本跑" + _ch_txt + "，六类动作均已触发，渠道权重已定义。"
else:
    CAP_41 = "本跑全部渠道均有发帖，六类动作均已触发，权重已定义。"

html = (TEMPLATE
        .replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
        .replace("__T0__", payload["meta"]["t0"])
        .replace("__END__", payload["meta"]["end"])
        .replace("__NROUNDS__", str(payload["meta"]["rounds"]))
        .replace("__FIRSTROUND__", str(FIRSTROUND))
        .replace("__SERIES0__", str(SERIES0))
        .replace("__PREWIN_NOTE__", PREWIN_NOTE)
        .replace("__PREWIN_APP__", PREWIN_APP)
        .replace("__PREWIN_SHORT__", PREWIN_SHORT)
        .replace("__LASTROUND__", str(LASTROUND))
        .replace("__NACT__", str(NACT))
        .replace("__ARCN__", str(ARCN))
        .replace("__TOTALFLOW__", str(TOTALFLOW))
        .replace("__HOURS__", str(HOURS))
        .replace("__NCDAYS__", str(NCDAYS))
        .replace("__DAYS__", str(DAYS))
        # ★ 2026-10-13 第十八轮：事件身份串改由配置注入（原先写死在模板里）。
        .replace("__DOC_TITLE__", CFG.doc_title)
        .replace("__REPORT_TITLE__", CFG["report_title"])
        .replace("__EYEBROW__", CFG.eyebrow)
        .replace("__EVENT_TYPE__", CFG["event_type"])
        .replace("__RUNDIR_NAME__", CFG.run_label)
        .replace("__SNAP_PATTERN__", CFG.snap_pattern_display)
        .replace("__RECORDER_FILE__", CFG["recorder_file"])
        .replace("__PERSONA_FILE__", CFG["persona_file"])
        # ★ 2026-10-13 第十八轮：C 组图注的现算值
        .replace("__CH_ORDER__", json.dumps(CH_ORDER, ensure_ascii=False))
        .replace("__CAP_T0__", CAP_T0)
        .replace("__CAP_FIRSTH__", CAP_FIRSTH)
        .replace("__CAP_EMODAYS__", CAP_EMODAYS)
        .replace("__CAP_CHORDER__", CAP_CHORDER)
        .replace("__CAP_Z12__", CAP_Z12)
        .replace("__CAP_Z13__", CAP_Z13)
        .replace("__CAP_Z32__", CAP_Z32)
        .replace("__CAP_41__", CAP_41))

# ★ 2026-10-13 人名脱敏：**产出物不外带人名**。
#   模板里逐轮保留了改版记录（「某次改版是谁提的」），这些注释写在内联 JS 里、会随报告
#   一起交付，所以称呼必须是中性的。全交付范围统一口径：提到仿真层用「仿真侧」，
#   提到上游画像层用「画像侧」。
#
#   原先的做法是「源码留原名、落盘前 `html.replace(原名, '需求方')`」—— 那是个**定时炸弹**：
#   一旦正文里真的出现「仿真侧」这个词，它会被一并改掉。既然源码已经中性化，这一层就撤了。
#   注释不进 DOM，故本改动不影响任何渲染文本或图表数据。

# ★ 2026-10-21：**离线图表**。模板顶部那行引的是公网 CDN（jsdelivr）。
#   在断网 / CDN 不通的机器上打开报告，图表一个都出不来，只剩那句红字提示 ——
#   交付件必须是「双击就有图」的，所以这里留一个显式开关：
#
#       REPORT_ECHARTS=<本地 echarts.min.js 路径>    → 把那一行换成**内联**，产出自包含单文件
#       不设                                          → 与之前逐位不变（仍走 CDN）
#
#   不自动探测：报告字节是验收对象，不能因为机器上碰巧有个 echarts 就变样。
#   内联段带 `data-vendor="echarts"`，渲染审计（_p196_render_check.js）据此跳过它 ——
#   那是第三方库，不是报告逻辑，塞进 DOM 沙箱执行只会假报错。
_ECH = os.environ.get("REPORT_ECHARTS") or ""
_CDN_TAG = '<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>'
if _ECH:
    if not os.path.exists(_ECH):
        raise SystemExit(u"★ REPORT_ECHARTS 指向的文件不存在：" + _ECH)
    _lib = io.open(_ECH, encoding="utf-8").read()
    if html.count(_CDN_TAG) != 1:
        raise SystemExit(u"★ 模板里 ECharts 外链不是恰好一处（%d），停下" % html.count(_CDN_TAG))
    # 库里若出现 `</script`，HTML 解析器会提前闭合标签。ECharts 正常不发这样，仍兜一下。
    _lib = _lib.replace("</script", "<\\/script")
    html = html.replace(_CDN_TAG, '<script data-vendor="echarts">\n' + _lib + '\n</script>')
    print(u"  离线图表：内联 %s（%d bytes）" % (os.path.basename(_ECH), len(_lib)))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, "w", encoding='utf-8').write(html)
print("html ->", OUT, os.path.getsize(OUT), "bytes")
print("  HOURS=%d  DAYS=%d  ROUNDS=%d  LASTROUND=%d  NACT=%d  ARCN=%d"
      % (HOURS, DAYS, payload["meta"]["rounds"], LASTROUND, NACT, ARCN))
print("  TOTALFLOW=%d  NCDAYS=%d" % (TOTALFLOW, NCDAYS))
for tok in ("__PAYLOAD__", "__T0__", "__END__", "__NROUNDS__", "__FIRSTROUND__", "__LASTROUND__",
            "__SERIES0__", "__PREWIN_NOTE__", "__PREWIN_APP__", "__PREWIN_SHORT__",
            "__NACT__", "__ARCN__", "__HOURS__", "__NCDAYS__",
            "__TOTALFLOW__",
            "__DAYS__",
            "__DOC_TITLE__", "__REPORT_TITLE__", "__EYEBROW__", "__EVENT_TYPE__",
            "__RUNDIR_NAME__", "__SNAP_PATTERN__", "__RECORDER_FILE__", "__PERSONA_FILE__",
            "__CH_ORDER__", "__CAP_T0__", "__CAP_FIRSTH__", "__CAP_EMODAYS__", "__CAP_CHORDER__",
            "__CAP_Z12__", "__CAP_Z13__", "__CAP_Z32__", "__CAP_41__"):
    if tok in html:
        print("!! 未替换占位符:", tok)
