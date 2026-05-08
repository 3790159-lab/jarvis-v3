from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9а-яіїєґ_]+", "_", value, flags=re.IGNORECASE)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or f"item_{uuid.uuid4().hex[:6]}"


def _tokenize(value: str) -> set[str]:
    value = re.sub(r"[^a-z0-9а-яіїєґ ]+", " ", (value or "").lower(), flags=re.IGNORECASE)
    return {x for x in value.split() if len(x) >= 3}


def _similarity(a: str, b: str) -> float:
    sa = _tokenize(a)
    sb = _tokenize(b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / max(1, union)


class KnowledgeItem(BaseModel):
    item_id: str
    title: str
    summary: str
    domain_id: str
    block_id: str
    preferred_service: str = ""
    tags: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    created_ts: float = 0.0
    updated_ts: float = 0.0


class KnowledgeBlock(BaseModel):
    block_id: str
    name: str
    description: str = ""
    item_ids: list[str] = Field(default_factory=list)


class KnowledgeDomain(BaseModel):
    domain_id: str
    name: str
    description: str = ""
    blocks: list[KnowledgeBlock] = Field(default_factory=list)


class KnowledgeGraphStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.domains: dict[str, KnowledgeDomain] = {}
        self.items: dict[str, KnowledgeItem] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"domains": [], "items": []}, ensure_ascii=False, indent=2), encoding="utf-8")

        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.domains = {}
        self.items = {}

        for item in raw.get("domains", []):
            domain = KnowledgeDomain.model_validate(item)
            self.domains[domain.domain_id] = domain

        for item in raw.get("items", []):
            k = KnowledgeItem.model_validate(item)
            self.items[k.item_id] = k

    def save(self) -> None:
        data = {
            "domains": [x.model_dump(mode="json") for x in self.domains.values()],
            "items": [x.model_dump(mode="json") for x in self.items.values()],
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def ensure_domain(self, name: str, description: str = "") -> KnowledgeDomain:
        domain_id = _slug(name)
        domain = self.domains.get(domain_id)
        if domain:
            return domain

        domain = KnowledgeDomain(
            domain_id=domain_id,
            name=name,
            description=description,
            blocks=[],
        )
        self.domains[domain_id] = domain
        self.save()
        return domain

    def ensure_block(self, domain_name: str, block_name: str, description: str = "") -> KnowledgeBlock:
        domain = self.ensure_domain(domain_name)
        block_id = _slug(block_name)

        for block in domain.blocks:
            if block.block_id == block_id:
                return block

        block = KnowledgeBlock(
            block_id=block_id,
            name=block_name,
            description=description,
            item_ids=[],
        )
        domain.blocks.append(block)
        self.save()
        return block

    def _find_merge_candidate(
        self,
        domain_id: str,
        block_id: str,
        title: str,
        summary: str,
    ) -> KnowledgeItem | None:
        title_slug = _slug(title)
        for item in self.items.values():
            if item.domain_id != domain_id or item.block_id != block_id:
                continue
            if _slug(item.title) == title_slug:
                return item
            if _similarity(item.summary, summary) >= 0.84:
                return item
        return None

    def add_or_merge_item(
        self,
        domain_name: str,
        block_name: str,
        title: str,
        summary: str,
        preferred_service: str = "",
        tags: list[str] | None = None,
        source_record_id: str = "",
        links: list[str] | None = None,
        confidence: float = 0.0,
    ) -> KnowledgeItem:
        domain = self.ensure_domain(domain_name)
        block = self.ensure_block(domain_name, block_name)

        existing = self._find_merge_candidate(
            domain_id=domain.domain_id,
            block_id=block.block_id,
            title=title,
            summary=summary,
        )

        if existing:
            existing.tags = sorted(set(existing.tags + list(tags or [])))
            existing.links = sorted(set(existing.links + list(links or [])))
            if source_record_id and source_record_id not in existing.source_record_ids:
                existing.source_record_ids.append(source_record_id)
            if len(summary) > len(existing.summary):
                existing.summary = summary
            if preferred_service and not existing.preferred_service:
                existing.preferred_service = preferred_service
            existing.confidence = round(max(existing.confidence, float(confidence or 0.0)), 3)
            existing.updated_ts = time.time()
            self.save()
            return existing

        item = KnowledgeItem(
            item_id=f"knowledge_item_{uuid.uuid4().hex[:10]}",
            title=title,
            summary=summary,
            domain_id=domain.domain_id,
            block_id=block.block_id,
            preferred_service=preferred_service or "",
            tags=sorted(set(list(tags or []))),
            source_record_ids=[source_record_id] if source_record_id else [],
            links=sorted(set(list(links or []))),
            confidence=float(confidence or 0.0),
            created_ts=time.time(),
            updated_ts=time.time(),
        )
        self.items[item.item_id] = item

        for dblock in domain.blocks:
            if dblock.block_id == block.block_id and item.item_id not in dblock.item_ids:
                dblock.item_ids.append(item.item_id)

        self.save()
        return item

    def classify_text(self, title: str, text: str, category: str = "agent_guidance", tags: list[str] | None = None) -> list[dict[str, str]]:
        lower = (text or "").lower()
        tags = list(tags or [])
        placements: list[dict[str, str]] = []

        def add(domain_name: str, block_name: str, reason: str):
            placements.append({
                "domain_name": domain_name,
                "block_name": block_name,
                "reason": reason,
            })

        if any(x in lower for x in ["claude", "anthropic", "codegen", "refactor", "debug", "patch", "implementation", "programming"]):
            add("Programming and Codegen", "Claude and Engineering", "Text discusses Claude/codegen/programming guidance.")

        if any(x in lower for x in ["openai", "gpt", "planning", "qa", "validation", "review"]):
            add("Planning and Quality", "OpenAI Planning and QA", "Text discusses planning/QA guidance.")

        if any(x in lower for x in ["n8n", "automation", "workflow", "webhook", "integration", "telegram", "google workspace"]):
            add("Automation and Integrations", "Workflow and Connector Operations", "Text discusses workflows/integrations/connectors.")

        if any(x in lower for x in ["memory", "context", "obsidian", "knowledge base", "reuse prior lessons", "retention"]):
            add("Memory and Knowledge", "Context Reuse and Knowledge Retention", "Text discusses memory/context/knowledge reuse.")

        if any(x in lower for x in ["agent", "agents", "communicat", "coordination", "consultation", "delegate", "second opinion", "support agent"]):
            add("Agent Communication and Coordination", "Coordination Patterns and Consultation", "Text discusses agent coordination/communication.")

        if any(x in lower for x in ["safety", "safe", "guard", "approval", "reliability", "trust", "risk"]):
            add("Safety and Reliability", "Guardrails and Approval Logic", "Text discusses safety/reliability/approval logic.")

        if not placements:
            fallback_domain = category.replace("_", " ").title()
            first_tag = tags[0].replace("_", " ").title() if tags else "General Notes"
            add(fallback_domain, first_tag, "Fallback classification from category/tags.")

        dedup = []
        seen = set()
        for item in placements:
            key = (item["domain_name"], item["block_name"])
            if key not in seen:
                dedup.append(item)
                seen.add(key)
        return dedup

    def list_domains(self, include_items: bool = True) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for domain in sorted(self.domains.values(), key=lambda x: x.name.lower()):
            row = {
                "domain_id": domain.domain_id,
                "name": domain.name,
                "description": domain.description,
                "blocks": [],
            }
            for block in sorted(domain.blocks, key=lambda x: x.name.lower()):
                item_rows = []
                if include_items:
                    for item_id in block.item_ids:
                        item = self.items.get(item_id)
                        if item:
                            item_rows.append(item.model_dump(mode="json"))
                row["blocks"].append({
                    "block_id": block.block_id,
                    "name": block.name,
                    "description": block.description,
                    "items": item_rows,
                })
            out.append(row)
        return out

    def find_relevant(
        self,
        domain_names: list[str],
        preferred_service: str = "",
        limit: int = 8,
    ) -> list[KnowledgeItem]:
        domain_ids = {_slug(x) for x in domain_names if x}
        items = []

        for item in self.items.values():
            if item.domain_id not in domain_ids:
                continue
            if preferred_service and item.preferred_service and item.preferred_service != preferred_service:
                continue
            items.append(item)

        items.sort(key=lambda x: (-x.confidence, -x.updated_ts))
        return items[:max(1, limit)]

    def summary(self) -> dict[str, Any]:
        blocks_count = 0
        for domain in self.domains.values():
            blocks_count += len(domain.blocks)
        return {
            "domains_count": len(self.domains),
            "blocks_count": blocks_count,
            "items_count": len(self.items),
        }