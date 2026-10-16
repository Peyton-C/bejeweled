import pytest


@pytest.fixture(autouse=True)
def no_cover_lookup(monkeypatch, request):
    """Keep the tests off the network, except the ones about the lookup itself."""
    if request.module.__name__.endswith("test_artwork"):
        return
    from bejeweled import artwork
    monkeypatch.setattr(artwork, "find", lambda *args, **kwargs: None)
