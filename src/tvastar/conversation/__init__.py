"""Conversation module — event-sourced session architecture.

Public API re-exports for convenience.
"""

from .projection import project_fields_from_log, project_run_result
from .records import Record, RecordType, record_from_dict, record_to_dict
from .reducer import reduce
from .session_info import SessionInfo
from .writer import ConversationWriter

__all__ = [
    "ConversationWriter",
    "Record",
    "RecordType",
    "SessionInfo",
    "project_fields_from_log",
    "project_run_result",
    "record_from_dict",
    "record_to_dict",
    "reduce",
]
