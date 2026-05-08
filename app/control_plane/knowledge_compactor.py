from __future__ import annotations

from pathlib import Path

from .knowledge_governance import KnowledgeLibraryStore
from .knowledge_domains import KnowledgeGraphStore


class KnowledgeCompactor:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.library = KnowledgeLibraryStore(self.base_dir / "knowledge_library.json")
        self.graph = KnowledgeGraphStore(self.base_dir / "knowledge_graph.json")

    def compact(self) -> dict:
        self.library.load()
        self.graph.load()

        backfilled_sources = 0
        updated_timestamps = 0
        merged_items = 0

        title_to_record_ids = {}
        for record in self.library.list_records(limit=1000):
            title_to_record_ids.setdefault(record.title.strip().lower(), []).append(record.record_id)

        # backfill source ids and updated_ts
        for item in self.graph.items.values():
            key = item.title.strip().lower()
            if not item.source_record_ids and key in title_to_record_ids:
                item.source_record_ids = sorted(set(title_to_record_ids[key]))
                backfilled_sources += 1
            if not item.updated_ts:
                item.updated_ts = item.created_ts
                updated_timestamps += 1

        # exact-title merge inside same domain/block
        seen = {}
        delete_ids = set()
        for item in self.graph.items.values():
            k = (item.domain_id, item.block_id, item.title.strip().lower())
            if k not in seen:
                seen[k] = item
                continue

            primary = seen[k]
            primary.tags = sorted(set(primary.tags + item.tags))
            primary.links = sorted(set(primary.links + item.links))
            primary.source_record_ids = sorted(set(primary.source_record_ids + item.source_record_ids))
            primary.confidence = max(primary.confidence, item.confidence)
            if len(item.summary) > len(primary.summary):
                primary.summary = item.summary
            primary.updated_ts = max(primary.updated_ts, item.updated_ts or item.created_ts)
            delete_ids.add(item.item_id)
            merged_items += 1

        if delete_ids:
            for domain in self.graph.domains.values():
                for block in domain.blocks:
                    block.item_ids = [x for x in block.item_ids if x not in delete_ids]
            for item_id in delete_ids:
                self.graph.items.pop(item_id, None)

        self.graph.save()

        return {
            "backfilled_sources": backfilled_sources,
            "updated_timestamps": updated_timestamps,
            "merged_items": merged_items,
            "graph_summary": self.graph.summary(),
        }