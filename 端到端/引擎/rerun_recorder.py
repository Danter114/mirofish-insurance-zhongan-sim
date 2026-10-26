# -*- coding: utf-8 -*-
"""
从**已落盘的状态快照**重出记录员 A 的行，不重新调用 LLM。

用途：记录员 A 的口径改了（或修了 bug），要把历史轮次的 7 行重出一遍 ——
      动作、情绪、视野全在快照里，没必要为改一个字段再花一次额度。

用法：
    python rerun_recorder.py 0        # 重出第 0 轮
    python rerun_recorder.py 1        # 重出第 1 轮
    python rerun_recorder.py 0 1      # 一次重出多轮

安全：先备份 round_snapshot.jsonl → round_snapshot.jsonl.bak

⚠️ 2026-10-07 对齐的三处口径（与 t1_runner.py 一致）：
    ① online_ids 直接读快照的 `is_online`（第 0 轮快照已含 46 硬门 + 1 个 t0 发帖人例外 = 47）
    ② states 直接读快照的 valence/arousal（R-22b 之后写的就是**轮末值**）
    ③ 新增 prev_channels（轮首 channels 快照）⇒ new_cross_channel_edges 走跨轮差集；
       第 0 轮的发帖动作在快照里只留了一个 `action: "post"` 字符串，这里补成记录员能认的形态。
"""
import argparse, io, json, os, shutil, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import recorder_a
import _sim_config

# ★ 引擎参数化：路径与文件名一律走 `事件配置.json`，代码里不再写死事件目录。
#   用法：python rerun_recorder.py 0 1 2   [--config PATH]
_ap = argparse.ArgumentParser()
_sim_config.add_config_arg(_ap)
_ap.add_argument('rounds', nargs='*', type=int, help='要重出的轮号（缺省 1）')
_a = _ap.parse_args()
CFG = _sim_config.load(path=_a.config, quiet=True)
BASE = CFG.run_dir


def round_len(r):
    """⚠️ 已**弃用**（保留仅为兼容旧调用）。

    原先这里复制了一份 R-11 轮次表（`2 if r < 12 else (6 if r < 24 else 12)`）。
    **2026-10-08 轮次表改为 `2h/4h/8h × 36 轮` 后，这份拷贝立刻成了漂移点** ——
    本文件不 import `t1_runner`（刻意解耦，避免拉起整个 runner 的依赖），
    所以不会自动跟着变。**现在改为不依赖本函数**：`rebuild()` 直接从快照自己的
    `window` 字段推窗口整点（见下），旧表新表的快照都能正确解析，不会再漂。
    """
    return 2 if r < 12 else (4 if r < 24 else 8)


def hours_of_window(win):
    """从快照自己的 `window = ['YYYY-MM-DD HH:MM', 'YYYY-MM-DD HH:MM']` 推窗口整点集合。

    **为什么不用 `round_len(r)`**：那是**外部复制**的轮次表，轮次表一改就漂。
    改成按窗口自身长度算 ⇒ 对旧表（2/6/12）与新表（2/4/8）的快照**同时正确**。
    """
    from datetime import datetime as _dt
    s = _dt.strptime(win[0], '%Y-%m-%d %H:%M')
    e = _dt.strptime(win[1], '%Y-%m-%d %H:%M')
    n = int((e - s).total_seconds() // 3600)
    return {(s.hour + k) % 24 for k in range(n)}


def load_state(r):
    return json.load(open(CFG.snap_path(r), encoding='utf-8'))


def load_round_end(r):
    """该轮的**轮末**状态：第 r 轮快照本身就是轮末；第 0 轮的轮末在 t0轮末状态.json。

    ⚠️ `t0状态_第0轮.json` 里存的是**轮首**值（= t0 初始），拿它出第 0 轮行会得到旧的
       −0.2502；R-22b 之后第 0 轮行必须记轮末的 −0.3410。
    """
    if r == 0:
        p = CFG.t0_end_path()
        if os.path.exists(p):
            return json.load(open(p, encoding='utf-8'))
    return None


PROF = {a['agent_id']: a for a in json.load(
    open(CFG.persona_path, encoding='utf-8'))['agents']}
T0S = load_state(0)
T0POSTS = {p['post_id']: p for p in T0S['injected_posts']}


def prev_channels_of(r):
    """第 r 轮**轮首**的 channels 快照：第 0 轮无前身；其余取第 r−1 轮快照里的 channels。"""
    if r == 0:
        return {}
    try:
        prevS = load_state(r - 1)
    except FileNotFoundError:
        return {pid: {p['channel']} for pid, p in T0POSTS.items()}
    src = prevS.get('posts') or prevS.get('injected_posts') or []
    out = {}
    for p in src:
        ch = p.get('channels')
        out[p['post_id']] = set(ch) if ch else {p['channel']}
    return out


def rebuild(r):
    S = load_state(r)
    E = load_round_end(r)          # 轮末状态（第 0 轮来自 t0轮末状态.json，其余 = S 本身）
    hrs = hours_of_window(S['window'])   # ★ 按快照自身窗口推，不再用外部 round_len(r)

    # ① 在线集合：优先用轮末快照的 is_online / online 名单
    if E is not None and E.get('online'):
        online_ids = set(E['online'])
    elif E is not None:
        online_ids = {a['agent_id'] for a in E['agents'] if a.get('is_online')}
    else:
        online_ids = {a['agent_id'] for a in S['agents'] if a.get('is_online')}
    if not online_ids:                       # 兜底：老快照没写 is_online 时按硬门现算
        online_ids = {a['agent_id'] for a in S['agents']
                      if a['role_type'] != '监管'
                      and set(PROF[a['agent_id']]['active_hours']) & hrs}

    # ② 情绪值 = **轮末**
    if E is not None and 'agents' in E and isinstance(E['agents'], dict):
        states = {aid: dict(valence=v['valence'], arousal=v['arousal'])
                  for aid, v in E['agents'].items()}
    else:
        states = {a['agent_id']: dict(valence=a['valence'], arousal=a['arousal'])
                  for a in S['agents']}

    # ③ 帖子：快照里的计数 + t0 的 leaves（话题叶子不随轮变）
    src = E.get('posts') if (E is not None and E.get('posts')) else \
        (S.get('posts') or S.get('injected_posts') or [])
    posts = {}
    for p in src:
        q = dict(p)
        q['leaves'] = T0POSTS.get(p['post_id'], {}).get('leaves', [])
        q.setdefault('channels', [p['channel']])
        posts[p['post_id']] = q

    # ④ 动作：第 0 轮的发帖在快照里只是 `action: "post"` 字符串，补成记录员形态
    if S.get('actions'):
        actions = S['actions']
    else:
        actions = [dict(agent_id=a['agent_id'], role_type=a['role_type'],
                        channel=a['primary_channel'],
                        action='QUOTE_POST', action_cn='发帖',   # 第 0 轮 post_cnt 数发帖
                        target_post_id='', content='')
                   for a in S['agents'] if a.get('action')]

    # ★ 2026-10-07 修：原先把 prompt+completion 加在一起塞进 `tok_in`、`tok_out` 记 0
    #   ⇒ 重出的行与 `t1_runner.py` 直接落的行**在 tokens 两列上不一致**。
    #   两列必须分开（快照里每人一条 `usage`，与 runner 的累加口径同源）。
    tok_in = sum((a.get('usage') or {}).get('prompt_tokens', 0) for a in actions)
    tok_out = sum((a.get('usage') or {}).get('completion_tokens', 0) for a in actions)
    return recorder_a.build_rows(dict(
        round=r, window=S['window'],
        agents=[dict(agent_id=a['agent_id'], role_type=a['role_type'],
                     primary_channel=a['primary_channel']) for a in S['agents']],
        states=states,
        online_ids=online_ids,
        vision={a['agent_id']: a.get('view', []) for a in S['agents']},
        posts=posts,
        prev_channels=prev_channels_of(r),
        actions=actions,
        tok_in=tok_in, tok_out=tok_out,
    ))


RS = _a.rounds or [1]
jpath = CFG.recorder_path()
shutil.copyfile(jpath, jpath + '.bak')
old = [json.loads(l) for l in open(jpath, encoding='utf-8') if l.strip()]

fresh = []
for R in RS:
    fresh += rebuild(R)

kept = [x for x in old if x['round'] not in set(RS)]
print('旧文件 %d 行（轮 %s）→ 保留 %d 行，重出轮 %s'
      % (len(old), sorted({x['round'] for x in old}), len(kept), sorted(RS)))
with open(jpath, 'w', encoding='utf-8') as f:
    for x in sorted(kept + fresh, key=lambda y: (y['round'], 0 if y['scope_type'] == 'global'
                                                 else 1 + recorder_a.CHANNELS.index(y['scope']))):
        f.write(json.dumps(x, ensure_ascii=False) + '\n')
print('备份 → round_snapshot.jsonl.bak')

for R in sorted(RS):
    print()
    print('── 重出后的第 %d 轮 ──' % R)
    for r in [x for x in fresh if x['round'] == R]:
        print('  %-6s online=%-3s 效价均值=%-8s 负比=%-7s 引用=%-2s 评论=%-2s 转发=%-2s '
              '点赞=%-2s 沉默=%-2s 新边=%-2s 互动=%s'
              % (r['scope'], r['online_cnt'], r['valence_mean'], r['negative_ratio'],
                 r['post_cnt'], r['comment_cnt'], r['repost_cnt'], r['like_cnt'],
                 r['silent_cnt'], r['new_cross_channel_edges'], r['interaction_total']))
