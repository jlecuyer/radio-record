"""Tests for the CLI entry point."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from radio_record.main import main


class TestCLIArgParsing:
    def test_url_argument(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path)])

            MockRecorder.assert_called_once()
            assert MockRecorder.call_args.kwargs["url"] == "http://example.com:8000/listen"

    def test_output_directory(self, tmp_path):
        out_dir = tmp_path / "output"
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(out_dir)])

            assert out_dir.exists()
            assert MockRecorder.call_args.kwargs["output_dir"] == out_dir

    def test_default_timeout(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path)])

            assert MockRecorder.call_args.kwargs["timeout"] == 30

    def test_custom_timeout(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path), "--timeout", "60"])

            assert MockRecorder.call_args.kwargs["timeout"] == 60

    def test_custom_retry_delay(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path), "--retry-delay", "5"])

            assert MockRecorder.call_args.kwargs["retry_delay"] == 5

    def test_verbose_flag(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder, \
             patch("radio_record.main.logging.basicConfig") as mock_basic:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path), "-v"])

            import logging
            mock_basic.assert_called_once()
            assert mock_basic.call_args.kwargs["level"] == logging.DEBUG

    def test_url_from_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RADIO_RECORD_URL", "http://env.example.com:8000/listen")
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["-o", str(tmp_path)])

            assert MockRecorder.call_args.kwargs["url"] == "http://env.example.com:8000/listen"

    def test_no_url_exits_with_error(self, tmp_path, monkeypatch):
        monkeypatch.delenv("RADIO_RECORD_URL", raising=False)
        with pytest.raises(SystemExit):
            main(["-o", str(tmp_path)])

    def test_recorder_run_called(self, tmp_path):
        with patch("radio_record.main.Recorder") as MockRecorder:
            mock_instance = MagicMock()
            MockRecorder.return_value = mock_instance
            main(["http://example.com:8000/listen", "-o", str(tmp_path)])

            mock_instance.run.assert_called_once()
