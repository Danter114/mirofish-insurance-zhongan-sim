# -*- coding: utf-8 -*-
"""把 12 个事件的产物重建进前端 mirofish.html 的 const D={...}。

★ 2026-10-13 v2：新增【展示边】—— 前端图谱之前只有「发帖者 -> 公司」的星形边
   （KG 的 M11 要求每条边带 source_url，导致 48 条 t0 帖只剩 48 条边，EVT-011/012 甚至为 0）。
   现在在前端层额外补三类边，**完全不影响后端 KG 与选人**：
     ① 提及边  发帖者 -> 该帖文提到的其他实体      （数据源：event_pack.docs[].entities）
     ② 共现边  同一条帖文里一起出现的实体两两相连   （数据源：同上）
     ③ 补边    没有 source_url 的 t0 帖也建 poster -> 公司  （后端因 M11 丢弃，前端补回）

★ 踩坑记录（务必保留）：
  第一版用 re.sub(pattern, "const D=" + json.dumps(...) + ";", h)
  —— 替换串里的【反斜杠会被 re 当转义处理】：json.dumps 输出的字面 "\r"（两字符）
     被 re 变成真实的 0x0D 回车，数据行被回车切碎 -> 前端 JS SyntaxError。
  修法：按【行】替换，完全避开 re。
"""
import json, os, re, hashlib
os.chdir(r"H:\众安舆情监测干预比赛\GraphRAG学习\仿真层上游项目代码（建图、定槽位、选人）")
OUT = "交付包/GraphRAG上游_12事件_v31"  # ★ v31
TITLES = {1:"宠物险广告低俗 P图女性私密部位",2:"72岁肝癌患者投诉恶意拒赔",3:"19元低价引流自动扣费",4:"金融监管1号罚单 被罚180万",
 5:"1.6元低价诱导自动扣费",6:"新冠隔离险阳了不赔",7:"隔离险拒赔 上海无中高风险区",8:"诱导老人投保低价引流",
 9:"营销骚扰 上海消保委315前夕点名",10:"众民保美国不可能有电梯广告出圈",11:"百保君子公司暴雷 京东E卡兑付危机",12:"齐鲁包搜索劫持 广告碰瓷惠民保"}
TITLES[12] = "齐鲁保搜索劫持 广告碰瓷惠民保"
pick = lambda d: d if isinstance(d, list) else (d.get("agents") or d.get("members") or [])
def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
def cont(a,b):
    A,B2=bg(a),bg(b)
    return 0 if not A or not B2 else len(A&B2)/float(min(len(A),len(B2)))
def clean(s): return re.sub(r"[\r\n\t\x00-\x1f]", " ", str(s or ""))

STAT = {"提及":0, "共现":0, "补边":0}
EV=[]
for i in range(1,13):
    eid="EVT-%03d"%i; P=OUT+"/"+eid+"/"; O="runs/"+eid+"/"  # ★ v31：产物在 runs/EVT-XXX
    A=json.load(open(P+"A_事件包.json",encoding="utf-8"))
    kg=json.load(open(O+"kg/event_kg_%s.json"%eid,encoding="utf-8"))
    dd=pick(json.load(open(P+"D_Agent名单.json",encoding="utf-8")))
    mm=pick(json.load(open(P+"_中间产物/D_Agent名单_完整.json",encoding="utf-8")))
    by={x["agent_id"]:x for x in mm}
    rel={x["agent_id"]:x for x in json.load(open(O+"relevance/relevance_%s.json"%eid,encoding="utf-8"))["records"]}
    try: t1={x["agent_id"]:x for x in json.load(open(O+"relevance/t1_activation_%s.json"%eid,encoding="utf-8"))["agents"]}
    except Exception: t1={}
    rpf=O+"downstream/%s_t0_real_personas.json"%eid
    rp=json.load(open(rpf,encoding="utf-8")) if os.path.exists(rpf) else {"agents":[]}
    bound={x.get("plays_entity") for x in (rp.get("agents") or [])}
    # ── 实体 ──
    ents=[{"id":"E%d"%(k+1),"name":clean(e["name"]),"kind":e.get("kind"),"type":e.get("type"),
           "role":e.get("role"),"bound":e["name"] in bound} for k,e in enumerate(kg.get("entities") or [])]
    rels=[{"f":r["from"],"t":r["to"],"type":clean(r["type"]),"fact":clean(r.get("fact"))[:240],
           "kind":"core"} for r in (kg.get("relations") or [])]
    # ═══ 建展示边 ═══
    # ★ 展示边的数据源：run 自己的 packs/event_pack.json :: docs
    #   它带 entities / topic_hit / source_url / author_id —— 建边需要的全在这里。
    #   （A_事件包.json 是交付件，只有 A3_posts，字段被裁过）
    _pkf = O + "packs/event_pack.json"
    docs = []
    if os.path.exists(_pkf):
        docs = (json.load(open(_pkf, encoding="utf-8")).get("docs") or [])
    name2id = {e["name"]: e["id"] for e in ents}
    # 别名也映射（KG 里 aliases 里有原文写法）
    for k,e in enumerate(kg.get("entities") or []):
        for al in (e.get("aliases") or []):
            name2id.setdefault(clean(al), "E%d"%(k+1))
    # 公司主体
    comp = [e["id"] for e in ents if e.get("kind")=="company"]
    have = {(r["f"], r["t"], r["type"]) for r in rels}
    def add(f,t,typ,fact,kind):
        if not f or not t or f==t: return
        k=(f,t,typ)
        if k in have: return
        have.add(k)
        rels.append({"f":f,"t":t,"type":typ,"fact":fact[:200],"kind":kind})
        if kind in STAT: STAT[kind]+=1
    for d in docs:
        aid = d.get("author_id"); srcurl = (d.get("source_url") or "").strip()
        src = name2id.get(clean(d.get("author_name"))) or (aid and name2id.get(clean(aid)))
        if not src:
            # 作者可能只以 author_id 出现在 KG
            for e in ents:
                pass
        mentions = []
        for nm in (d.get("entities") or []):
            nm = clean(nm if isinstance(nm,str) else (nm.get("name") if isinstance(nm,dict) else ""))
            eid2 = name2id.get(nm)
            if eid2 and eid2 != src: mentions.append(eid2)
        # ① 提及边：发帖者 -> 提到的实体
        for m in mentions:
            add(src, m, "提及", clean(d.get("raw_content"))[:80], "提及")
        # ② 共现边：同帖实体两两
        for a2 in range(len(mentions)):
            for b2 in range(a2+1, len(mentions)):
                add(mentions[a2], mentions[b2], "共现", "同帖出现", "共现")
        # ③ 补边：没有 source_url 的 t0 帖也建 poster -> 公司
        if src and comp and not srcurl:
            add(src, comp[0], "报道" if d.get("poster_role")=="媒体" else "质疑", clean(d.get("raw_content"))[:80], "补边")
    # ── 实体过滤 / 去重（与后端一致）──
    for e in ents:
        e["edge"]=sum(1 for r in rels if r["f"]==e["id"] or r["t"]==e["id"]); e["actor"]=e["edge"]>0 or e["bound"]
    ents=sorted([e for e in ents if e["actor"]],key=lambda x:-x["edge"])
    keep,drop=[],[]
    for e in ents:
        d2=next((k for k in keep if cont(k["name"],e["name"])>=0.60 or k["name"] in e["name"] or e["name"] in k["name"]),None)
        (drop.append((e,d2)) if d2 else keep.append(e))
    mp={"E%d"%(k+1):"E%d"%(k+1) for k in range(len(kg.get("entities") or []))}
    for e,d2 in drop: mp[e["id"]]=d2["id"]
    outr=[]; seen=set()
    for r in rels:
        f2,t2 = mp.get(r["f"],r["f"]), mp.get(r["t"],r["t"])
        if f2==t2: continue
        k=(f2,t2,r["type"],r.get("kind"))
        if k in seen: continue
        seen.add(k); outr.append(dict(r,f=f2,t=t2))
    rels=outr; ents=keep
    ids={e["id"] for e in ents}
    rels=[r for r in rels if r["f"] in ids and r["t"] in ids]
    for e in ents:
        e["edge"]=sum(1 for r in rels if r["f"]==e["id"] or r["t"]==e["id"]); e["t0"]=e["edge"]>0
    # ── agents ──
    ag=[]
    for x in dd:
        m=by.get(x["agent_id"],{})
        ag.append({"id":clean(x["agent_id"]),"role":x["role_type"],"ch":x["primary_channel"],
          "org":x["role_type"] in ("媒体","监管"),"real":bool(m.get("is_t0_real")),"name":clean(m.get("plays_entity")) or None,
          "orgT":m.get("org_type"),"orgP":m.get("org_position"),"orgS":m.get("org_scope"),"unk":m.get("unknown_fields") or [],
          "g":m.get("gender"),"age":m.get("age"),"reg":m.get("region"),"ut":m.get("urban_tier"),
          "edu":m.get("education"),"occ":m.get("occupation"),"ms":m.get("marital_status"),"inc":m.get("income_band"),
          "circ":m.get("interest_circle"),"fans":m.get("followers"),"inf":x["influence_weight"],"hrs":x["active_hours"],
          "it":x["interest_topics"],"sb":x["stance_bias"],"es":x["emotion_sensitivity"],
          "vb":x["valence_baseline"],"ab":x["arousal_baseline"],"vb0":x["valence_base_0"],"ab0":x["arousal_base_0"],
          "bio":clean(x["persona_bio"]),"tier":(t1.get(x["agent_id"]) or {}).get("tier"),
          "rel":(rel.get(x["agent_id"]) or {}).get("relevance",0),
          "seat":m.get("seat"),"side":m.get("side"),"issue":clean(m.get("issue_leaf")) or None})
    sl=json.load(open(P+"C_槽位表.json",encoding="utf-8")); sl=sl.get("slots") or sl
    posts=A if isinstance(A,list) else (A.get("docs") or A.get("posts") or A.get("records") or [])
    chd={}
    for p in posts:
        if p.get("channel"): chd[p["channel"]]=chd.get(p["channel"],0)+1
        elif p.get("channel_std"): chd[p["channel_std"]]=chd.get(p["channel_std"],0)+1
    EV.append({"id":eid,"tag":"e%d"%i,"name":TITLES[i],"t0":kg.get("t0"),"docs":kg.get("t0_docs") or 0,
      "chdist":chd,"entities":ents,"relations":rels,
      "issues":[{"leaf":x["leaf"],"heat":x.get("heat")} for x in (kg.get("issues") or []) if (x.get("provenance") or "ontology")!="auto:oov"],
      "slots":{"n":len(sl),"head":sum(x.get("headcount") or 0 for x in sl)},
      "t0real":len(rp.get("agents") or []),"agents":ag})
    kc={}
    for r in rels: kc[r.get("kind","core")]=kc.get(r.get("kind","core"),0)+1
    print("  %s %4d人 ｜ 实体%2d 边%3d ｜ %s" % (eid,len(ag),len(ents),len(rels),
          " ".join("%s%d"%(k,v) for k,v in sorted(kc.items()))))
print()
print("  展示边新增: 提及 %d ｜ 共现 %d ｜ 补边 %d ｜ 合计 %d" % (STAT["提及"],STAT["共现"],STAT["补边"],sum(STAT.values())))
DATA_LINE = "const D=" + json.dumps({"events":EV}, ensure_ascii=False, separators=(",", ":")) + ";"
f = "frontend/mirofish.html"
h = open(f, encoding="utf-8").read()
L = h.split("\n")
hit = [k for k, x in enumerate(L) if x.startswith("const D=")]
if not hit:
    print("  ❌ 找不到 const D= 行"); raise SystemExit(1)
L[hit[0]] = DATA_LINE
open(f, "w", encoding="utf-8").write("\n".join(L))
print()
print("  ✅ 前端 %.2f MB ｜ 事件 %d ｜ 合计 %d 人 ｜ 边合计 %d" % (
  os.path.getsize(f)/1024/1024, len(EV), sum(len(e["agents"]) for e in EV), sum(len(e["relations"]) for e in EV)))
