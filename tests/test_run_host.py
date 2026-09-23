"""Адрес сервера: по умолчанию только 127.0.0.1, вся сеть только по явному запросу."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("run", Path(__file__).resolve().parents[1] / "run.py")
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)


def test_default_host_is_loopback(monkeypatch):
    monkeypatch.delenv(run.HOST_ENV, raising=False)
    assert run.resolve_host(None) == "127.0.0.1"


def test_all_interfaces_only_when_asked_by_env(monkeypatch):
    monkeypatch.setenv(run.HOST_ENV, "0.0.0.0")
    assert run.resolve_host(None) == "0.0.0.0"


def test_cli_host_beats_env(monkeypatch):
    monkeypatch.setenv(run.HOST_ENV, "0.0.0.0")
    assert run.resolve_host("127.0.0.1") == "127.0.0.1"


@pytest.mark.parametrize("host, expected", [("0.0.0.0", "127.0.0.1"), ("127.0.0.1", "127.0.0.1"),
                                            ("192.168.1.5", "192.168.1.5")])
def test_link_never_points_to_all_interfaces(host, expected):
    """На 0.0.0.0 страницу не открыть: ссылка и браузер идут на 127.0.0.1."""
    assert run.browse_host(host) == expected


def test_pulp_is_required_so_system_python_gets_it():
    """Без pulp сервер поднимается, но кнопка «Оптимизировать» падает."""
    assert "pulp" in run.NEEDED


def test_docker_sets_all_interfaces_explicitly():
    root = Path(__file__).resolve().parents[1]
    assert "KRIOKONTUR_HOST=0.0.0.0" in (root / "Dockerfile").read_text(encoding="utf-8")
    assert "KRIOKONTUR_HOST=0.0.0.0" in (root / "docker-compose.yaml").read_text(encoding="utf-8")
