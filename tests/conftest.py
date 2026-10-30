"""Shared test setup (pytest loads this automatically before every test file).

- Tests always use the local in-memory graph, never the live TigerGraph.
- The graph is rebuilt for every test, so a test that swaps in fake contacts/vendors
  gets a graph built from those, not a graph cached by an earlier test.
- Attack search uses the offline hash embedder (no API calls).
"""
import pytest

from provenant.graph import reputation


@pytest.fixture(autouse=True)
def fresh_local_graph(monkeypatch):
    monkeypatch.delenv("TG_HOST", raising=False)
    monkeypatch.setenv("EMBEDDER", "hash")
    reputation._backend = None
    yield
    reputation._backend = None
