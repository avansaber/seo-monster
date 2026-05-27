"""Shared test fixtures and helpers.

The mocking boundary is the client layer: tests inject fake clients (objects
with a ``probe()`` method, and in later phases the data methods) rather than
patching the network. ``make_dispatcher`` wraps ``server.dispatch`` with a fixed
set of injected clients so a test can drive any tool offline.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

import pytest

from seo_mcp.config import Config, load_config


@pytest.fixture
def fake_env() -> Callable[..., dict[str, str]]:
    """Factory for an env mapping. Pass keyword overrides; only non-None values
    are included, so a test can express "this var is unset" by omitting it."""

    def _make(**overrides: str | None) -> dict[str, str]:
        return {k: v for k, v in overrides.items() if v is not None}

    return _make


@pytest.fixture
def make_config(fake_env) -> Callable[..., Config]:
    """Build a Config from an env override set, ignoring any real config file by
    pointing at a path that does not exist."""

    def _make(config_path: str = "/nonexistent/seo-mcp.toml", **env_overrides: str | None) -> Config:
        return load_config(env=fake_env(**env_overrides), config_path=config_path)

    return _make


class FakeProbeClient:
    """Minimal fake client exposing the probe() contract used by system_status.

    ``ok=True`` -> probe returns True; ``ok=False`` -> probe raises (to exercise
    the failure path). ``calls`` records how many times probe ran so tests can
    assert zero-call behavior."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls = 0

    def probe(self) -> bool:
        self.calls += 1
        if not self.ok:
            raise RuntimeError("simulated upstream failure")
        return True


@pytest.fixture
def fake_client() -> type[FakeProbeClient]:
    """The FakeProbeClient class, so tests can build instances: fake_client(ok=False)."""
    return FakeProbeClient


@pytest.fixture
def make_dispatcher() -> Callable[..., Callable[..., dict[str, Any]]]:
    """Return a dispatcher bound to a fixed clients mapping.

    Usage:
        dispatch = make_dispatcher(clients={"gsc": FakeProbeClient()})
        result = dispatch("system_status", {"probe": True}, config)
    """
    from seo_mcp import server

    def _factory(clients: Mapping[str, Any] | None = None):
        clients = clients or {}

        def _dispatch(name: str, arguments: Mapping[str, Any], config: Config) -> dict[str, Any]:
            return server.dispatch(name, arguments, config, clients)

        return _dispatch

    return _factory
