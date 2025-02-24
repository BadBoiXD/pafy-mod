import logging
import os
import sys
import time

uni = str if sys.version_info[:2] >= (3, 0) else unicode
try:
    import yt_dlp
except ModuleNotFoundError:
    try:
        import youtube_dl as yt_dlp
    except ModuleNotFoundError:
        pass

from . import g
from .backend_shared import (
    BasePafy,
    BaseStream,
    get_size_done,
    get_status_string,
    remux,
)

dbg = logging.debug


early_py_version = sys.version_info[:2] < (2, 7)


class YtdlPafy(BasePafy):
    def __init__(self, *args, **kwargs):
        self._ydl_info = None
        self._ydl_opts = g.def_ydl_opts
        if ydl_opts := kwargs.get("ydl_opts"):
            self._ydl_opts.update(ydl_opts)
        super(YtdlPafy, self).__init__(*args, **kwargs)

    def _fetch_basic(self):
        """Fetch basic data and streams."""
        if self._have_basic:
            return

        with yt_dlp.YoutubeDL(self._ydl_opts) as ydl:
            try:
                self._ydl_info = ydl.extract_info(self.videoid, download=False)
            # Turn into an IOError since that is what pafy previously raised
            except yt_dlp.utils.DownloadError as e:
                raise IOError(str(e).replace("YouTube said", "Youtube says"))

        if self.callback:
            self.callback("Fetched video info")
        try:
            self._title = self._ydl_info["title"]
        except:
            self._title = "NA"
        try:
            self._author = self._ydl_info["uploader"]
        except:
            self._author = "NA"
        try:
            self._rating = self._ydl_info["average_rating"]
        except:
            self._rating = "NA"
        self._length = self._ydl_info.get("duration", 0)
        self._viewcount = self._ydl_info["view_count"]
        self._likes = self._ydl_info.get("like_count", 0)
        self._username = self._ydl_info["uploader_id"]
        self._subtitles = self._ydl_info.get("subtitles", {})
        self._category = (
            self._ydl_info["categories"][0] if self._ydl_info["categories"] else ""
        )
        self._bestthumb = self._ydl_info["thumbnails"][0]["url"]
        self._bigthumb = g.urls["bigthumb"] % self.videoid
        self._bigthumbhd = g.urls["bigthumbhd"] % self.videoid
        self.expiry = time.time() + g.lifespan

        self._have_basic = True

    def _fetch_gdata(self):
        """Extract gdata values, fetch gdata if necessary."""
        if self._have_gdata:
            return

        item = self._get_video_gdata(self.videoid)["items"][0]
        snippet = item["snippet"]
        self._published = uni(snippet["publishedAt"])
        self._description = uni(snippet["description"])
        # Note: using snippet.get since some videos have no tags object
        self._keywords = [uni(i) for i in snippet.get("tags", ())]
        self._have_gdata = True

    def _process_streams(self):
        """Create Stream object lists from internal stream maps."""

        if not self._have_basic:
            self._fetch_basic()

        allstreams = [YtdlStream(z, self) for z in self._ydl_info["formats"]]
        self._streams = [i for i in allstreams if i.mediatype == "normal"]
        self._audiostreams = [i for i in allstreams if i.mediatype == "audio"]
        self._videostreams = [i for i in allstreams if i.mediatype == "video"]
        self._m4astreams = [i for i in allstreams if i.extension == "m4a"]
        self._oggstreams = [i for i in allstreams if i.extension == "ogg"]
        self._allstreams = allstreams


class YtdlStream(BaseStream):
    def __init__(self, info, parent):
        super(YtdlStream, self).__init__(parent)
        self._itag = info["format_id"]

        if info.get("acodec") != "none" and info.get("vcodec") == "none":
            self._mediatype = "audio"
        elif info.get("acodec") == "none" and info.get("vcodec") != "none":
            self._mediatype = "video"
        else:
            self._mediatype = "normal"

        self._threed = info.get("format_note") == "3D"
        self._rawbitrate = info.get("abr", 0) * 1024

        height = info.get("height") or 0
        width = info.get("width") or 0
        self._resolution = f"{str(width)}x{str(height)}"
        self._dimensions = width, height
        self._bitrate = str(info.get("abr", 0)) + "k"
        self._quality = (
            self._bitrate if self._mediatype == "audio" else self._resolution
        )

        self._extension = info["ext"]
        self._notes = info.get("format_note") or ""
        self._url = info.get("url")
        if self._url.startswith("https://manifest.googlevideo.com"):
            self._url = info.get("fragment_base_url", self._url)

        self._info = info

    def get_filesize(self):
        """Return filesize of the stream in bytes.  Set member variable."""

        # Faster method
        if "filesize" in self._info and self._info["filesize"] is not None:
            return self._info["filesize"]

        # Fallback
        return super(YtdlStream, self).get_filesize()

    def download(
        self,
        filepath="",
        quiet=False,
        progress="Bytes",
        callback=None,
        meta=False,
        remux_audio=False,
    ):
        downloader = yt_dlp.downloader.http.HttpFD(ydl(), {"http_chunk_size": 10485760})

        progress_available = ["KB", "MB", "GB"]
        if progress not in progress_available:
            progress = "Bytes"

        status_string = get_status_string(progress)

        def progress_hook(s):
            if s["status"] != "downloading":
                return
            bytesdone = s["downloaded_bytes"]
            total = s["total_bytes"]
            rate = s["speed"] / 1024 if s.get("speed") is not None else 0
            eta = 0 if s.get("eta") is None else s["eta"]
            progress_stats = (
                get_size_done(bytesdone, progress),
                bytesdone * 1.0 / total,
                rate,
                eta,
            )
            if not quiet:
                status = status_string.format(*progress_stats)
                sys.stdout.write("\r" + status + " " * 4 + "\r")
                sys.stdout.flush()

            if callback:
                callback(total, *progress_stats)

        downloader._progress_hooks = [progress_hook]

        if filepath and os.path.isdir(filepath):
            filename = self.generate_filename(max_length=256 - len(".temp"))
            filepath = os.path.join(filepath, filename)

        elif filepath:
            pass

        else:
            filepath = self.generate_filename(meta=meta, max_length=256 - len(".temp"))

        infodict = {"url": self.url}

        downloader.download(filepath, infodict)
        print("")

        if remux_audio and self.mediatype == "audio":
            os.rename(filepath, filepath + ".temp")
            remux(filepath + ".temp", filepath, quiet=quiet, muxer=remux_audio)

        return filepath


class ydl:
    def urlopen(self, url):
        return g.opener.open(url)

    def to_screen(self, *args, **kwargs):
        pass

    def to_console_title(self, *args, **kwargs):
        pass

    def trouble(self, *args, **kwargs):
        pass

    def report_warning(self, *args, **kwargs):
        pass

    def report_error(self, *args, **kwargs):
        pass
