from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any
from enum import Enum
import time

class ToolStatus(Enum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    TIMEOUT = "timeout"

@dataclass
class ArtifactRef:
    path: str
    sha256: str
    size: int

@dataclass
class ToolCallRecord:
    tool_id: str
    name: str
    arguments: Dict[str, Any]
    status: ToolStatus
    duration_ms: int
    timestamp: float = field(default_factory=time.time)
    output_summary: Optional[str] = None
    artifact: Optional[ArtifactRef] = None

    def to_dict(self):
        d = asdict(self)
        d['status'] = self.status.value
        return d
        
@dataclass
class EventRecord:
    event_type: str
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    tool_call_id: Optional[str] = None

    def to_dict(self):
        return asdict(self)
