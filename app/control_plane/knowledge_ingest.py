from __future__ import annotations

from pathlib import Path

from .knowledge_governance import (
    KnowledgeGovernanceManager,
    KnowledgeLibraryStore,
    ReviewQueueStore,
    NegativeKnowledgeStore,
)


class KnowledgeIngestor:
    def __init__(self, base_dir: Path, lesson_store, policy_store, strategy_store, experiment_registry, auth_manager) -> None:
        self.manager = KnowledgeGovernanceManager(
            base_dir=base_dir,
            lesson_store=lesson_store,
            policy_store=policy_store,
            strategy_store=strategy_store,
            experiment_registry=experiment_registry,
            auth_manager=auth_manager,
        )

    def ingest_text(self, title: str, text: str, source_type: str = "article", category: str = "agent_guidance", auto_apply: bool = True):
        return self.manager.ingest_text(
            title=title,
            text=text,
            source_type=source_type,
            category=category,
            auto_apply=auto_apply,
        )