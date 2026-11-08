"""Composition root: the one place that decides which concrete classes are wired together."""
from __future__ import annotations

from dataclasses import dataclass

from .agents.llm import ChatModel, GeminiChat
from .config import Settings
from .domain.geo import GeoService
from .domain.rate_selector import PlanTypeSelector
from .repositories.base import PriceRepository
from .repositories.sqlite_repo import SqlitePriceRepository
from .services.agent_service import AgentService
from .services.estimate_service import EstimateService
from .services.extraction import CodeExtractor, KeywordCodeExtractor
from .services.gemini_extractor import FallbackExtractor, GeminiCodeExtractor
from .services.note_reader import GeminiTranscriber, NoteReader


@dataclass
class Container:
    repo: PriceRepository
    estimator: EstimateService
    extractor: CodeExtractor
    agents: AgentService
    reader: NoteReader

    @classmethod
    def build(cls, settings: Settings) -> "Container":
        if settings.database_url:
            from .repositories.postgres_repo import PostgresPriceRepository   # lazy: SQLite-only runs don't need psycopg
            repo: PriceRepository = PostgresPriceRepository(settings.database_url)
        else:
            repo = SqlitePriceRepository(settings.db_path)
        extractor: CodeExtractor = KeywordCodeExtractor(repo)
        if settings.gemini_api_key:
            extractor = FallbackExtractor(
                GeminiCodeExtractor(repo, settings.gemini_api_key, settings.gemini_model), extractor)
        agent_model = GeminiChat(settings.gemini_api_key, settings.gemini_model) if settings.gemini_api_key else None
        transcriber = GeminiTranscriber(settings.gemini_api_key, settings.gemini_model) if settings.gemini_api_key else None
        return cls.from_repo(repo, extractor, agent_model, reader=NoteReader(transcriber))

    @classmethod
    def from_repo(cls, repo: PriceRepository, extractor: CodeExtractor | None = None,
                  agent_model: ChatModel | None = None, agent_service: AgentService | None = None,
                  reader: NoteReader | None = None) -> "Container":
        return cls(
            repo=repo,
            estimator=EstimateService(repo, PlanTypeSelector(), GeoService()),
            extractor=extractor or KeywordCodeExtractor(repo),
            agents=agent_service or AgentService(agent_model),
            reader=reader or NoteReader(),
        )
