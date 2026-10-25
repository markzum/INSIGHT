"""Дедупликация потока материалов: точные совпадения, MinHash и кластеры событий."""

from dedup.core import Deduplicator, IdentityConflict
from dedup.models import DedupResult, Document

__all__ = ["Deduplicator", "DedupResult", "Document", "IdentityConflict"]
__version__ = "1.0.0"
