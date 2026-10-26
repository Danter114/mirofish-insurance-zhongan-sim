# -*- coding: utf-8 -*-
"""报告层三处 LLM 正文 —— e2e_v3 运行目录版（交付外工作脚本，2026-10-20）

与 `_p250_report\\llm_fill.py` 同一套规则（第二十七/二十八轮），只把路径改成**运行目录**：

    python -X utf8 _llm_fill_run.py --run-dir 中间产物\\图谱\\EVT-001
    python -X utf8 _llm_fill_run.py --run-dir 中间产物\\图谱\\EVT-001 --verify

流程：规则报告先出 → node 沙箱抠出 #r22 / #r31 / #advBox 的**渲染后纯文本** →
原样喂模型写解读 → 写 `<run-dir>\\_llm_text.json` → 再跑一次报告段即挂上。
文件不存在/调用失败 ⇒ 不写该键，报告回退纯规则文案（R3/R5）。

五把锁：R1 数字不出正文（本脚本现场核，任何数字不在原文即判 FAIL、不落盘）；
R2 事实判定不出 LLM；R3 失败必降级；R4 落盘 + prompt/source 指纹；R5 可离线。
凭据只从环境或 .env 读，**不打印、不写进任何产物**。
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.request

SPOTS = [
    ("L1", "r22", "2.2 情绪在各角色、各渠道上的分布",
     "写一段 120–200 字的「交叉解读」：把角色维度与渠道维度放在一起看，"
     "说清二者的情绪差异到底来自「谁在该平台上发声」，还是「平台本身的属性」；"
     "若有反例（某平台在各角色上都更负/更温和），要点明出来。", 120, 200),
    ("L2", "r31", "3.1 关键影响节点",
     "写一段 100–180 字的「事件链解读」：说明这些关键事件的先后顺序与相互关系，"
     "指出哪一个最早改变了走向，以及后续事件是在放大它还是只是延续它。", 100, 180),
    ("L3", "advBox", "五 · 应对建议",
     "写一段 150–220 字的「综合研判」：说明这份建议清单的优先级理由 —— "
     "最该先做什么、为什么先做它、哪些可以缓。要扣住建议里已经写明的触发条件，"
     "不要泛泛谈原则。", 150, 220),
]

PROMPT_HEAD = """你是舆情研判报告的编辑。下面是一份多智能体仿真推演报告中「{spot}」的正文（由程序按固定规则从数据现算生成，数字全部真实）。

任务：{task}

硬性要求：
1. 只能使用下面正文中**已经出现**的数字、角色、渠道与事件；不得引入任何新数字、新事件、新渠道、新角色，也不得做任何算术推导出新数字。
2. 不得推翻或改写正文已有的结论（例如自然平息判定、风险等级、关键时段）；也不得提出正文**没有支撑**的判断——
   正文若已说明某个渠道/角色只有个位数样本、或把某现象归因于样本量小，就**不能**再把它上升为「平台属性」「机制性差异」一类结论；
   正文没有给出的因果方向（谁导致谁），不要自己编。
3. 不要逐句复述正文：只写正文没有明说、但由这些数放在一起才看得出的判断；不要用「首先 / 其次 / 最后」这类套话，不要罗列。
4. 长度严格控制在 {lo}–{hi} 个中文字符（含标点），**超过 {hi} 字即不合格**。
5. 输出**纯文本**：不要 Markdown 标题、不要列表符号、不要用引号把整段包裹、不要任何前言或结语说明。
6. 直接输出这一段文字。

正文：
<<<
{text}
>>>
"""

NUM = re.compile(r"-?\d+(?:\.\d+)?")


def base_dir():
    return os.path.dirname(os.path.abspath(__file__))


def find_check():
    for p in (os.path.join(base_dir(), "_p196_render_check.js"),
              ):
        if os.path.exists(p):
            return p
    raise SystemExit("!! 找不到 _p196_render_check.js")


def load_env():
    out = {}
    p = os.environ.get("SIM_ENV") or ""
    if not os.path.exists(p):
        return out, p
    for ln in io.open(p, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, _, v = ln.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    # 进程环境变量优先（与引擎一致）
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out, p


def rendered_text(html_path, ids):
    r = subprocess.run(["node", find_check(), html_path, "--text"] + list(ids),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError("render check 失败：" + (r.stderr or "")[-400:])
    parts = {}
    for b in re.split(r"\n=+\n### ", r.stdout)[1:]:
        m = re.match(r"(\S+?)　", b)
        if not m:
            continue
        body = b.split("=+\n", 1)
        parts[m.group(1)] = (body[1].strip() if len(body) > 1 else b.strip())
    return parts


def call_llm(env, prompt, timeout=180):
    url = env["LLM_BASE_URL"].rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": env["LLM_MODEL_NAME"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 900,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + env["LLM_API_KEY"],
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        d = json.loads(resp.read().decode("utf-8"))
    return d["choices"][0]["message"]["content"].strip()


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("\n\n", "<br><br>").replace("\n", "<br>"))


def num_set(s):
    out = set()
    for t in NUM.findall(s):
        out.add(t.lstrip("-"))
    return out


def run(run_dir, verify_only=False):
    cfg_p = os.path.join(run_dir, "事件配置.json")
    cfg = json.load(io.open(cfg_p, encoding="utf-8"))
    html = os.path.join(run_dir, cfg.get("out_html") or (cfg.get("event_id", "EVT") + "_仿真运行可视化报告.html"))
    # ★ 2026-10-21：与报告 HTML **同目录**（与 viz_build 的读取位置同一契约）。
    #   旧布局里 html 就在 run_dir 下，这里退化成原来的路径，行为不变。
    out_p = os.path.join(os.path.dirname(os.path.abspath(html)), "_llm_text.json")
    if not os.path.exists(html):
        raise SystemExit("!! 报告还没生成：" + html)

    ids = [s[1] for s in SPOTS]
    parts = rendered_text(html, ids)

    if verify_only:
        if not os.path.exists(out_p):
            raise SystemExit("!! 无 _llm_text.json：" + out_p)
        d = json.load(io.open(out_p, encoding="utf-8"))
        bad = 0
        for k, v in (d.get("items") or {}).items():
            src = parts.get(dict((s[0], s[1]) for s in SPOTS)[k], "")
            src = re.split(r"模型撰写 ·", src)[0]
            srcn = num_set(src)
            miss = sorted({t for t in NUM.findall(v.get("raw", "")) if t.lstrip("-") not in srcn})
            if miss:
                bad += 1
                print("  %s FAIL  原文没有的数字：%s" % (k, "、".join(miss[:12])))
            else:
                print("  %s OK    全部数字均可在原文中找到（%d 字）" % (k, v.get("chars", 0)))
        print("\n通过 %d / %d" % ((len(d.get("items") or {}) - bad), len(d.get("items") or {})))
        return bad == 0

    env, env_p = load_env()
    if not env.get("LLM_API_KEY"):
        raise SystemExit("!! 未找到 LLM_API_KEY（%s）" % env_p)
    print("env:", env_p, "｜ model:", env.get("LLM_MODEL_NAME"))

    out = {"event": cfg.get("event_id", ""), "model": env.get("LLM_MODEL_NAME", ""),
           "items": {}, "audit": {}}
    for key, hid, spot, task, lo, hi in SPOTS:
        text = parts.get(hid, "")
        if len(text) < 60:
            print("  !! %s 段落 %s 取到的正文过短（%d 字），跳过" % (key, hid, len(text)))
            continue
        prompt = PROMPT_HEAD.format(spot=spot, task=task, lo=lo, hi=hi, text=text)
        got = call_llm(env, prompt)
        # ★ 2026-10-21：超长改成**最多压三次**（原来只压一次 —— 实测 L3 压完 226 字仍超 220 上限，
        #   却照样落盘，等于限长规则形同虚设）。三次仍超就落盘但打标，宁可带一个已知偏差，
        #   也不让整段解读凭空消失（丢段落比超 6 个字更伤报告）。
        over = False
        for _try in range(3):
            if len(got) <= hi:
                break
            print("  !! %s 超长 %d 字（上限 %d）⇒ 第 %d 次压缩" % (key, len(got), hi, _try + 1))
            got = call_llm(env, prompt + "\n注意：上一次输出 %d 字，超过了 %d 字上限。这次压到 %d 字以内，只保留最关键的判断。\n"
                           % (len(got), hi, hi))
            print("     → %d 字" % len(got))
        if len(got) > hi:
            over = True
            print("  !! %s 压三次仍超长（%d > %d）⇒ 仍落盘，标记 over_limit" % (key, len(got), hi))
        srcn = num_set(text)
        miss = sorted({t for t in NUM.findall(got) if t.lstrip("-") not in srcn})
        if miss:
            print("  !! %s 模型引入原文没有的数字：%s ⇒ 重写一次" % (key, "、".join(miss[:12])))
            got = call_llm(env, prompt + "\n注意：上一次输出出现了正文中没有的数字（%s），这次一个都不要出现。\n"
                           % "、".join(miss[:12]))
            miss = sorted({t for t in NUM.findall(got) if t.lstrip("-") not in srcn})
            if miss:
                print("  !! %s 仍出现数值越界（%s）⇒ 按 R1 不落该键" % (key, "、".join(miss[:12])))
                continue
        out["items"][key] = {
            "text": esc(got),
            "raw": got,
            "chars": len(got),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        }
        if over:
            out["items"][key]["over_limit"] = True
        print("  %s OK  %d 字  %s" % (key, len(got), got[:40].replace("\n", " ")))
    if not out["items"]:
        print("!! 三段都没写成，不落盘（报告保持纯规则文案）")
        return False
    out["audit"] = {
        "llm_written": list(out["items"]),
        "rule_written": ["模块一全部", "模块二 2.1 / 2.2 除 L1 外的全部段落",
                         "模块三 3.1 除 L2 外的全部段落", "模块四全部",
                         "模块五建议条目（规则触发）"],
        "numeric_check": "每个数字都已核对存在于它读到的渲染正文中（R1）",
        "note": "LLM 只新增解读段，不改写任何由公式现算的结论；本文件缺失时报告回退到纯规则文案。",
    }
    io.open(out_p, "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print("->", out_p, os.path.getsize(out_p), "bytes")
    return True


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    ok = run(os.path.abspath(a.run_dir), verify_only=a.verify)
    sys.exit(0 if ok else 1)
