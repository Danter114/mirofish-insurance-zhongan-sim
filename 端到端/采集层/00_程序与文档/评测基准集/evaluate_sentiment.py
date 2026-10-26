# -*- coding: utf-8 -*-
"""情感识别评测脚本

用本目录下的人工标注基准集（EVT-001_gt.json ~ EVT-013_gt.json）评测
13 个事件 processed 数据里的 sentiment 字段，输出：
  三分类准确率 / 负面准确率(precision) / 负面召回率(recall) / 混淆矩阵 / 分歧清单

用法（任意工作目录均可）：
    python evaluate_sentiment.py

指标口径：
  三分类准确率 = 与基准集完全一致的条数占比
  负面准确率   = 负面类 precision = TP / (TP + FP)
  负面召回率   = 负面类 recall    = TP / (TP + FN)
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # 数据包根目录

gt, pred = {}, []
for n in range(1, 14):
    with open(os.path.join(HERE, "EVT-%03d_gt.json" % n), encoding="utf-8-sig") as f:
        gt.update(json.load(f))
    folder = [d for d in os.listdir(ROOT) if d.startswith("EVT-%03d_" % n)][0]
    with open(os.path.join(ROOT, folder, "EVT-%03d_processed.json" % n), encoding="utf-8") as f:
        for r in json.load(f):
            pred.append((r["post_id"], r["sentiment"]))

n_total = len(pred)
n_correct = sum(1 for pid, p in pred if gt.get(pid) == p)
acc = n_correct / n_total
tp = sum(1 for pid, p in pred if p == "负面" and gt.get(pid) == "负面")
fp = sum(1 for pid, p in pred if p == "负面" and gt.get(pid) != "负面")
fn = sum(1 for pid, p in pred if p != "负面" and gt.get(pid) == "负面")
prec = tp / (tp + fp) if (tp + fp) else 0
rec = tp / (tp + fn) if (tp + fn) else 0

print("评测集: %d 条（13 事件全量）" % n_total)
print("三分类准确率: %.2f%%（%d/%d）  目标 >= 88%%" % (acc * 100, n_correct, n_total))
print("负面准确率(precision): %.2f%%（TP=%d FP=%d）  目标 >= 90%%" % (prec * 100, tp, fp))
print("负面召回率(recall): %.2f%%（TP=%d FN=%d）  目标 >= 85%%" % (rec * 100, tp, fn))
print()
print("混淆矩阵（行=真值, 列=预测）:")
labels = ["负面", "中性", "正面"]
mat = {(g, p): 0 for g in labels for p in labels}
for pid, p in pred:
    mat[(gt.get(pid, "?"), p)] = mat.get((gt.get(pid, "?"), p), 0) + 1
print("%8s | %s" % ("", " | ".join("%4s" % l for l in labels)))
for g in labels:
    print("%8s | %s" % (g, " | ".join("%4d" % mat[(g, p)] for p in labels)))
print()
print("分歧清单（GT -> 预测）:")
n_dis = 0
for pid, p in pred:
    g = gt.get(pid)
    if g != p:
        print("  %s: GT=%s 预测=%s" % (pid, g, p))
        n_dis += 1
if n_dis == 0:
    print("  （无分歧）")
