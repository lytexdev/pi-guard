"""Links in a question are read only from the public internet: the excerpt goes to the AI."""

from __future__ import annotations

import pytest

from pi_guard.addons import assistant


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8443/api/session",
        "http://192.168.178.1/",
        "http://10.8.0.1/admin/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "file:///etc/passwd",
    ],
)
async def test_links_into_the_home_network_are_not_fetched(url: str) -> None:
    with pytest.raises(ValueError):
        await assistant._fetch_public(url)


async def test_a_refused_link_leaves_no_excerpt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse(url: str) -> str:
        raise ValueError("not public")

    monkeypatch.setattr(assistant, "_fetch_public", refuse)
    bot = object.__new__(assistant.Assistant)
    assert await bot._link_excerpts("what is on http://192.168.178.1/ ?") == ""
