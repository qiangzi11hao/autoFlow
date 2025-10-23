"""Workflow orchestration utilities."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .automation import (
    AutomationAPI,
    AutomationError,
    HotkeyListener,
    SafetySettings,
    ensure_focus,
    initialize_safety,
)

LOGGER = logging.getLogger(__name__)


class TaskContext:
    """Runtime context for tasks executed within a workflow."""

    def __init__(self, api: AutomationAPI, safety: SafetySettings, shared: Optional[Dict[str, Any]] = None):
        self.api = api
        self.safety = safety
        self.shared = shared or {}


class Task:
    """Basic interface for automation tasks."""

    name: str = "task"
    delay_before: float = 0.0

    def run(self, ctx: TaskContext) -> None:  # pragma: no cover - interface definition
        raise NotImplementedError


class MoveTask(Task):
    name = "move"

    def __init__(self, x: float, y: float, duration: float = 0.0):
        self.x = x
        self.y = y
        self.duration = duration

    def run(self, ctx: TaskContext) -> None:
        ensure_focus(ctx.safety)
        ctx.api.move_to(self.x, self.y, duration=self.duration)


class ClickTask(Task):
    name = "click"

    def __init__(self, button: str = "left"):
        self.button = button

    def run(self, ctx: TaskContext) -> None:
        ensure_focus(ctx.safety)
        ctx.api.click(button=self.button)


class TypeTask(Task):
    name = "type"

    def __init__(self, text: str, interval: float = 0.0):
        self.text = text
        self.interval = interval

    def run(self, ctx: TaskContext) -> None:
        ensure_focus(ctx.safety)
        ctx.api.write(self.text, interval=self.interval)


class HotkeyTask(Task):
    name = "hotkey"

    def __init__(self, *keys: str):
        if len(keys) < 1:
            raise ValueError("HotkeyTask requires at least one key")
        self.keys = keys

    def run(self, ctx: TaskContext) -> None:
        ensure_focus(ctx.safety)
        ctx.api.hotkey(*self.keys)


class WaitTask(Task):
    name = "wait"

    def __init__(self, seconds: float):
        self.seconds = seconds

    def run(self, ctx: TaskContext) -> None:
        ctx.api.sleep(self.seconds)


TASK_REGISTRY: Dict[str, Callable[..., Task]] = {
    MoveTask.name: MoveTask,
    ClickTask.name: ClickTask,
    TypeTask.name: TypeTask,
    HotkeyTask.name: HotkeyTask,
    WaitTask.name: WaitTask,
}


@dataclass
class Workflow:
    """A sequence of tasks to execute in order."""

    name: str
    tasks: Sequence[Task]

    def run(self, ctx: TaskContext) -> None:
        LOGGER.info("Starting workflow '%s'", self.name)
        for task in self.tasks:
            if getattr(task, "delay_before", 0):
                ctx.api.sleep(task.delay_before)
            LOGGER.debug("Running task %s in workflow %s", task.name, self.name)
            task.run(ctx)
        LOGGER.info("Completed workflow '%s'", self.name)


@dataclass
class ScheduledWorkflow:
    workflow: Workflow
    run_at: datetime


@dataclass
class Scheduler:
    """Simple scheduler that executes workflows sequentially."""

    safety: SafetySettings
    workflows: List[ScheduledWorkflow] = field(default_factory=list)

    def add_workflow(self, workflow: Workflow, run_at: Optional[datetime] = None) -> None:
        if run_at is None:
            run_at = datetime.now()
        self.workflows.append(ScheduledWorkflow(workflow=workflow, run_at=run_at))
        LOGGER.debug("Scheduled workflow '%s' at %s", workflow.name, run_at.isoformat())

    def run(self) -> None:
        if not self.workflows:
            LOGGER.warning("No workflows scheduled; nothing to do")
            return

        if len(self.workflows) > 1:
            self.workflows.sort(key=lambda wf: wf.run_at)

        listener = initialize_safety(self.safety)
        try:
            with listener:
                api = AutomationAPI()
                ctx = TaskContext(api=api, safety=self.safety)
                for scheduled in self.workflows:
                    self._maybe_wait_until(scheduled.run_at, listener)
                    if listener.cancelled:
                        LOGGER.warning("Automation cancelled before workflow '%s'", scheduled.workflow.name)
                        break
                    scheduled.workflow.run(ctx)
        except AutomationError as exc:
            LOGGER.error("Automation failed: %s", exc)
            raise

    def _maybe_wait_until(self, run_at: datetime, listener: HotkeyListener) -> None:
        while True:
            now = datetime.now()
            remaining = (run_at - now).total_seconds()
            if remaining <= 0:
                return
            LOGGER.info("Waiting %ss before next workflow", round(remaining, 2))
            listener.wait(min(remaining, 1.0))
            if listener.cancelled:
                return


def build_task(spec: Dict[str, Any]) -> Task:
    """Instantiate a task from configuration."""

    if "type" not in spec:
        raise ValueError("Task configuration missing 'type'")

    task_type = spec["type"].lower()
    factory = TASK_REGISTRY.get(task_type)
    if factory is None:
        raise ValueError(f"Unknown task type '{task_type}'")

    kwargs = {k: v for k, v in spec.items() if k != "type"}
    task = factory(**kwargs)
    delay = spec.get("delay_before")
    if delay:
        setattr(task, "delay_before", delay)
    return task


def build_workflow(name: str, spec: Dict[str, Any]) -> Workflow:
    if "tasks" not in spec:
        raise ValueError(f"Workflow '{name}' missing 'tasks' definition")

    tasks = [build_task(task_spec) for task_spec in spec["tasks"]]
    return Workflow(name=name, tasks=tasks)


def load_config(path: os.PathLike[str] | str) -> Dict[str, Any]:
    path_obj = Path(path)
    if not path_obj.exists():
        raise FileNotFoundError(path)

    text = path_obj.read_text(encoding="utf8")
    if path_obj.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency missing
            raise RuntimeError(
                "YAML configuration requested but PyYAML is not installed"
            ) from exc
        return yaml.safe_load(text)
    return __import__("json").loads(text)


def load_workflows_from_config(config: Dict[str, Any]) -> tuple[Scheduler, List[Workflow]]:
    safety_config = config.get("safety", {})
    safety = SafetySettings(**safety_config)

    workflows_config = config.get("workflows")
    if not workflows_config:
        raise ValueError("Configuration must define at least one workflow")

    workflows: List[Workflow] = []
    scheduler = Scheduler(safety=safety)

    for name, spec in workflows_config.items():
        workflow = build_workflow(name, spec)
        workflows.append(workflow)
        schedule_spec = spec.get("schedule", {})
        run_at = None
        if "delay_seconds" in schedule_spec:
            run_at = datetime.now() + timedelta(seconds=float(schedule_spec["delay_seconds"]))
        elif "run_at" in schedule_spec:
            run_at = datetime.fromisoformat(schedule_spec["run_at"])
        scheduler.add_workflow(workflow, run_at=run_at)

    return scheduler, workflows

