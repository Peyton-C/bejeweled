"""Cover art for a song that arrived without any, looked up by artist and title, or
by artist and album for every song of one.

A mix from YouTube or a folder of stems carries no art, and a stem file without it is
a grey square in every DJ library. The iTunes Search API is asked first, then
Deezer's; neither needs a key.
"""
from __future__ import annotations

import os
import re
import unicodedata

import requests

ITUNES = "https://itunes.apple.com/search"
DEEZER = "https://api.deezer.com/search"
DEEZER_ALBUM = "https://api.deezer.com/search/album"

# Large enough for any deck's artwork view, small enough not to bloat every stem file.
# iTunes renders whatever size the URL asks for, Deezer's cover_xl is this size too.
SIZE = 1000


def find(artist: str | None, title: str | None, out_dir: str, session=None) -> str | None:
    """Download the cover of the song, returning its path, or None when none matches.

    A result counts only when its artist and title both match, so a title alone, which
    many songs share, never brings back the wrong cover.
    """
    if not artist or not title:
        return None
    return _download((_itunes, _deezer), artist, title, out_dir, session)


def find_album(artist: str | None, album: str | None, out_dir: str,
               session=None) -> str | None:
    """Download the cover of the album, returning its path, or None when none matches.

    The album's name must match whole, so a deluxe edition or a remix album, whose
    cover often differs, is never taken for the album itself.
    """
    if not artist or not album:
        return None
    return _download((_itunes_album, _deezer_album), artist, album, out_dir, session)


def _download(lookups, artist, name, out_dir, session) -> str | None:
    get = (session or requests).get
    for lookup in lookups:
        try:
            url = lookup(get, artist, name)
            if not url:
                continue
            r = get(url, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"cover art lookup failed: {e}")
            continue
        path = os.path.join(out_dir, "cover.jpg")
        with open(path, "wb") as f:
            f.write(r.content)
        return path
    return None


def _itunes(get, artist, title) -> str | None:
    r = get(ITUNES, params={"term": f"{primary(artist)} {plain(title)}", "media": "music",
                            "entity": "song", "limit": 25}, timeout=30)
    r.raise_for_status()
    for hit in r.json().get("results", []):
        if _matches(hit.get("artistName"), hit.get("trackName"), artist, title):
            art = hit.get("artworkUrl100")
            # The size is in the file name, and any size can be asked for
            return art and re.sub(r"/\d+x\d+bb\.", f"/{SIZE}x{SIZE}bb.", art)
    return None


def _deezer(get, artist, title) -> str | None:
    # The artist: filter misses some artists outright, `artist:"100 gecs"` finds none of
    # their songs, so a plain search follows. _matches still guards both.
    for q in (f'artist:"{primary(artist)}" track:"{plain(title)}"',
              f"{primary(artist)} {plain(title)}"):
        r = get(DEEZER, params={"q": q}, timeout=30)
        r.raise_for_status()
        for hit in r.json().get("data", []):
            if _matches((hit.get("artist") or {}).get("name"), hit.get("title"),
                        artist, title):
                return (hit.get("album") or {}).get("cover_xl")
    return None


def _itunes_album(get, artist, album) -> str | None:
    r = get(ITUNES, params={"term": f"{primary(artist)} {album}", "media": "music",
                            "entity": "album", "limit": 25}, timeout=30)
    r.raise_for_status()
    for hit in r.json().get("results", []):
        # iTunes names a single or an EP as such, `hot to go! - Single`
        name = re.sub(r"\s+-\s+(?:Single|EP)$", "", hit.get("collectionName") or "")
        if _album_matches(hit.get("artistName"), name, artist, album):
            art = hit.get("artworkUrl100")
            return art and re.sub(r"/\d+x\d+bb\.", f"/{SIZE}x{SIZE}bb.", art)
    return None


def _deezer_album(get, artist, album) -> str | None:
    for q in (f'artist:"{primary(artist)}" album:"{album}"', f"{primary(artist)} {album}"):
        r = get(DEEZER_ALBUM, params={"q": q}, timeout=30)
        r.raise_for_status()
        for hit in r.json().get("data", []):
            if _album_matches((hit.get("artist") or {}).get("name"), hit.get("title"),
                              artist, album):
                return hit.get("cover_xl")
    return None


def _album_matches(their_artist, their_album, artist, album) -> bool:
    return bool(their_artist and their_album
                and _key(primary(artist)) in _key(their_artist)
                and _key(their_album) == _key(album))


def _matches(their_artist, their_title, artist, title) -> bool:
    return bool(their_artist and their_title
                and _key(primary(artist)) in _key(their_artist)
                and _key(plain(their_title)) == _key(plain(title)))


def primary(artist: str) -> str:
    """The first credited artist: Doja Cat, of `Doja Cat feat. Rico Nasty`."""
    return re.split(r"\s+(?:feat\.?|ft\.?|featuring|with|x|&|and)\s+|,\s*", artist,
                    flags=re.IGNORECASE)[0].strip()


def plain(title: str) -> str:
    """A title without anything in brackets, which holds markers, features and edits."""
    return re.sub(r"\s*[\[(][^\])]*[\])]", "", title).strip() or title


def _key(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", text.lower())
