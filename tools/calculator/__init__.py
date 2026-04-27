"""Project-type registry and empty-project constructor.

There are no per-job-type 'calculators' anymore. A JobLineItem is just a
container; entries are the truth. This module provides:
- The canonical list of job types Michael's business handles.
- A single create_empty_project() helper to instantiate one.

Entries get populated either by the AI parser (tools/parser/notes_to_line_items.py)
from Michael's quick notes, or manually via the Quote Detail Takeoff tab inline editor.
"""

from server.schemas import JobLineItem

JOB_TYPES = [
    {"key": "retaining_wall",     "label": "Retaining Wall"},
    {"key": "concrete_driveway",  "label": "Concrete Driveway"},
    {"key": "gravel_driveway",    "label": "Gravel Driveway"},
    {"key": "patio",              "label": "Patio"},
    {"key": "land_clearing",      "label": "Land Clearing"},
    {"key": "road_building",      "label": "Road Building"},
    {"key": "foundation",         "label": "Foundation Excavation"},
    {"key": "drainage",           "label": "Perimeter Drains"},
    {"key": "machine_hours",      "label": "Machine Hours / Other"},
]


def get_job_type(key: str):
    return next((j for j in JOB_TYPES if j["key"] == key), None)


def create_empty_project(job_type: str, label: str = None) -> JobLineItem:
    jt = get_job_type(job_type)
    return JobLineItem(
        job_type=job_type,
        label=label or (jt["label"] if jt else job_type.replace("_", " ").title()),
        inputs={"source": "manual"},
        entries=[],
    )


__all__ = ["JOB_TYPES", "get_job_type", "create_empty_project"]
