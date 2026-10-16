"""Cover art looked up by artist and title, without touching the network."""
from bejeweled import artwork


class Reply:
    def __init__(self, payload=None, content=b""):
        self.payload, self.content = payload, content

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Session:
    """Answers the two search APIs from canned results and records what was fetched."""

    def __init__(self, itunes=(), deezer=()):
        self.itunes, self.deezer, self.fetched = list(itunes), list(deezer), []

    def get(self, url, params=None, timeout=None):
        if url == artwork.ITUNES:
            return Reply({"results": self.itunes})
        if url == artwork.DEEZER:
            return Reply({"data": self.deezer})
        self.fetched.append(url)
        return Reply(content=b"jpeg")


def test_itunes_art_is_fetched_large(tmp_path):
    session = Session(itunes=[
        {"artistName": "Someone Else", "trackName": "Tia Tamera",
         "artworkUrl100": "https://a/wrong/100x100bb.jpg"},
        {"artistName": "Doja Cat & Rico Nasty", "trackName": "Tia Tamera (feat. Rico Nasty)",
         "artworkUrl100": "https://a/right/100x100bb.jpg"},
    ])
    path = artwork.find("Doja Cat feat. Rico Nasty", "Tia Tamera (RF IN)", str(tmp_path),
                        session=session)
    assert open(path, "rb").read() == b"jpeg"
    assert session.fetched == ["https://a/right/1000x1000bb.jpg"]


def test_deezer_answers_when_itunes_has_nothing(tmp_path):
    session = Session(deezer=[{"artist": {"name": "Ariana Grande"}, "title": "motive",
                               "album": {"cover_xl": "https://d/cover.jpg"}}])
    assert artwork.find("Ariana Grande & Doja Cat", "motive", str(tmp_path), session=session)
    assert session.fetched == ["https://d/cover.jpg"]


def test_a_title_alone_never_matches(tmp_path):
    session = Session(itunes=[{"artistName": "Someone Else", "trackName": "Streets",
                               "artworkUrl100": "https://a/100x100bb.jpg"}])
    assert artwork.find("Doja Cat", "Streets", str(tmp_path), session=session) is None
    assert artwork.find(None, "Streets", str(tmp_path), session=session) is None
    assert session.fetched == []
