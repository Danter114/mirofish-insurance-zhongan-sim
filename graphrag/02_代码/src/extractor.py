#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extractor.py —— 抽取后端。

★ GraphRAG 的可替换点就在这一个文件。
  RuleExtractor  默认实现：词典 + 正则，纯离线、可复现、零成本。MVP 用它。
  LLMExtractor   接口已定：换成 LightRAG / 内网 LLM 时，输入输出契约不变，
                 其余所有代码一行都不用改。

两个后端必须满足同一个契约：
  extract(text, doc) -> {"entities": [...], "issues": [{"leaf":..,"confidence":..}],
                          "oov_issues": [...], "relations": [...]}
"""
from __future__ import annotations

import json
import re

from common import leaf_index


class Extractor:
    name = "base"

    def extract(self, text: str, doc: dict) -> dict:
        raise NotImplementedError

    # ---- 实体后处理（两个后端共用）
    def classify_entity(self, name: str, doc: dict | None = None):
        g = self.rules["entity_gazetteer"]
        n = str(name).strip()
        if not n or re.match(r"[（(]\d{4}[）)].*号$", n):
            return None
        if any(w in n for w in g["platform"]):
            return None                                  # 平台不是 agent，不进实体
        if any(w in n for w in g["org"]):
            return "监管机构"
        if any(w in n for w in g["product"]):
            return "产品"
        if any(w in n for w in g["company"]):
            return "公司主体"
        if re.match(r"^[\u4e00-\u9fa5]{1,3}(某某|先生|女士|某)$", n):
            return "当事人"
        if doc and doc.get("author_id") and n == doc.get("author_id"):
            return self.ROLE_TO_ENTITY_TYPE.get(doc.get("poster_role"))
        return None

    def normalize_entity(self, name: str):
        """别名归一。铁律：宁可不合并，不要错合并。"""
        n = str(name).strip()
        for fake, real in self.rules["impostor_aliases"].items():
            if n == fake:
                return real, True, True                      # (规范名, 是否别名, 是否冒名)
        canon = self.rules["canonical_company"]
        if "众安" in n and "汽车服务" not in n:
            return canon, (n != canon), False
        return n, False, False


class RuleExtractor(Extractor):
    """词典 + 正则。可复现、零成本、可当 LLM 后端的回归基线。"""
    name = "rule"
    # ★ v2.0：角色 6 类（KOC 已删）。KOC 只作历史数据兜底，适配器已把它归到 KOL。
    # ★ v2.0：6 类角色全部要能成为「发声主体」实体 ——
    #   组长 3.1 ②：T0 的机制是「让扮演发帖人的 agent 真的把那条帖子发出来」，
    #   所以 t0 窗口里每一个发帖人都得有一个可绑的实体，网民也不例外。
    ROLE_TO_ENTITY_TYPE = {"网民": "网民主体", "客户": "当事人", "KOL": "KOL",
                           "媒体": "媒体", "专业背书方": "律所/专业背书方",
                           "监管": "监管机构", "KOC": "KOL"}

    def __init__(self, rules: dict, taxonomy: dict):
        self.rules = rules
        self.tax = taxonomy
        self.leaves = set(leaf_index(taxonomy))
        self.pats = [(re.compile(p["re"], re.I), p["leaf"]) for p in rules["issue_patterns"]]

    def extract(self, text: str, doc: dict) -> dict:
        text = text or ""
        issues, oov = [], set()
        seen = set()
        for rx, leaf in self.pats:
            if leaf not in self.leaves:
                oov.add(leaf)                                # 规则指向了不存在的叶子 -> 词表问题
                continue
            if leaf in seen:
                continue
            if rx.search(text):
                seen.add(leaf)
                issues.append({"leaf": leaf, "confidence": 1.0})
        ents = []
        for raw in (doc.get("entities") or []):
            canon, aliased, impostor = self.normalize_entity(raw)
            typ = self.classify_entity(canon, doc)
            if typ:
                ents.append({"name": canon, "type": typ,
                             "is_impostor": impostor, "alias_of": canon if aliased else None,
                             "raw": str(raw)})
        return {"entities": ents, "issues": issues, "oov_issues": sorted(oov), "relations": []}


class LLMExtractor(Extractor):
    """LLM / LightRAG 后端。契约与 RuleExtractor 完全一致。

    接入步骤：
      1. 装好内网 LLM（LightRAG / vLLM / 内部 API）
      2. 把下面的 _call 换成真实调用，prompt 用 ontology['llm_prompt_template']
      3. run.py 加 --extractor llm
    注意：LLM 输出的 leaf 必须回查词表；词表外的一律进 oov_issues，不得自造叶子。
    """
    name = "llm"
    ROLE_TO_ENTITY_TYPE = RuleExtractor.ROLE_TO_ENTITY_TYPE

    def __init__(self, rules: dict, taxonomy: dict, ontology: dict, endpoint: str | None = None):
        self.rules = rules
        self.tax = taxonomy
        self.onto = ontology
        self.endpoint = endpoint
        self.leaves = set(leaf_index(taxonomy))

    def _call(self, text: str, doc: dict) -> dict:
        raise NotImplementedError(
            "LLMExtractor 尚未接线。把 _call 指向内网 LLM 即可，"
            "prompt 见 config/ontology.json 的 llm_prompt_template；"
            "输出契约见 extractor.py 顶部注释。")

    def extract(self, text: str, doc: dict) -> dict:
        raw = self._call(text, doc)
        issues, oov = [], set()
        for it in raw.get("issues", []):
            leaf = it.get("leaf")
            if leaf in self.leaves:
                issues.append({"leaf": leaf, "confidence": float(it.get("confidence", 0.8))})
            else:
                oov.add(str(leaf))
        oov |= set(raw.get("oov_issues") or [])
        ents = []
        for e in raw.get("entities", []):
            canon, aliased, impostor = self.normalize_entity(e.get("name", ""))
            if e.get("is_impostor"):
                impostor = True
            typ = e.get("type") or self.classify_entity(canon, doc)
            if typ:
                ents.append({"name": canon, "type": typ, "is_impostor": impostor,
                             "alias_of": canon if aliased else None, "raw": e.get("name")})
        return {"entities": ents, "issues": issues, "oov_issues": sorted(oov),
                "relations": raw.get("relations", [])}


def build_extractor(kind: str, rules: dict, taxonomy: dict, ontology: dict):
    if kind == "llm":
        return LLMExtractor(rules, taxonomy, ontology)
    return RuleExtractor(rules, taxonomy)
