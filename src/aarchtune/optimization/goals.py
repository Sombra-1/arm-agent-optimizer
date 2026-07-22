"""Goal-specific planning preferences without performance predictions."""

from __future__ import annotations

from dataclasses import dataclass

from aarchtune.optimization.models import OptimizationGoal


@dataclass(frozen=True)
class GoalPreferences:
    parallel_order: tuple[int, ...]
    batch_preference: str
    thread_preference: str
    include_cross_goal_tags: tuple[OptimizationGoal, ...]


GOAL_PREFERENCES: dict[OptimizationGoal, GoalPreferences] = {
    OptimizationGoal.LATENCY: GoalPreferences(
        parallel_order=(1,),
        batch_preference="moderate",
        thread_preference="moderate_to_high",
        include_cross_goal_tags=(),
    ),
    OptimizationGoal.THROUGHPUT: GoalPreferences(
        parallel_order=(2, 4, 1),
        batch_preference="large",
        thread_preference="high",
        include_cross_goal_tags=(),
    ),
    OptimizationGoal.MEMORY: GoalPreferences(
        parallel_order=(1,),
        batch_preference="small",
        thread_preference="conservative",
        include_cross_goal_tags=(),
    ),
    OptimizationGoal.BALANCED: GoalPreferences(
        parallel_order=(1, 2),
        batch_preference="diverse",
        thread_preference="diverse",
        include_cross_goal_tags=(
            OptimizationGoal.LATENCY,
            OptimizationGoal.THROUGHPUT,
            OptimizationGoal.MEMORY,
        ),
    ),
}
