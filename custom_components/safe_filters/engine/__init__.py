"""Pure-Python processing engine for Safe Filters (no Home Assistant imports)."""

from .combine import CombineMethod, combine, effective_weights
from .compensation import OffsetLearner
from .options import build_config, is_single, source_ids
from .pipeline import (
    OutlierConfig,
    Pipeline,
    PipelineConfig,
    PipelineResult,
    PipelineStatus,
)
from .stages import (
    EmaStage,
    LowPassStage,
    MovingAverageStage,
    OutlierStage,
    RateLimitStage,
    Stage,
)
from .validation import SourceReading, SourceStatus, ValidationConfig, validate

__all__ = [
    "CombineMethod",
    "EmaStage",
    "LowPassStage",
    "MovingAverageStage",
    "OffsetLearner",
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
    "effective_weights",
    "is_single",
    "source_ids",
    "validate",
]
