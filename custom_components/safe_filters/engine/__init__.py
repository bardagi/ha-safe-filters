"""Pure-Python processing engine for Safe Filters (no Home Assistant imports)."""

from .combine import CombineMethod, combine
from .options import build_config, is_single, source_ids
from .pipeline import (
    OutlierConfig,
    Pipeline,
    PipelineConfig,
    PipelineResult,
    PipelineStatus,
)
from .stages import LowPassStage, MovingAverageStage, OutlierStage, RateLimitStage, Stage
from .validation import SourceReading, SourceStatus, ValidationConfig, validate

__all__ = [
    "CombineMethod",
    "LowPassStage",
    "MovingAverageStage",
    "OutlierConfig",
    "OutlierStage",
    "Pipeline",
    "PipelineConfig",
    "PipelineResult",
    "PipelineStatus",
    "RateLimitStage",
    "SourceReading",
    "SourceStatus",
    "Stage",
    "ValidationConfig",
    "build_config",
    "combine",
    "is_single",
    "source_ids",
    "validate",
]
