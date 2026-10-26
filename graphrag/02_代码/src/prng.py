#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prng.py —— 确定性伪随机数发生器（规范定义，跨语言 / 跨机器 / 跨版本可复现）

★ 为什么不用语言自带的 random：
   Python 的 random.Random(seed) 虽然同版本内稳定，但它依赖实现细节
   （random.choices / sample 的内部算法可能随版本变化），且与 JS / Java / Go 的
   实现互不相通。画像库换一台机器、换一个语言，结果就可能不一样。

★ 本实现只用【SHA-256 + 整数运算】，任何语言几行就能复刻：

    seed_material = SHA256( str(master_seed) + "|" + stream_name )          # 32 字节
    block(c)      = SHA256( seed_material + uint64_be(c) )                  # 第 c 块，从 1 开始
    u64           = 把 block 按 8 字节一组切成 uint64（大端）
    u             = (u64 >> 11) * 2^-53      ->  [0,1) 的 double，53 位精度

  只要两边都用 SHA-256 和 IEEE-754 double，输出逐位相同。

★ 每个人的流互相独立：persona 的流用 agent_id 作为 stream_name，
  所以「只重生成某一个人」和「打乱生成顺序」都不会改变结果。
"""
from __future__ import annotations

import hashlib
import math
import struct

TWO53 = float(1 << 53)


class DeterministicRNG:
    """SHA-256 计数器模式 PRNG。接口与 random.Random 的常用子集一致。"""

    def __init__(self, master_seed: int, stream_name: str):
        self.master_seed = master_seed
        self.stream_name = stream_name
        self._seed = hashlib.sha256(
            ("%d|%s" % (master_seed, stream_name)).encode("utf-8")).digest()
        self._ctr = 0
        self._buf = b""
        self._pos = 8          # 触发第一次取块

    # ---------- 基础：字节 / 整数 / 均匀分布 ----------
    def _block(self) -> bytes:
        self._ctr += 1
        return hashlib.sha256(self._seed + struct.pack(">Q", self._ctr)).digest()

    def next_u64(self) -> int:
        if self._pos + 8 > len(self._buf):
            self._buf = self._block()
            self._pos = 0
        v = struct.unpack(">Q", self._buf[self._pos:self._pos + 8])[0]
        self._pos += 8
        return v

    def random(self) -> float:
        """[0, 1) 的 double，53 位精度（与 IEEE-754 尾数一致）"""
        return (self.next_u64() >> 11) / TWO53

    def randint(self, a: int, b: int) -> int:
        """闭区间 [a, b]，拒绝采样，无模偏差"""
        n = b - a + 1
        limit = (1 << 64) - ((1 << 64) % n)
        while True:
            x = self.next_u64()
            if x < limit:
                return a + x % n

    def uniform(self, a: float, b: float) -> float:
        return a + (b - a) * self.random()

    def gauss(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        """Box-Muller。只用两个 uniform，不用缓存的第二值（避免状态歧义）"""
        u1 = self.random()
        if u1 < 1e-12:
            u1 = 1e-12
        u2 = self.random()
        return mu + sigma * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def choice_index(self, weights) -> int:
        """按权重选一个下标。用整数比较避免累加浮点误差的歧义。"""
        total = 0.0
        for w in weights:
            total += w
        r = self.random() * total
        acc = 0.0
        for i, w in enumerate(weights):
            acc += w
            if r < acc:
                return i
        return len(weights) - 1

    def choice(self, population):
        """等概率选一个（与 random.choice 一致）"""
        seq = list(population)
        return seq[self.randint(0, len(seq) - 1)]

    def choices(self, population, weights=None, k=1):
        """有放回抽样（与 random.choices 一致），返回 list"""
        pop = list(population)
        w = list(weights) if weights is not None else [1.0] * len(pop)
        return [pop[self.choice_index(w)] for _ in range(k)]

    def sample(self, population, k: int):
        """无放回等概率抽样（与 random.sample 一致）"""
        pool = list(population)
        out = []
        for _ in range(min(k, len(pool))):
            i = self.randint(0, len(pool) - 1)
            out.append(pool.pop(i))
        return out

    # ---------- 加权无放回抽样（本规范的核心动作） ----------
    def weighted_sample_without_replacement(self, items, weights, k: int):
        """Efraimidis-Spirakis (A-Res)：

            key_i = -ln(u_i) / w_i        u_i ~ U(0,1)
            取 key 最小的 k 个

        等价于 key_i = u_i^(1/w_i) 取最大。并列时按 str(item) 字典序打破，
        保证不同实现给出同一个顺序。
        """
        keyed = []
        for it, w in zip(items, weights):
            if w is None or w <= 0:
                continue
            u = self.random()
            if u <= 0.0:
                u = 5e-324          # 最小正 double，避免 log(0)
            keyed.append((-math.log(u) / float(w), str(it), it))
        keyed.sort(key=lambda t: (t[0], t[1]))
        return [t[2] for t in keyed[:k]]


def persona_rng(master_seed: int, agent_id: str) -> DeterministicRNG:
    """每个人的独立流：以 agent_id 作为 stream 名。

    好处：① 与生成顺序无关，可并行；② 可以只重生成某一个人；
         ③ 两个人之间不会互相影响。
    """
    return DeterministicRNG(master_seed, "persona:" + agent_id)


def digest_of(records, key_fields) -> str:
    """库指纹：把每条记录的指定字段按 agent_id 排序后串起来取 SHA-256。
    换台机器生成后比对这一串，就能确认是否逐位一致。"""
    import json as _json
    lines = []
    for r in sorted(records, key=lambda x: x["agent_id"]):
        lines.append(_json.dumps({k: r.get(k) for k in key_fields},
                                 ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    # 自测：同种子必得同序列；不同种子必得不同序列
    a = DeterministicRNG(20261002, "selftest")
    b = DeterministicRNG(20261002, "selftest")
    c = DeterministicRNG(20261003, "selftest")
    sa = [round(a.random(), 12) for _ in range(5)]
    sb = [round(b.random(), 12) for _ in range(5)]
    sc = [round(c.random(), 12) for _ in range(5)]
    print("同种子一致:", sa == sb, sa[:3])
    print("异种子不同:", sa != sc, sc[:3])
    r = DeterministicRNG(20261002, "pick")
    print("加权无放回抽样:", r.weighted_sample_without_replacement(
        ["A", "B", "C", "D", "E"], [0.5, 0.25, 0.15, 0.07, 0.03], 3))
    print("已知向量 random() 前 3 个 =",
          [round(DeterministicRNG(1, "vec").random(), 15) for _ in [0]][:1])
