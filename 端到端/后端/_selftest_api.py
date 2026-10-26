# -*- coding: utf-8 -*-
r"""接口自检：在测试端口起真服务，发真 HTTP 请求，逐条断言。

    python -I _selftest_api.py

覆盖：健康检查 / 列事件 / 事件详情 / 404 / 跑仿真（空转）/ 并发拒绝 /
      状态轮询 / 报告直出 / 新建事件的参数校验 / LLM 配置写入不泄露明文 /
      采集层的检索作业（开单 → 收单：失败要带清单回来，不是抛 500）。
"""
import io
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app        # noqa: E402
import paths      # noqa: E402

PORT = 5099
BASE = "http://127.0.0.1:%d" % PORT
EID = sys.argv[1] if len(sys.argv) > 1 else "EVT-001"
fails = []
_srv = None


def req(method, path, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read()
            ct = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in ct else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def check(cond, label, extra=""):
    print("%s %s%s" % ("✔" if cond else "✘", label, ("   " + extra) if extra else ""))
    if not cond:
        fails.append(label)


def main():
    global _srv
    _srv = ThreadingHTTPServer(("127.0.0.1", PORT), app.Handler)
    _srv.daemon_threads = True
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    time.sleep(0.4)

    print("=" * 78)
    print("接口自检 ｜ %s" % BASE)
    print("=" * 78)

    print("\n[1] 健康检查 / 列事件")
    s, b = req("GET", "/api/health")
    check(s == 200 and b.get("ok"), "GET /api/health", "python %s" % b.get("python"))
    s, b = req("GET", "/api/events")
    ids = [e["event_id"] for e in b["events"]]
    check(s == 200 and EID in ids, "GET /api/events 含 " + EID, "共 %d 个" % len(ids))

    print("\n[2] 事件详情")
    s, b = req("GET", "/api/events/" + EID)
    check(s == 200, "GET /api/events/<eid>")
    check(b["config"]["event_id"] == EID, "配置读出来了")
    check("precheck" in b and "inventory" in b, "详情里带预检与产物盘点")
    check(b["inventory"]["complete"], "产物盘点：已跑完",
          "轮 %d/%d" % (b["inventory"]["rounds_done"], b["inventory"]["rounds_executed"]))

    print("\n[3] 凭据不外泄")
    lv = b["config"]["llm"]
    check("api_key" not in lv, "llm 段里没有裸 `api_key` 键")
    check(lv.get("api_key_set") is False, "只回布尔量 api_key_set（当前未填）")
    # ★ 强检查：塞一个哨兵钥匙进去，确认它不会出现在**任何**响应里
    p_cfg = paths.config_path(EID)
    _saved = json.load(open(p_cfg, encoding="utf-8"))["llm"]
    _sentinel = "sk-SENTINEL-DO-NOT-LEAK-1234567890"
    _restore_llm(p_cfg, dict(_saved, api_key=_sentinel))
    s, b2 = req("GET", "/api/events/" + EID)
    check(_sentinel not in json.dumps(b2, ensure_ascii=False), "哨兵 api_key 不出现在事件详情里")
    check(b2["config"]["llm"]["api_key_set"] is True, "但 api_key_set 如实变 true")
    _restore_llm(p_cfg, _saved)

    print("\n[4] 不存在的事件 → 404")
    s, b = req("GET", "/api/events/NO_SUCH_EVENT")
    check(s == 404, "GET 不存在的事件 得 404", str(b.get("error", ""))[:40])
    s, b = req("POST", "/api/events/NO_SUCH/run")
    check(s == 404, "POST 不存在的事件 得 404")

    print("\n[5] 注入路径穿越 → 挡住")
    s, b = req("GET", "/api/events/..%2f..%2fapp")
    check(s in (403, 404), "非法事件 id 被挡", "得 %d" % s)

    print("\n[6] 跑仿真（空转秒退）")
    s, b = req("POST", "/api/events/%s/run" % EID)
    check(s == 200 and b.get("state") == "running", "POST /run 起任务", "pid=%s" % b.get("pid"))
    s2, b2 = req("POST", "/api/events/%s/run" % EID)
    check(s2 == 409, "并发第二次被拒 409", str(b2.get("error", ""))[:40])

    print("\n[7] 状态轮询")
    t0 = time.time()
    while time.time() - t0 < 60:
        s, b = req("GET", "/api/events/%s/status" % EID)
        if b["job"]["state"] != "running":
            break
        time.sleep(0.3)
    check(b["job"]["state"] == "done", "任务跑完 done", "= %s" % b["job"]["state"])
    check(isinstance(b.get("log_tail"), list) and b["log_tail"], "状态里带日志尾",
          "%d 行" % len(b.get("log_tail") or []))

    print("\n[8] 报告直出")
    s, b = req("GET", "/api/events/%s/report" % EID)
    check(s == 200 and isinstance(b, bytes) and len(b) > 100000, "GET 报告 HTML",
          "%d bytes" % (len(b) if isinstance(b, bytes) else -1))
    check(isinstance(b, bytes) and b"<html" in b[:2000].lower(), "确实是 HTML")

    print("\n[9] 新建事件的参数校验")
    s, b = req("POST", "/api/events", {"event_id": "../../etc", "event_brief": "x",
                                       "t0_time": "2026-01-01 00:00"})
    check(s == 400, "非法 event_id 被拒", str(b.get("error", ""))[:40])
    s, b = req("POST", "/api/events", {"event_id": "T_NEW_API", "event_brief": "",
                                       "t0_time": "2026-01-01 00:00"})
    check(s == 400, "空 event_brief 被拒", str(b.get("error", ""))[:40])
    s, b = req("POST", "/api/events", {"event_id": EID, "event_brief": "x",
                                       "t0_time": "2026-01-01 00:00"})
    check(s == 409, "重名事件被拒 409", str(b.get("error", ""))[:40])

    print("\n[10] LLM 配置写入（不泄露、不误删）")
    p = paths.config_path(EID)
    before = json.load(open(p, encoding="utf-8")).get("llm", {})
    s, b = req("POST", "/api/events/%s/llm" % EID, {"model_name": "test-model-xyz"})
    check(s == 200, "写 model_name")
    after = json.load(open(p, encoding="utf-8")).get("llm", {})
    check(after.get("model_name") == "test-model-xyz", "落盘生效")
    check(after.get("api_key", "MISSING") == before.get("api_key", "MISSING"),
          "空 api_key 提交**没有**抹掉原有钥匙")
    check(b["llm"]["model_name_set"], "返回的是布尔量而非明文")
    _restore_llm(p, before)          # 还原，别把测试值留在交付包里
    check(json.load(open(p, encoding="utf-8"))["llm"] == before, "已还原")

    # ── [11] 采集层：检索作业（开单 → 按单落档 → 收单）────────────────────
    # 这一组验的是**HTTP 那一层**：两条新路由真的把 `检索作业.py` 叫起来了，
    # 且"校验没过"是作为 passed=False 回来的（HTTP 200），不是抛 500 ——
    # 界面要能看见缺哪一项，所以失败必须带清单回去。
    print("\n[11] 采集层：检索作业的开单 / 收单（HTTP 层）")
    s, b = req("GET", "/api/collect/status")
    sample = next((x for x in (b.get("samples") or []) if x["event_id"] == 1), None) if s == 200 else None
    if not sample:
        check(False, "找得到 EVT-001 样本（开单要用它的参数卡）", "status=%s" % s)
    else:
        card, job = sample["card"], None
        s, b = req("POST", "/api/collect/job/open", {"card": card})
        job = b.get("job") if isinstance(b, dict) else None
        check(s == 200 and b.get("ok") and job and os.path.isdir(job),
              "POST /api/collect/job/open", "%s" % (os.path.basename(job) if job else b.get("error")))
        names = sorted(os.path.basename(x) for x in (b.get("files") or []))
        check(len(names) >= 4 and "检索作业单.md" in names and "查询记录.csv" in names,
              "开单产出四件模板", "、".join(names))
        check(len(b.get("queries") or []) == 6, "6 个渠道都给了建议检索串",
              "%d 条" % len(b.get("queries") or []))

        # 空作业直接收单 ⇒ 必须**回 200 + passed=False**，并点名缺什么
        s, b = req("POST", "/api/collect/job/collect", {"job": job, "card": card})
        check(s == 200 and b.get("ok") and b.get("passed") is False,
              "空作业收单：200 但 passed=False", "fails=%d" % len(b.get("fails") or []))
        check(bool(b.get("fails")), "失败项带回来了（不是抛 500）",
              "；".join(b.get("fails") or [])[:90])

        # 把 EVT-001 的现成快照当成"这次检索的成果"填进去 ⇒ 应通过
        snap = sample["snapshot_dir"]
        for fn in os.listdir(snap):
            shutil.copy(os.path.join(snap, fn), os.path.join(job, "原文快照", fn))
        src_dir = os.path.dirname(card)
        shutil.copy(os.path.join(src_dir, "EVT-001_采集清单.json"),
                    os.path.join(job, "采集清单.json"))
        with open(os.path.join(job, "查询记录.csv"), "w", encoding="utf-8-sig", newline="") as f:
            f.write("channel,query,queried_at,result_count,opened_count,kept_count,drop_reason,note\n")
            for ch, q, kept in (("微博", "众安保险 宠物险", 0), ("抖音", "众安保险 宠物险 广告低俗", 1),
                                ("小红书", "众安保险 宠物险 广告低俗", 2), ("新闻门户", "众安保险 宠物险", 0),
                                ("黑猫投诉", "众安保险", 0), ("监管通报", "众安保险 通报 处罚", 0)):
                f.write("%s,%s,2026-10-01 15:00,0,0,%d,,\n" % (ch, q, kept))
        s, b = req("POST", "/api/collect/job/collect", {"job": job, "card": card})
        check(s == 200 and b.get("passed") is True, "填好后收单通过",
              "fails=%s" % ("、".join(b.get("fails") or []) or "无"))
        check((b.get("raw") or {}).get("count") == 3,
              "收单顺带出的 raw", "%s 条 / %s 字段" % ((b.get("raw") or {}).get("count"),
                                                    (b.get("raw") or {}).get("fields")))
        check(b.get("metas") == b.get("claimed_kept") == 3, "H8 对账数一致",
              "快照 %s ／ 记录 %s" % (b.get("metas"), b.get("claimed_kept")))

        # 越界：作业目录不在运行目录下 ⇒ 403（别让它变成任意目录跑子进程的口子）
        s, b = req("POST", "/api/collect/job/collect", {"job": os.path.abspath(os.sep), "card": card})
        check(s == 403, "运行目录之外的 job 被拒 403", str((b or {}).get("error", ""))[:50])

        shutil.rmtree(job, ignore_errors=True)    # 测试件不留

    # ── [12] 前后端连接：四页要的那几个只读接口 ────────────────────────────
    # 这一组不是"能跑仿真"，而是**前端每一页发得出请求、拿得到同一场的数据**。
    # 名册是 2026-10-21 补的：之前页面上烤死的是 EVT-001 的人，换场就选不了人。
    print("\n[12] 前后端连接：看板 / 图谱 / 名册 / 对话状态")
    s, b = req("GET", "/api/events/%s/board" % EID)
    bd = (b.get("board") or {}) if isinstance(b, dict) else {}
    # 顶层键按产物自己的名字断言（kpi / timeline / channels …），别照抄脑子里的名字
    check(s == 200 and b.get("ok") and bd.get("kpi") and bd.get("timeline"),
          "GET /board", "%s 张卡 / %s 个时间点" % (len(bd.get("kpi") or []), len(bd.get("timeline") or [])))
    s, b = req("GET", "/api/events/%s/graph" % EID)
    g = (b.get("graph") or {}) if isinstance(b, dict) else {}
    check(s == 200 and b.get("ok") and g.get("NODES"),
          "GET /graph", "%s 个节点 / %s 条边" % (len(g.get("NODES") or []), len(g.get("EDGES") or [])))
    s, b = req("GET", "/api/events/%s/roster" % EID)
    grp = b.get("groups") if isinstance(b, dict) else None
    check(s == 200 and b.get("ok") and isinstance(grp, list) and grp,
          "GET /roster", "%s 组 / %s 人" % (len(grp or []), b.get("total") if isinstance(b, dict) else "?"))
    # 名册必须是**本场**的：组内人数合计要等于 total，且每个人带齐卡片要的四个字段
    if isinstance(grp, list) and grp:
        ppl = [p for g in grp for p in (g.get("people") or [])]
        check(len(ppl) == (b.get("total") or -1), "名册人数与 total 一致", "%d / %d" % (len(ppl), b.get("total") or -1))
        check(all(isinstance(p.get("id"), str) and p.get("id") for p in ppl), "每个人都有 id")
        check(all(set(p) >= {"id", "channel", "label", "bio"} for p in ppl),
              "字段与 `gen_roster.py` 烘的那份一致（id/channel/label/bio）")
        ids = [p.get("id") for p in ppl]
        check(len(ids) == len(set(ids)), "id 无重复")
    s, b = req("GET", "/api/events/%s/chat" % EID)
    check(s == 200 and "llm" in (b or {}), "GET /chat（徽标要的 llm 布尔量，不回显钥匙）")

    print("=" * 78)
    _srv.shutdown()
    if fails:
        print("✘ 不通过：%s" % "；".join(fails))
        sys.exit(1)
    print("✔ 全部通过")


def _restore_llm(path, llm):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["llm"] = llm
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
