from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _encode_message(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8") + body


def _decode_message(stream) -> dict:
    headers = {}
    while True:
        line = stream.readline()
        if not line:
            raise RuntimeError("EOF while waiting for MCP response")
        if line in {b"\r\n", b"\n"}:
            break
        key, value = line.decode("utf-8").split(":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers["content-length"])
    body = stream.read(length)
    return json.loads(body.decode("utf-8"))


class ServerProtocolTests(unittest.TestCase):
    def test_initialize_and_list_tools(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")

        process = subprocess.Popen(
            [sys.executable, "-m", "ninova_mcp"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            assert process.stdin is not None
            assert process.stdout is not None

            process.stdin.write(
                _encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1.0"},
                        },
                    }
                )
            )
            process.stdin.flush()
            initialize_response = _decode_message(process.stdout)
            self.assertEqual(initialize_response["result"]["serverInfo"]["name"], "ninova-mcp")

            process.stdin.write(
                _encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/list",
                    }
                )
            )
            process.stdin.flush()
            tools_response = _decode_message(process.stdout)
            tool_names = [tool["name"] for tool in tools_response["result"]["tools"]]
            self.assertIn("get_dashboard", tool_names)
            self.assertIn("download_resource", tool_names)
            self.assertIn("get_course_announcements", tool_names)
            self.assertIn("get_course_assignments", tool_names)
            self.assertIn("get_course_class_files", tool_names)
            self.assertIn("get_dashboard_assignments", tool_names)
            self.assertIn("get_course_grades", tool_names)
            self.assertIn("get_course_message_board", tool_names)
            self.assertIn("get_course_overview", tool_names)
            self.assertIn("sync_all_courses", tool_names)
            self.assertIn("get_updates", tool_names)
        finally:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
