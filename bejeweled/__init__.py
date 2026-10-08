"""bejeweled - split songs into stems, and author NI stem files from them."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bejeweled")
except PackageNotFoundError:  # imported from a source tree that was never installed
    __version__ = "unknown"