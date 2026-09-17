"""Radiant Ranked — RateMyProfessors data ingestion.

Phase 2 of the project: a clean, resumable pull of every professor at the
target schools (UNC Chapel Hill, Duke) with their aggregate rating data,
via RMP's unofficial GraphQL API.

Entry point: ``python scripts/ingest_rmp.py`` (see ``radiant_ingest.cli``).
"""

from .client import GraphQLError, IngestError, RmpClient
from .persistence import Checkpoint, RawStore, load_raw_professors

__all__ = [
    "RmpClient",
    "IngestError",
    "GraphQLError",
    "Checkpoint",
    "RawStore",
    "load_raw_professors",
]
