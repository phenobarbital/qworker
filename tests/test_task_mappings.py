"""Unit tests for BackendDispatcher.load_task_mappings() — TASK-039.

Covers YAML flat format, TOML flat format, and error cases.
"""
import textwrap
from pathlib import Path

import pytest

from qw.backends.dispatch import BackendDispatcher
from qw.backends.models import ContainerTaskMapping


class TestLoadTaskMappingsYAML:
    """Tests for YAML flat-format mapping file loading."""

    def test_load_yaml_flat_format(self, tmp_path: Path):
        """load_task_mappings() parses valid YAML with flat keys."""
        yaml_content = textwrap.dedent("""\
            mappings:
              - task_pattern: "ml_*"
                backend: k8s
                image: ml-worker:latest
              - task_pattern: "report_*"
                backend: docker
                image: report-worker:latest
        """)
        mapping_file = tmp_path / "mappings.yaml"
        mapping_file.write_text(yaml_content)

        result = BackendDispatcher.load_task_mappings(str(mapping_file))

        assert len(result) == 2
        assert all(isinstance(m, ContainerTaskMapping) for m in result)
        assert result[0].task_pattern == "ml_*"
        assert result[0].config.backend == "k8s"
        assert result[0].config.image == "ml-worker:latest"
        assert result[1].task_pattern == "report_*"
        assert result[1].config.backend == "docker"

    def test_load_yaml_flat_format_with_env(self, tmp_path: Path):
        """load_task_mappings() parses YAML with env vars in flat format."""
        yaml_content = textwrap.dedent("""\
            mappings:
              - task_pattern: "worker_*"
                backend: docker
                image: worker:latest
                env:
                  MY_VAR: "hello"
                  DEBUG: "true"
        """)
        mapping_file = tmp_path / "mappings.yaml"
        mapping_file.write_text(yaml_content)

        result = BackendDispatcher.load_task_mappings(str(mapping_file))

        assert len(result) == 1
        assert result[0].config.env["MY_VAR"] == "hello"

    def test_load_yaml_missing_task_pattern_raises(self, tmp_path: Path):
        """load_task_mappings() raises ValueError when task_pattern key is missing."""
        yaml_content = textwrap.dedent("""\
            mappings:
              - backend: docker
                image: worker:latest
        """)
        mapping_file = tmp_path / "mappings.yaml"
        mapping_file.write_text(yaml_content)

        with pytest.raises(ValueError, match="task_pattern"):
            BackendDispatcher.load_task_mappings(str(mapping_file))

    def test_load_yaml_invalid_backend_raises(self, tmp_path: Path):
        """load_task_mappings() raises ValueError for invalid backend names."""
        yaml_content = textwrap.dedent("""\
            mappings:
              - task_pattern: "ml_*"
                backend: invalid_backend
                image: ml-worker:latest
        """)
        mapping_file = tmp_path / "mappings.yaml"
        mapping_file.write_text(yaml_content)

        with pytest.raises(ValueError):
            BackendDispatcher.load_task_mappings(str(mapping_file))

    def test_load_yaml_empty_mappings(self, tmp_path: Path):
        """load_task_mappings() returns empty list for empty mappings."""
        yaml_content = "mappings: []\n"
        mapping_file = tmp_path / "mappings.yaml"
        mapping_file.write_text(yaml_content)

        result = BackendDispatcher.load_task_mappings(str(mapping_file))
        assert result == []

    def test_load_yaml_file_not_found_raises(self):
        """load_task_mappings() raises FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            BackendDispatcher.load_task_mappings("/nonexistent/path/mappings.yaml")

    def test_load_yml_extension(self, tmp_path: Path):
        """load_task_mappings() accepts .yml extension as well as .yaml."""
        yaml_content = textwrap.dedent("""\
            mappings:
              - task_pattern: "etl_*"
                backend: docker
                image: etl-worker:latest
        """)
        mapping_file = tmp_path / "mappings.yml"
        mapping_file.write_text(yaml_content)

        result = BackendDispatcher.load_task_mappings(str(mapping_file))
        assert len(result) == 1
        assert result[0].task_pattern == "etl_*"


class TestLoadTaskMappingsTOML:
    """Tests for TOML flat-format mapping file loading."""

    def test_load_toml_format(self, tmp_path: Path):
        """load_task_mappings() parses valid TOML with flat keys."""
        toml_content = textwrap.dedent("""\
            [[mappings]]
            task_pattern = "ml_*"
            backend = "k8s"
            image = "ml-worker:latest"

            [[mappings]]
            task_pattern = "report_*"
            backend = "docker"
            image = "report-worker:latest"
        """)
        mapping_file = tmp_path / "mappings.toml"
        mapping_file.write_text(toml_content)

        result = BackendDispatcher.load_task_mappings(str(mapping_file))

        assert len(result) == 2
        assert result[0].task_pattern == "ml_*"
        assert result[0].config.backend == "k8s"
        assert result[1].config.backend == "docker"

    def test_load_toml_missing_task_pattern_raises(self, tmp_path: Path):
        """load_task_mappings() raises ValueError when TOML entry misses task_pattern."""
        toml_content = textwrap.dedent("""\
            [[mappings]]
            backend = "docker"
            image = "worker:latest"
        """)
        mapping_file = tmp_path / "mappings.toml"
        mapping_file.write_text(toml_content)

        with pytest.raises(ValueError, match="task_pattern"):
            BackendDispatcher.load_task_mappings(str(mapping_file))

    def test_load_unsupported_extension_raises(self, tmp_path: Path):
        """load_task_mappings() raises ValueError for unsupported file extension."""
        mapping_file = tmp_path / "mappings.json"
        mapping_file.write_text("{}")

        with pytest.raises(ValueError, match="Unsupported"):
            BackendDispatcher.load_task_mappings(str(mapping_file))
