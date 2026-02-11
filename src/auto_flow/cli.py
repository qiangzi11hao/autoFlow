"""Command line entry point for the auto_flow package."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

from .workflows import Scheduler, load_config, load_workflows_from_config

DEFAULT_CONFIG_FILENAMES: tuple[str, ...] = (
    "automation.yaml",
    "automation.yml",
    "automation.json",
)


def discover_default_config() -> Optional[Path]:
    for name in DEFAULT_CONFIG_FILENAMES:
        candidate = Path(name)
        if candidate.exists():
            return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automation workflow runner")
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to YAML/JSON configuration file. Defaults to ./automation.yaml",
    )
    parser.add_argument(
        "--workflow",
        "-w",
        action="append",
        dest="workflows",
        help="Workflow name(s) to execute. If omitted, run all configured workflows.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available workflows defined in the configuration and exit.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser


def filter_scheduler_workflows(scheduler: Scheduler, names: Iterable[str]) -> None:
    selected = [wf for wf in scheduler.workflows if wf.workflow.name in names]
    missing = set(names) - {wf.workflow.name for wf in selected}
    if missing:
        raise SystemExit(f"Unknown workflow(s): {', '.join(sorted(missing))}")
    scheduler.workflows = selected


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    configure_logging(args.log_level)

    config_path = args.config or discover_default_config()
    if config_path is None:
        parser.error(
            "No configuration file provided and none of the default files exist "
            f"({', '.join(DEFAULT_CONFIG_FILENAMES)})."
        )

    config = load_config(config_path)
    scheduler, workflows = load_workflows_from_config(config)

    if args.list:
        for workflow in workflows:
            print(workflow.name)
        return 0

    if args.workflows:
        filter_scheduler_workflows(scheduler, args.workflows)

    logging.info("Executing %s workflow(s)", len(scheduler.workflows))
    scheduler.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

