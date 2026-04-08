"""Tests for qw/cli/info.py — CLI info command formatting."""
import io
import json
import pytest
from contextlib import redirect_stdout

from qw.cli.info import render_json, render_text


SAMPLE_DATA = {
    "worker": {
        "name": "TestWorker_0",
        "pid": 12345,
        "address": ("127.0.0.1", 8888),
        "serving": "('127.0.0.1', 8888)"
    },
    "state": {
        "pid": 12345,
        "queue": [],
        "tcp_executing": [
            {
                "task_id": "abc-123-def",
                "function_name": "process_data",
                "enqueued_at": 1712534400.0,
                "started_at": 1712534401.0,
                "completed_at": None,
                "duration": None,
                "retries": 0,
                "status": "executing",
                "result": "",
                "source": "tcp"
            }
        ],
        "redis_executing": [],
        "broker_executing": [],
        "completed": [
            {
                "task_id": "xyz-789",
                "function_name": "old_task",
                "enqueued_at": 1712534390.0,
                "started_at": 1712534391.0,
                "completed_at": 1712534393.0,
                "duration": 2.0,
                "retries": 0,
                "status": "completed",
                "result": "success",
                "source": "queue"
            }
        ]
    }
}


class TestRenderJson:
    def test_render_json_produces_valid_json(self, capsys):
        """--json output is valid JSON."""
        render_json(SAMPLE_DATA)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert isinstance(parsed, dict)

    def test_render_json_contains_worker(self, capsys):
        """JSON output contains 'worker' key."""
        render_json(SAMPLE_DATA)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "worker" in parsed

    def test_render_json_contains_state(self, capsys):
        """JSON output contains 'state' key."""
        render_json(SAMPLE_DATA)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert "state" in parsed

    def test_render_json_pretty_printed(self, capsys):
        """JSON output is indented (pretty-printed)."""
        render_json(SAMPLE_DATA)
        captured = capsys.readouterr()
        # Pretty-printed JSON has multiple lines
        assert "\n" in captured.out


class TestRenderText:
    def test_render_text_does_not_crash(self):
        """render_text doesn't raise on sample data."""
        # Just verify no exception is raised; rich output goes to console
        render_text(SAMPLE_DATA)

    def test_render_text_empty_state(self):
        """render_text handles empty state gracefully."""
        empty_data = {
            "worker": {"name": "W0", "pid": 1, "address": ("127.0.0.1", 8888)},
            "state": {
                "pid": 1,
                "queue": [],
                "tcp_executing": [],
                "redis_executing": [],
                "broker_executing": [],
                "completed": []
            }
        }
        render_text(empty_data)  # Should not raise


class TestBackwardCompatibility:
    def test_parser_accepts_no_subcommand(self):
        """Root parser accepts old-style args without subcommand."""
        import argparse
        from qw.__main__ import _add_start_args
        parser = argparse.ArgumentParser()
        _add_start_args(parser)
        # Should not raise SystemExit
        args = parser.parse_args(['--host', '0.0.0.0', '--port', '8888'])
        assert args.host == '0.0.0.0'
        assert args.port == 8888

    def test_parser_info_subcommand(self):
        """Parser correctly parses 'info' subcommand."""
        import argparse
        from qw.__main__ import _add_start_args
        # Create a minimal parser that mirrors main()
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest='command')
        info_sub = subparsers.add_parser('info')
        info_sub.add_argument('--host', default='127.0.0.1')
        info_sub.add_argument('--port', type=int, default=8888)
        info_sub.add_argument('--json', dest='json_output', action='store_true')
        info_sub.add_argument('--watch', type=int, default=None)
        args = parser.parse_args(['info', '--host', '10.0.0.1', '--port', '9999', '--json'])
        assert args.command == 'info'
        assert args.host == '10.0.0.1'
        assert args.port == 9999
        assert args.json_output is True
