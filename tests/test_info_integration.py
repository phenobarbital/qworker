"""Integration tests for the TCP 'info' command and QClient.info() method.

NOTE: These tests require a running QWorker on localhost:8888 to pass the
      TCP connectivity tests. Tests that require a live worker are marked with
      @pytest.mark.integration and are skipped unless --integration flag is passed.

Unit-level tests (QClient.info method signature, structure) run without a worker.
"""
import pytest
import asyncio
import json
import orjson
from unittest.mock import AsyncMock, patch, MagicMock
from qw.client import QClient
from qw.exceptions import QWException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mock_info_response():
    """Create a sample info response as JSON bytes."""
    state = {
        "worker": {
            "name": "TestWorker_0",
            "address": ("127.0.0.1", 8888),
            "serving": "('127.0.0.1', 8888)",
            "pid": 12345
        },
        "state": {
            "pid": 12345,
            "queue": [],
            "tcp_executing": [],
            "redis_executing": [],
            "broker_executing": [],
            "completed": []
        }
    }
    return orjson.dumps(state)


def make_mock_reader(response_bytes):
    """Create a mock asyncio StreamReader that yields response_bytes then EOF.

    First read() call returns the data. Subsequent calls return b''.
    at_eof() returns False before first read, True after.
    """
    mock_reader = AsyncMock()
    read_calls = [0]

    async def mock_read(n):
        if read_calls[0] == 0:
            read_calls[0] += 1
            return response_bytes
        return b''

    def mock_at_eof():
        return read_calls[0] > 0

    mock_reader.read = mock_read
    mock_reader.at_eof = mock_at_eof
    return mock_reader


# ---------------------------------------------------------------------------
# Unit tests — do not require a live worker
# ---------------------------------------------------------------------------

class TestQClientInfoMethod:
    """Unit tests for QClient.info() — mock the TCP connection."""

    @pytest.mark.asyncio
    async def test_info_method_exists(self):
        """QClient has an info() method."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        assert hasattr(client, 'info')
        assert asyncio.iscoroutinefunction(client.info)

    @pytest.mark.asyncio
    async def test_info_returns_dict(self):
        """QClient.info() returns a dict when response is valid JSON."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            result = await client.info()

        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_info_contains_worker_key(self):
        """Response contains 'worker' key."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            result = await client.info()

        assert "worker" in result

    @pytest.mark.asyncio
    async def test_info_contains_state_key(self):
        """Response contains 'state' key."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            result = await client.info()

        assert "state" in result

    @pytest.mark.asyncio
    async def test_info_state_contains_all_keys(self):
        """State section contains all expected source lists."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            result = await client.info()

        state = result["state"]
        for key in ("queue", "tcp_executing", "redis_executing",
                    "broker_executing", "completed"):
            assert key in state, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_info_worker_metadata(self):
        """Worker section contains name and pid."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            result = await client.info()

        worker = result["worker"]
        assert "name" in worker
        assert "pid" in worker

    @pytest.mark.asyncio
    async def test_info_raises_on_bad_json(self):
        """QClient.info() raises QWException when response is not valid JSON."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])
        mock_reader = make_mock_reader(b'this is not json')
        mock_writer = AsyncMock()

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', return_value=None), \
             patch.object(client, 'close', return_value=None):
            with pytest.raises(QWException):
                await client.info()


# ---------------------------------------------------------------------------
# TCP send verification (unit level)
# ---------------------------------------------------------------------------

class TestInfoTCPCommand:
    """Verify the 'info' command bytes are sent correctly."""

    @pytest.mark.asyncio
    async def test_info_sends_correct_command(self):
        """info() sends 'info' as bytes to the worker."""
        client = QClient(worker_list=[("127.0.0.1", 8888)])

        sent_data = []
        mock_reader = make_mock_reader(make_mock_info_response())
        mock_writer = AsyncMock()

        async def capture_send(data, writer):
            sent_data.append(data)

        with patch.object(client, 'get_worker_connection',
                          return_value=(mock_reader, mock_writer)), \
             patch.object(client, 'sendto_worker', side_effect=capture_send), \
             patch.object(client, 'close', return_value=None):
            await client.info()

        assert len(sent_data) == 1
        assert sent_data[0] == b'info'
