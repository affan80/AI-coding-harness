import os
import json
import hashlib
import time
from pathlib import Path
from typing import Any, Dict, Optional
from .models import ToolCallRecord, ArtifactRef, ToolStatus, EventRecord

ARTIFACT_INLINE_LIMIT = 4096  # 4 KiB
SUMMARY_LIMIT = 400

class RunStore:
    def __init__(self, base_dir: str = "runs"):
        self.run_id = f"run_{int(time.time())}"
        self.run_dir = Path(base_dir) / self.run_id
        self.artifacts_dir = self.run_dir / "artifacts"
        self.events_file = self.run_dir / "events.jsonl"
        self.tool_calls_file = self.run_dir / "tool-calls.jsonl"
        self._tool_counter = 0

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(exist_ok=True)

    def _atomic_write_json(self, filename: str, data: Any):
        """Atomically write JSON data to avoid interrupted writes leaving corrupt state."""
        target_path = self.run_dir / filename
        temp_path = self.run_dir / f"{filename}.tmp"
        
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
            
        # Atomic replace
        os.replace(temp_path, target_path)

    def save_state(self, request: Optional[Dict] = None, session: Optional[Dict] = None, 
                   goals: Optional[Dict] = None, plan: Optional[Dict] = None):
        if request is not None:
            self._atomic_write_json("request.json", request)
        if session is not None:
            self._atomic_write_json("session.json", session)
        if goals is not None:
            self._atomic_write_json("goals.json", goals)
        if plan is not None:
            self._atomic_write_json("plan.json", plan)

    def record_event(self, event_type: str, payload: Dict[str, Any], tool_call_id: Optional[str] = None):
        event = EventRecord(event_type=event_type, payload=payload, tool_call_id=tool_call_id)
        with open(self.events_file, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    def record_tool_call(self, name: str, arguments: Dict[str, Any], status: ToolStatus, 
                         duration_ms: int, raw_output: str) -> ToolCallRecord:
        self._tool_counter += 1
        tool_id = f"tool-{self._tool_counter:04d}"
        
        output_bytes = raw_output.encode("utf-8")
        size = len(output_bytes)
        
        artifact_ref = None
        summary = None
        
        if size > ARTIFACT_INLINE_LIMIT:
            sha256 = hashlib.sha256(output_bytes).hexdigest()
            artifact_path = self.artifacts_dir / f"{tool_id}-output.txt"
            with open(artifact_path, "wb") as f:
                f.write(output_bytes)
            
            artifact_ref = ArtifactRef(
                path=f"artifacts/{tool_id}-output.txt",
                sha256=sha256,
                size=size
            )
            summary = raw_output[:SUMMARY_LIMIT] + ("..." if len(raw_output) > SUMMARY_LIMIT else "")
        else:
            summary = raw_output

        record = ToolCallRecord(
            tool_id=tool_id,
            name=name,
            arguments=arguments,
            status=status,
            duration_ms=duration_ms,
            output_summary=summary,
            artifact=artifact_ref
        )
        
        with open(self.tool_calls_file, "a") as f:
            f.write(json.dumps(record.to_dict()) + "\n")
            
        self.record_event("tool_call", {"status": status.value, "duration_ms": duration_ms}, tool_call_id=tool_id)
        
        return record
