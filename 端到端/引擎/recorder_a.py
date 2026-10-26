# -*- coding: utf-8 -*-
"""
记录员 A —— 单轮 7 行快照的**唯一实现**。

抽成模块的原因（2026-10-07）：口径还会继续改，而每一次改都要重出历史轮次的行。
放在这里之后，`t1_runner.py` 与 `rerun_recorder.py` 共用同一份逻辑，**不会各写一版而对不上**。

口径依据：`记录员A_字段口径.md`
    §3.1 情绪统计取**在线者**，空集填 null 不填 0
    §3.2 动作计数 ≠ 互动量
    §3.2.1 `post_cnt` 第 0 轮数发帖、第 1 轮起数引用
    §3.3 scope 与动作语义全中文

⚠️ 2026-10-07 修的两处
    ① `online_cnt` 原来误用「视野门后实际上场人数」。口径.md:45 定义的是
       **本轮窗口 ∩ active_hours ≠ ∅**（硬门口径）。第 0 轮两者碰巧都是 47，
       第 1 轮才分叉（16 vs 5）。现改为由调用方传入 `online_ids`（硬门口径）。
    ② 缺口：原表只有 post/comment/repost 三个动作计数，**点赞不可见**，且无法从
       其它字段反推（沉默可以：名额 − 四个动作数）。现补 `like_cnt`，
       并补 `silent_cnt`（= 在场但未产生任何动作的人数）。
"""
import statistics
from collections import Counter, defaultdict

CHANNELS = ['微博', '小红书', '抖音', '黑猫投诉', '新闻门户', '监管通报']
# ⚠️ 2026-10-07 修：`N_AGENTS` 原写死 102。换事件/换规模（如命题 6 的 500 人场）后
#   `agent_total` 会静默报错值。改为由 `build_rows` 的 `agents` 实参推导（见下）。
GEAR_CN = {0: '沉默', 1: '点赞', 2: '评论', 3: '转发', 4: '引用'}
ACT_CN = {'QUOTE_POST': '引用', 'REPOST': '转发', 'CREATE_COMMENT': '评论',
          'LIKE_POST': '点赞', 'DO_NOTHING': '沉默'}


def build_rows(ctx):
    """ctx 需要的键：
        round         int
        window        [起, 止]  字符串
        agents        [{agent_id, role_type, primary_channel}, ...]   名单（102 人）
        states        {agent_id: {valence, arousal}}
        online_ids    硬门口径的在线集合（set）
        vision        {agent_id: [post_id, ...]}
        posts         {post_id: {like_cnt_sim, comment_cnt_sim, repost_cnt_sim,
                                 channels[], leaves[]}}
        prev_channels {post_id: {渠道, ...}}   ★ 轮首的 channels 快照 —— new_cross_channel_edges
                                               必须与它做**跨轮差集**（2026-10-07 修）
        actions       [{agent_id, role_type, channel, action, action_cn}, ...]
        tok_in/tok_out int
    """
    R = ctx['round']
    window = ctx['window']
    agents = ctx['agents']
    states = ctx['states']
    online_ids = ctx['online_ids']
    vision = ctx['vision']
    posts = ctx['posts']
    prev_channels = ctx.get('prev_channels') or {}
    actions = ctx['actions']
    tok_in = ctx.get('tok_in', 0)
    tok_out = ctx.get('tok_out', 0)

    def uid_list(chf):
        return [a['agent_id'] for a in agents if chf is None or a['primary_channel'] == chf]

    rows = []
    for name, stype, chf in [('全局', 'global', None)] + [(c, 'channel', c) for c in CHANNELS]:
        ids = uid_list(chf)
        onl = [i for i in ids if i in online_ids]
        # ★ R-42（2026-10-08 定案）：**主口径改为「全场」**。
        #   病：原主口径取「在线者」，而在线集合逐轮换人（47→16→5→8→55…）。
        #       实测 30 轮：报出的曲线逐轮变化里 **74% 来自换人**，只有 26% 是同一批人真的变了。
        #       轮 2 报"回升 +0.093"，同一批人其实在跌（−0.033）；轮 3 的"暴跌 −0.166"
        #       两轮在线人群**毫无交集**，真值应为 0。
        #   药：主口径 = scope 内**全部 agent**（分母恒定、逐轮可比）；原来的在线口径**降为辅列**
        #       保留（`*_online`），因为它回答的是另一个问题：「此刻在场的人」情绪如何。
        #   两条线的**差**本身即信息：差大 = 舆情温度集中在少数活跃者身上。
        vs_all = [states[i]['valence'] for i in ids if i in states]
        ar_all = [states[i]['arousal'] for i in ids if i in states]
        vs = [states[i]['valence'] for i in onl if i in states]
        ar = [states[i]['arousal'] for i in onl if i in states]
        acts = [a for a in actions if chf is None or a['channel'] == chf]

        # 本轮新增的跨渠道传播边：(帖, 渠道) 对
        # ★ 2026-10-07 修（BUG 2，两处都错）：
        #   ① 必须与**上一轮**的 channels 做差集 —— 原先算的是"当前 channels 元素个数"，
        #      只要有转发就计数，传播拐点在曲线上完全看不见；
        #   ② 必须按 scope 过滤 —— 原先遍历全部 actions，导致 7 行同值。
        seen = set()
        for a in actions:
            if chf is not None and a['channel'] != chf:
                continue
            if a['action'] != 'REPOST' or a.get('target_post_id') not in posts:
                continue
            pid = a['target_post_id']
            before = set(prev_channels.get(pid, ()))
            for c_ in posts[pid]['channels']:
                if c_ not in before:
                    seen.add((pid, c_))

        vis = set()
        for i in onl:
            vis.update(vision.get(i, []))
        br = Counter()
        for p in vis:
            for lf in posts[p].get('leaves', []):
                br[lf] += 1

        n_like = sum(1 for a in acts if a['action'] == 'LIKE_POST')
        # 沉默 = **上场者里选了 DO_NOTHING 的**（不是"在线但没上场"的人 —— 没上场是没被抽中）
        n_act = sum(1 for a in acts if a['action'] != 'DO_NOTHING')

        rows.append(dict(
            round=R, window=window, scope=name, scope_type=stype,
            agent_total=len(agents), online_cnt=len(onl),
            # ★ R-42：主口径 = **全场**（scope 内全部 agent，分母恒定）
            valence_mean=round(sum(vs_all) / len(vs_all), 4) if vs_all else None,
            valence_std=round(statistics.pstdev(vs_all), 4) if len(vs_all) > 1 else None,
            arousal_mean=round(sum(ar_all) / len(ar_all), 4) if ar_all else None,
            negative_ratio=(round(sum(1 for v in vs_all if v < 0) / len(vs_all), 4)
                            if vs_all else None),
            # ★ R-42 新增：原「在线者」口径，降为辅列（客户/媒体/监管的 prompt 场景照旧可读）
            valence_mean_online=round(sum(vs) / len(vs), 4) if vs else None,
            arousal_mean_online=round(sum(ar) / len(ar), 4) if ar else None,
            negative_ratio_online=(round(sum(1 for v in vs if v < 0) / len(vs), 4)
                                   if vs else None),
            post_cnt=sum(1 for a in acts if a['action'] == 'QUOTE_POST'),
            comment_cnt=sum(1 for a in acts if a['action'] == 'CREATE_COMMENT'),
            repost_cnt=sum(1 for a in acts if a['action'] == 'REPOST'),
            like_cnt=n_like,
            silent_cnt=len(acts) - n_act,          # 上场者里选沉默的（= 名额 − 四个动作数）
            # ★ 2026-10-07 修（R-08 方案 A）：互动量只数**仿真自产**的 `*_sim` 三件套。
            #   采集侧的 like_cnt/comment_cnt 的 capture_date 是事件后 4 个月，展示即泄漏。
            #   ⇒ 第 0 轮 interaction_total = 0（t0 帖热度从 0 起算）。
            interaction_total=sum(posts[p].get('like_cnt_sim', 0) + posts[p].get('comment_cnt_sim', 0)
                                  for p in vis),
            interaction_breakdown=dict(
                like=sum(posts[p].get('like_cnt_sim', 0) for p in vis),
                comment=sum(posts[p].get('comment_cnt_sim', 0) for p in vis),
                repost=sum(posts[p].get('repost_cnt_sim', 0) for p in vis)),
            new_cross_channel_edges=len(seen),
            topic_heat={k: round(v / len(vis), 4) for k, v in br.items()} if vis else {},
            cum_prompt_tokens=tok_in, cum_completion_tokens=tok_out,
        ))

    # 逐 scope 的语义切片（确定性派生）
    for r_ in rows:
        chf = None if r_['scope_type'] == 'global' else r_['scope']
        sem = defaultdict(lambda: defaultdict(int))
        for a in actions:
            if chf is None or a['channel'] == chf:
                sem['%s×%s' % (a['role_type'], a['channel'])][a['action_cn']] += 1
        r_['action_semantic'] = {k: dict(v) for k, v in sem.items()}

        rs = {}
        for a in agents:
            if chf is not None and a['primary_channel'] != chf:
                continue
            rs.setdefault(a['role_type'], dict(online=0, post=0, comment=0, repost=0, like=0,
                                               _v=[], _va=[]))
        for a in agents:
            if chf is not None and a['primary_channel'] != chf:
                continue
            # ★ R-42：`_va` = 该角色的**全部**人（主口径）；`_v` = 其中在线者（辅口径）
            if a['agent_id'] in states:
                rs[a['role_type']]['_va'].append(states[a['agent_id']]['valence'])
            if a['agent_id'] in online_ids:
                d = rs[a['role_type']]
                d['online'] += 1
                if a['agent_id'] in states:
                    d['_v'].append(states[a['agent_id']]['valence'])
        for a in actions:
            if chf is not None and a['channel'] != chf:
                continue
            k = {'QUOTE_POST': 'post', 'CREATE_COMMENT': 'comment',
                 'REPOST': 'repost', 'LIKE_POST': 'like'}.get(a['action'])
            if k:
                rs[a['role_type']][k] += 1
        r_['role_slices'] = {
            k: dict(online=v['online'], post=v['post'], comment=v['comment'],
                    repost=v['repost'], like=v['like'],
                    valence_mean=(round(sum(v['_va']) / len(v['_va']), 4) if v['_va'] else None),
                    valence_mean_online=(round(sum(v['_v']) / len(v['_v']), 4) if v['_v'] else None))
            for k, v in rs.items()}
    return rows
