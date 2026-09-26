import unittest
import os
import json
import tempfile
import shutil
from pathlib import Path
from harness.telemetry.store import RunStore
from harness.telemetry.models import ToolStatus

class TestRunStore(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.store = RunStore(base_dir=self.test_dir)
        
    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_atomic_state_writes(self):
        self.store.save_state(request={"test": "req"}, plan={"steps": []})
        req_path = self.store.run_dir / "request.json"
        plan_path = self.store.run_dir / "plan.json"
        
        self.assertTrue(req_path.exists())
        self.assertTrue(plan_path.exists())
        
        with open(req_path, "r") as f:
            data = json.load(f)
            self.assertEqual(data["test"], "req")
            
        # Ensure runs/ is in gitignore
        # (This is handled by root .gitignore, but we just check the file exists)
        
    def test_record_tool_call_small(self):
        record = self.store.record_tool_call(
            name="test_tool",
            arguments={"arg": 1},
            status=ToolStatus.OK,
            duration_ms=100,
            raw_output="Small output"
        )
        
        self.assertEqual(record.tool_id, "tool-0001")
        self.assertEqual(record.output_summary, "Small output")
        self.assertIsNone(record.artifact)
        
        # Check tool-calls.jsonl
        with open(self.store.tool_calls_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["tool_id"], "tool-0001")
            
        # Check events.jsonl
        with open(self.store.events_file, "r") as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["tool_call_id"], "tool-0001")
            self.assertEqual(data["event_type"], "tool_call")
            
    def test_record_tool_call_large(self):
        large_output = "A" * 5000  # > 4 KiB
        record = self.store.record_tool_call(
            name="big_tool",
            arguments={},
            status=ToolStatus.OK,
            duration_ms=500,
            raw_output=large_output
        )
        
        self.assertIsNotNone(record.artifact)
        self.assertEqual(record.artifact.size, 5000)
        
        artifact_path = self.store.run_dir / record.artifact.path
        self.assertTrue(artifact_path.exists())
        
        with open(artifact_path, "r") as f:
            content = f.read()
            self.assertEqual(content, large_output)
            
        self.assertTrue(record.output_summary.endswith("..."))
        self.assertEqual(len(record.output_summary), 403) # 400 + '...'
        
if __name__ == '__main__':
    unittest.main()
