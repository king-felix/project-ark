"""Project ARK — the video shelf: what a file really is, and what may be promised.

A video is the one thing on this box that can be large, slow to prepare and
impossible to play.  So the shelf is built on three refusals to guess:

* **An extension is a hint, never a fact.**  ``.mp4`` decides only that a file
  should be *looked at*; ``ffprobe`` decides what it is.  A renamed archive is
  rejected with a sentence, not served as a broken film.
* **"Supported" is not one word.**  A container can be accepted while its codec
  cannot be played, and a codec can be playable while the file needs a cheap
  repack.  ``plan_for()`` is the only place that decides between *direct*,
  *remux*, *convert* and *unsupported*, so no page can promise more than the
  bytes can do.
* **One original, one place — until the copy can stand alone.**  The bytes stay
  in ``drop/files`` under the name the File Box already uses; a media row
  *refers* to that file row and does not copy it.  Once a playing copy is ready
  and verified the upload has done its job and is released
  (``release_original``), unless the film is played straight from it or the
  File Box still offers it.  Derivatives, posters and captions live under
  ``media/``.

Everything here is plain data and plain SQL.  The slow work — probing,
thumbnails, conversion — lives in ``ark_media_jobs`` and runs in one background
worker, never in a request.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import ark_db
import ark_recovery
import ark_space
import ark_storage

Refused = ark_storage.Refused

#: Extensions that send a file to the shelf.  A hint for routing only.
VIDEO_EXT = frozenset({
    ".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi", ".mpg", ".mpeg",
    ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".wmv", ".flv",
})
#: Sidecar captions that may travel with a film.
SUB_EXT = frozenset({".srt", ".vtt"})

#: A film may be larger than a shared file, and still not unlimited.
MAX_MEDIA = 8 * 1024 ** 3
CHUNK = 1024 * 1024
PROBE_TIMEOUT = 40.0

#: What the player may do with the bytes.
DIRECT = "direct"          # hand the original to the browser
REMUX = "remux"            # repack the same streams into MP4/WebM: cheap
CONVERT = "convert"        # re-encode what the browser cannot decode
UNSUPPORTED = "unsupported"

PLAN_WORDS = {
    DIRECT: "Plays as it is",
    REMUX: "Needs a quick repack",
    CONVERT: "Needs converting",
    UNSUPPORTED: "Cannot be played",
}

STATE_PENDING = "pending"
STATE_PUBLISHED = "published"
STATE_HIDDEN = "hidden"
STATES = (STATE_PUBLISHED, STATE_PENDING, STATE_HIDDEN)

PAGE = 24

#: Codecs a browser plays without help.  Kept deliberately short: a codec that is
#: "usually fine" is exactly the one that fails on somebody's phone.
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {"aac", "mp3", "opus", "vorbis"}
#: Containers a browser opens by itself.
DIRECT_CONTAINERS = {"mp4", "m4v", "webm"}


# ---------------------------------------------------------------------------
#  where the shelf lives (read at call time, like every other path on the box)
# ---------------------------------------------------------------------------
def media_root() -> Path:
    return Path(os.environ.get("ARK_ROOT", "/srv/ark")) / "media"


def play_dir() -> Path:
    """Derivatives: a remux or a conversion the player may serve."""
    return media_root() / "play"


def thumbs_dir() -> Path:
    return media_root() / "thumbs"


def subs_dir() -> Path:
    return media_root() / "subs"


def temp_dir() -> Path:
    """Half-written derivatives.  Nothing serves this folder."""
    return media_root() / "tmp"


def ensure_dirs() -> None:
    for folder in (media_root(), play_dir(), thumbs_dir(), subs_dir(), temp_dir()):
        folder.mkdir(parents=True, exist_ok=True)


def is_video_name(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in VIDEO_EXT


def is_subtitle_name(filename: str) -> bool:
    return Path(str(filename or "")).suffix.lower() in SUB_EXT


def ext_of(filename: str) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    return ext if ext in VIDEO_EXT else ".mp4"


# ---------------------------------------------------------------------------
#  the tools
# ---------------------------------------------------------------------------
def ffprobe_bin() -> Optional[str]:
    return os.environ.get("ARK_FFPROBE") or shutil.which("ffprobe")


def ffmpeg_bin() -> Optional[str]:
    return os.environ.get("ARK_FFMPEG") or shutil.which("ffmpeg")


def tools_present() -> bool:
    return bool(ffprobe_bin() and ffmpeg_bin())


def _float(value, default=None):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _stream(json_facts: dict, kind: str) -> dict:
    """The stream this box will actually use, and whose codec is judged.

    For **video** that is the default stream, because that is the one a person
    means.  For **audio** it is the *first* stream, because ``build_command``
    maps ``0:a:0`` — judging the default track while copying the first one is how
    a commentary track ends up in the playing copy, or the muxer refuses a DTS
    track the probe never mentioned.
    """
    streams = [s for s in (json_facts.get("streams") or [])
               if isinstance(s, dict) and s.get("codec_type") == kind]
    if kind != "audio":
        for stream in streams:
            if (stream.get("disposition") or {}).get("default"):
                return stream
    return streams[0] if streams else {}


def probe(path: Path | str, timeout: float | None = None) -> dict:
    """Ask ``ffprobe`` what these bytes are.  Never raises; always answers.

    ``ok`` is the only field a caller may trust without reading ``error``: a
    file that could not be read is reported as such, with the tool's own last
    line, instead of being guessed at from its name.
    """
    empty = {"ok": False, "error": "", "container": "", "vcodec": "", "acodec": "",
             "profile": "", "pix_fmt": "", "width": 0, "height": 0, "duration": None,
             "rotation": 0, "channels": 0, "has_audio": False, "has_video": False,
             "size": 0, "format_name": "", "faststart": False}
    tool = ffprobe_bin()
    if not tool:
        return {**empty, "error": "The box has no video reader installed yet."}
    target = Path(path)
    try:
        if not target.is_file():
            return {**empty, "error": "The file is not on the card any more."}
        if target.stat().st_size == 0:
            return {**empty, "error": "The file is empty."}
    except OSError:
        return {**empty, "error": "The file could not be read."}

    # The whole stream description is asked for on purpose: side data carries the
    # rotation a phone recorded, and stripping the JSON to a few fields is how that
    # fact gets lost and a portrait film is thumbnailed sideways.
    # `-protocol_whitelist file` is not decoration: a file whose bytes are an
    # HLS or DASH playlist would otherwise make ffprobe fetch remote segments.
    # The shelf reads local bytes and nothing else, ever.
    cmd = [tool, "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
           "-show_format", "-show_streams", str(target)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout or PROBE_TIMEOUT,
                              check=False)
    except subprocess.TimeoutExpired:
        return {**empty, "error": "Reading that video took too long — it may be damaged."}
    except OSError:
        return {**empty, "error": "The box could not run its video reader."}
    text = (done.stdout or b"").decode("utf-8", "replace")
    if done.returncode != 0 or not text.strip():
        last = ((done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
                or ["The video could not be read."])[-1]
        return {**empty, "error": _plain_probe_error(last)}
    try:
        facts = json.loads(text)
    except ValueError:
        return {**empty, "error": "The video's description could not be read."}
    if not isinstance(facts, dict):
        return {**empty, "error": "The video's description could not be read."}

    video = _stream(facts, "video")
    audio = _stream(facts, "audio")
    fmt = facts.get("format") or {}
    if not video:
        return {**empty, "container": _container(fmt), "has_audio": bool(audio),
                "acodec": str(audio.get("codec_name") or ""),
                "duration": _duration(fmt, video, audio),
                "size": _int(fmt.get("size"), 0), "format_name": str(fmt.get("format_name") or ""),
                "error": "There is no picture in that file — only sound."
                if audio else "That file is not a video."}

    rotation = _rotation(video)
    duration = _duration(fmt, video, audio)
    return {
        "ok": True, "error": "",
        "container": _container(fmt),
        "format_name": str(fmt.get("format_name") or ""),
        "vcodec": str(video.get("codec_name") or ""),
        "acodec": str(audio.get("codec_name") or ""),
        "profile": str(video.get("profile") or ""),
        "pix_fmt": str(video.get("pix_fmt") or ""),
        "width": _int(video.get("width"), 0),
        "height": _int(video.get("height"), 0),
        "duration": duration,
        "rotation": rotation,
        "channels": _int(audio.get("channels"), 0),
        "has_audio": bool(audio),
        "has_video": True,
        "size": _int(fmt.get("size"), 0) or _size(target),
        "faststart": mp4_faststart(target) if _container(fmt) in ("mp4", "m4v") else False,
    }


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _plain_probe_error(line: str) -> str:
    """ffprobe's last line, made fit for a person — never a stack of flags."""
    text = re.sub(r"\s+", " ", str(line or "")).strip()
    text = text.split(":", 1)[-1].strip() if text.lower().startswith("file:") else text
    if not text:
        return "That file could not be read as a video."
    return f"That file could not be read as a video ({text[:180]})."


def _container(fmt: dict) -> str:
    names = str(fmt.get("format_name") or "").lower()
    tokens = names.replace(",", " ").split()
    for candidate in tokens:
        # ``mov,mp4,m4a,3gp,3g2,mj2`` is one container family to ffprobe.  The
        # spelling a browser cares about comes from the file's own suffix, in
        # ``plan_for`` — here the family is simply "mp4".
        if candidate in ("mp4", "mov", "m4a", "3gp", "3g2", "mj2"):
            return "mp4"
        if candidate in ("matroska", "webm"):
            # ffprobe calls both spellings "matroska,webm"; the file's own suffix
            # is what tells them apart, and `plan_for` reads it.
            return "mkv"
        if candidate in ("mpegts", "mpeg", "avi", "flv", "asf", "ogg", "wav"):
            return {"mpegts": "ts", "mpeg": "mpg", "asf": "wmv", "ogg": "ogv"}.get(
                candidate, candidate)
    return names.split(",")[0] if names else ""


def _duration(fmt: dict, video: dict, audio: dict):
    for source in (fmt.get("duration"), video.get("duration"), audio.get("duration")):
        value = _float(source)
        if value is not None and value > 0:
            return round(value, 3)
    return None


def _rotation(video: dict) -> int:
    for side in (video.get("side_data_list") or []):
        if isinstance(side, dict) and side.get("rotation") is not None:
            return int(_float(side.get("rotation"), 0) or 0) % 360
    tags = video.get("tags") or {}
    if isinstance(tags, dict) and tags.get("rotate") is not None:
        return int(_float(tags.get("rotate"), 0) or 0) % 360
    return 0


def mp4_faststart(path: Path | str) -> bool:
    """True when ``moov`` comes before ``mdat`` — the difference between a film
    that starts and a film that appears to hang while the whole file downloads.

    Only the top-level atom headers are read (``seek``, never the payload), and
    anything unexpected answers ``False``: an unknown layout is treated as
    "repack it", which is cheap and always correct.
    """
    try:
        with open(path, "rb") as handle:
            offset = 0
            size = os.fstat(handle.fileno()).st_size
            while offset + 8 <= size:
                handle.seek(offset)
                header = handle.read(16)
                if len(header) < 8:
                    return False
                box_size = int.from_bytes(header[0:4], "big")
                box_type = header[4:8]
                if box_size == 1:
                    if len(header) < 16:
                        return False
                    box_size = int.from_bytes(header[8:16], "big")
                if box_size < 8:
                    return False
                if box_type == b"moov":
                    return True
                if box_type == b"mdat":
                    return False
                offset += box_size
    except (OSError, ValueError):
        return False
    return False


# ---------------------------------------------------------------------------
#  what a browser may be promised
# ---------------------------------------------------------------------------
#: Format families that are instructions to fetch other things, not films.
_NETWORK_FORMATS = ("hls", "dash", "applehttp", "rtmp", "rtsp", "sdp", "webm_dash_manifest")


def plan_for(facts: dict, path: Path | str | None = None) -> str:
    """``direct`` / ``remux`` / ``convert`` / ``unsupported`` — the only verdict.

    The rules are intentionally conservative.  A wrong "direct" promises a film
    that does not start; a wrong "convert" costs electricity and nothing else,
    and the job can still be cancelled.
    """
    if not facts.get("ok"):
        return UNSUPPORTED
    if not facts.get("has_video"):
        return UNSUPPORTED
    if any(name in str(facts.get("format_name") or "").lower() for name in _NETWORK_FORMATS):
        return UNSUPPORTED
    vcodec = str(facts.get("vcodec") or "").lower()
    acodec = str(facts.get("acodec") or "").lower()
    container = str(facts.get("container") or "").lower()
    pix = str(facts.get("pix_fmt") or "").lower()
    if not vcodec:
        return UNSUPPORTED
    audio_ok = (not facts.get("has_audio")) or acodec in BROWSER_AUDIO
    video_ok = vcodec in BROWSER_VIDEO and pix in ("", "yuv420p", "yuvj420p")
    # A .mov and a .mp4 are the same container family to ffprobe, but a browser is
    # not obliged to open the .mov spelling; repacking costs seconds and removes
    # the doubt.  The suffix is used only for this, never for the codec verdict.
    suffix = Path(str(path)).suffix.lower() if path else ""
    # ...and a .webm is the same *demuxer* as an .mkv, so the suffix is also the
    # only thing that says "this is the box a browser opens".  Judged by suffix
    # AND codec: a .webm holding H.264 is not a WebM any browser will play.
    if suffix == ".webm" and video_ok and acodec in ("opus", "vorbis", "aac", ""):
        return DIRECT
    if container in DIRECT_CONTAINERS and video_ok and audio_ok:
        if container in ("mp4", "m4v") and (not facts.get("faststart") or suffix == ".mov"):
            return REMUX
        return DIRECT
    # The streams may already be browser-safe and simply sit in the wrong box.
    # `video_ok` (H.264 **8-bit** 4:2:0) is required, not just `vcodec == h264`:
    # a Hi10P film repacked byte-for-byte is still a film no browser can decode,
    # and calling it ready is a broken promise.  A 10-bit source is converted.
    if vcodec == "h264" and video_ok and audio_ok and container in (
            "mov", "mkv", "ts", "m2ts", "mts", "3gp"):
        return REMUX
    return CONVERT


def plan_words(plan: str) -> str:
    return PLAN_WORDS.get(str(plan or ""), "Not looked at yet")


# ---------------------------------------------------------------------------
#  titles
# ---------------------------------------------------------------------------
_DOTS = re.compile(r"[._]+")
_SPACES = re.compile(r"\s+")


def title_from_filename(filename: str) -> str:
    """A readable title: the extension is hidden, dots become spaces, nothing else.

    Deliberately not cleverer than that — no years scraped out, no guesses at
    episode numbers.  The original filename stays visible in the details, so a
    person can always tell what the file was called.
    """
    name = Path(str(filename or "")).name
    stem = Path(name).stem if Path(name).suffix.lower() in VIDEO_EXT else name
    text = _SPACES.sub(" ", _DOTS.sub(" ", stem)).strip(" -")
    return (text[:160] or "Untitled")


def human_duration(seconds) -> str:
    if seconds is None:
        return "length not known"
    total = int(round(float(seconds)))
    if total <= 0:
        return "length not known"
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_size(value) -> str:
    """A size in the same units the rest of the portal uses (1024, not 1000).

    ``ark_health.human_size`` prints "16.0 GB" for the card's reserve; a Media
    page that printed "17.2 GB" for the same number would be describing the same
    bytes twice and disagreeing with itself.
    """
    if value is None:
        return "not measured"
    step = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{int(step)} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return f"{step:.1f} TB"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
#  adding a film
# ---------------------------------------------------------------------------
def add_row(conn: sqlite3.Connection, *, file_id: int, stored: str, filename: str,
            size: int, sha256: str, facts: dict, title: str = "",
            state: str = STATE_PENDING, added_by: str = "", note: str = "",
            category: str = "") -> int:
    """Record one film.  The caller owns the transaction and the job."""
    plan = plan_for(facts)
    cur = conn.execute(
        "INSERT INTO media (ts, file_id, title, filename, stored, size, sha256, state,"
        " category, container, vcodec, acodec, width, height, duration, rotation, plan,"
        " probe, added_by, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (now_iso(), int(file_id), title or title_from_filename(filename), filename,
         stored, int(size), sha256 or "", state if state in STATES else STATE_PENDING,
         category or "", str(facts.get("container") or ""), str(facts.get("vcodec") or ""),
         str(facts.get("acodec") or ""), _int(facts.get("width")), _int(facts.get("height")),
         facts.get("duration"), _int(facts.get("rotation")), plan,
         json.dumps(_storable(facts)), added_by or "", (note or "")[:200]))
    return int(cur.lastrowid)


def _storable(facts: dict) -> dict:
    keep = ("ok", "container", "format_name", "vcodec", "acodec", "profile", "pix_fmt",
            "width", "height", "duration", "rotation", "channels", "has_audio",
            "has_video", "size", "faststart", "error")
    return {key: facts.get(key) for key in keep}


def accept(read, filename: str, *, state: str, added_by: str = "", note: str = "",
           title: str = "", limit: int | None = None,
           category: str = "") -> dict:
    """Take a video from a stream: spool it, prove it, record it, queue the work.

    ``read`` is the upload's own ``read(n)`` callable, so nothing is held in
    memory.  A file that proves not to be a video is removed again and refused
    with the reason — the box never keeps bytes it cannot describe.
    """
    capped = int(limit or MAX_MEDIA)
    if capped <= 0:
        raise Refused("This upload is as large as one file may be. Send the rest separately.")
    display = ark_storage.display_name(filename)
    if not is_video_name(display):
        raise Refused("That file is not a kind of video this box knows.")
    stored = ark_storage.spool(
        read, display, limit=capped)
    try:
        facts = probe(stored.path)
        if not facts.get("ok"):
            raise Refused(facts.get("error") or "That file could not be read as a video.")
        if plan_for(facts) == UNSUPPORTED:
            raise Refused("That file has no picture in it, so there is nothing to play.")
        with ark_db.tx() as conn:
            cur = conn.execute(
                "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
                " VALUES (?,?,?,?,?,?,?,1)",
                (now_iso(), stored.display, stored.stored, stored.display, stored.size,
                 "Kept for the Media shelf", stored.sha256))
            file_id = int(cur.lastrowid)
            media_id = add_row(
                conn, file_id=file_id, stored=stored.stored, filename=stored.display,
                size=stored.size, sha256=stored.sha256, facts=facts, title=title,
                state=state, added_by=added_by, note=note, category=category)
            enqueue(conn, media_id, "prepare")
        return {"id": media_id, "stored": stored.stored, "facts": facts}
    except BaseException:
        ark_storage._unlink(stored.path)
        with ark_db.tx() as conn:
            conn.execute("DELETE FROM files WHERE stored = ? AND note = ?",
                         (stored.stored, "Kept for the Media shelf"))
        raise


def adopt(conn: sqlite3.Connection, file_row, *, state: str = STATE_PUBLISHED,
          added_by: str = "", title: str = "", facts: dict | None = None) -> int:
    """Put a file that is already in the File Box on the shelf, without copying it.

    The File Box row keeps its visibility: adopting a shared file changes the
    shelf, not who may download the file.  That is why the original filename and
    the stored name are read from the row rather than invented.
    """
    path = ark_storage.drop_dir() / str(file_row["stored"] or file_row["name"])
    facts = facts if facts is not None else probe(path)
    if not facts.get("ok"):
        raise Refused(facts.get("error") or "That file could not be read as a video.")
    media_id = add_row(
        conn, file_id=int(file_row["id"]), stored=str(file_row["stored"] or file_row["name"]),
        filename=str(file_row["display"] or file_row["name"]), size=int(file_row["size"] or 0),
        sha256=str(file_row["sha256"] or ""), facts=facts, title=title, state=state,
        added_by=added_by, note="Added from the File Box")
    enqueue(conn, media_id, "prepare")
    return media_id


def add_captions(conn: sqlite3.Connection, media_id: int, name: str, payload: bytes,
                 source: str = "") -> int:
    """Keep a sidecar caption file, converted to the format a browser reads.

    ``.vtt`` is stored as it arrived; ``.srt`` is converted here, in memory, with
    a bounded parse — no tool, no network, nothing that can hang.  An empty or
    unreadable file is refused rather than stored as a broken track.
    """
    ext = Path(str(name or "")).suffix.lower()
    if ext not in SUB_EXT:
        raise Refused("Captions must be a .vtt or .srt file.")
    if not payload:
        raise Refused("That caption file was empty.")
    if len(payload) > 2 * 1024 * 1024:
        raise Refused("That caption file is too large to be captions.")
    text = payload.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")
    if ext == ".srt":
        text = srt_to_vtt(text)
    else:
        text = vtt_clean(text)
    if not text.strip():
        raise Refused("That caption file had no captions in it.")
    lang = subtitle_language(name)
    folder = subs_dir() / str(int(media_id))
    folder.mkdir(parents=True, exist_ok=True)
    # A second English track is a second track, not a replacement: the name on
    # disk is made unique so one upload can never silently eat another.
    taken = {str(row["stored"]) for row in conn.execute(
        "SELECT stored FROM media_subs WHERE media_id = ?", (int(media_id),)).fetchall()}
    stem = lang
    bump = 2
    while f"{int(media_id)}/{stem}.vtt" in taken:
        stem = f"{lang}-{bump}"
        bump += 1
    target = folder / f"{stem}.vtt"
    plot = target.with_suffix(".vtt.part")
    plot.write_text(text, encoding="utf-8")
    os.chmod(plot, 0o644)
    os.replace(plot, target)
    # The path is kept **relative to the captions folder**: a record that names an
    # absolute path stops being true the day the box is restored somewhere else.
    cur = conn.execute(
        "INSERT INTO media_subs (media_id, lang, label, stored, size, source)"
        " VALUES (?,?,?,?,?,?)",
        (int(media_id), lang, language_label(lang), f"{int(media_id)}/{stem}.vtt",
         len(text.encode("utf-8")), source or str(name)))
    return int(cur.lastrowid)


_TIME = re.compile(r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _vtt_time(match) -> str:
    hours, minutes, seconds, millis = match.groups()
    return f"{int(hours):02d}:{minutes}:{seconds}.{millis.ljust(3, '0')[:3]}"


def srt_to_vtt(text: str) -> str:
    """SRT → WebVTT: a header, a decimal comma becomes a dot, nothing else.

    This is a text transformation on purpose.  ffmpeg would also do it, but a
    caption file is small, and a person's subtitles should never need a 40-second
    subprocess (or fail because one is missing) to become readable.
    """
    body = _TIME.sub(lambda m: _vtt_time(m), text.strip("\ufeff").strip())
    body = re.sub(r"^\d+\s*$\n?", "", body, flags=re.MULTILINE)
    return "WEBVTT\n\n" + body.strip() + "\n"


def vtt_clean(text: str) -> str:
    body = text.strip("\ufeff").strip()
    if not body.upper().startswith("WEBVTT"):
        body = "WEBVTT\n\n" + body
    return body.rstrip() + "\n"


#: Language codes this box will name.  A file called ``The.Matrix.720p.srt`` must
#: not become the language "the".
LANGUAGES = frozenset({
    "en", "es", "fr", "de", "it", "pt", "nl", "pl", "ru", "ar", "hi", "ja", "ko",
    "zh", "sv", "tr", "da", "fi", "no", "cs", "el", "he", "uk", "vi", "th", "id",
    "ro", "hu", "bg", "sr", "hr", "sk", "fa", "ms", "tl", "ca", "eu", "gl", "is",
})


def subtitle_language(name: str) -> str:
    """``film.en.srt`` → ``en``; anything that is not a known code is ``en``."""
    stem = Path(str(name or "")).stem
    for part in reversed(re.split(r"[._\- ]+", stem)):
        tag = part.lower()
        if tag in LANGUAGES:
            return tag
        if len(tag) == 3 and tag in LANGUAGES:
            return tag
    return "en"


def language_label(code: str) -> str:
    names = {"en": "English", "es": "Spanish", "fr": "French", "de": "German",
             "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish",
             "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "ja": "Japanese",
             "ko": "Korean", "zh": "Chinese", "sv": "Swedish", "tr": "Turkish"}
    return names.get((code or "").lower(), (code or "en").upper())


# ---------------------------------------------------------------------------
#  the work queue
# ---------------------------------------------------------------------------
def enqueue(conn: sqlite3.Connection, media_id: int, kind: str = "prepare") -> int:
    """Queue work, unless the same work is already waiting or running."""
    row = conn.execute(
        "SELECT id FROM media_jobs WHERE media_id = ? AND kind = ?"
        " AND state IN ('queued','running') ORDER BY id DESC LIMIT 1",
        (int(media_id), str(kind))).fetchone()
    if row:
        return int(row["id"] if isinstance(row, sqlite3.Row) else row[0])
    cur = conn.execute(
        "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (?,?, 'queued', ?)",
        (int(media_id), str(kind), now_iso()))
    return int(cur.lastrowid)


def claim(conn: sqlite3.Connection, pid: int = 0):
    """Take the oldest waiting job, or return ``None``.

    The caller holds ``BEGIN IMMEDIATE`` (``ark_db.tx``), so two workers cannot
    claim the same row: the loser's update matches nothing.
    """
    row = conn.execute(
        "SELECT * FROM media_jobs WHERE state = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    changed = conn.execute(
        "UPDATE media_jobs SET state = 'running', started = ?, heartbeat = ?, pid = ?,"
        " attempts = attempts + 1 WHERE id = ? AND state = 'queued'",
        (now_iso(), _now(), int(pid), int(row["id"]))).rowcount
    if not changed:
        return None
    return conn.execute("SELECT * FROM media_jobs WHERE id = ?", (int(row["id"]),)).fetchone()


def _now() -> float:
    import time
    return time.time()


def finish(conn: sqlite3.Connection, job_id: int, state: str, detail: str = "",
           progress: float = 0.0) -> None:
    conn.execute(
        "UPDATE media_jobs SET state = ?, finished = ?, detail = ?, progress = ?"
        " WHERE id = ?",
        (state, now_iso(), str(detail or "")[:500], float(progress or 0), int(job_id)))


def heartbeat(conn: sqlite3.Connection, job_id: int, progress: float | None = None) -> None:
    if progress is None:
        conn.execute("UPDATE media_jobs SET heartbeat = ? WHERE id = ?", (_now(), int(job_id)))
    else:
        conn.execute("UPDATE media_jobs SET heartbeat = ?, progress = ? WHERE id = ?",
                     (_now(), float(progress), int(job_id)))


def job_state(conn: sqlite3.Connection, job_id: int) -> str:
    row = conn.execute("SELECT state FROM media_jobs WHERE id = ?", (int(job_id),)).fetchone()
    return str(row["state"]) if row else ""


def cancel(conn: sqlite3.Connection, media_id: int) -> int:
    """Ask every waiting or running job for this film to stop.

    A running job learns about it from ``job_state()`` on its next progress tick
    and stops the tool itself; a queued one is simply marked, so the worker
    skips it.  Both are idempotent.
    """
    return int(conn.execute(
        "UPDATE media_jobs SET state = 'cancelled', finished = ?"
        " WHERE media_id = ? AND state IN ('queued','running')",
        (now_iso(), int(media_id))).rowcount or 0)


def recover(conn: sqlite3.Connection) -> dict:
    """Reconcile work a power cut or a crash interrupted.

    A job that was running when the box stopped is put back in the queue once,
    and failed after three honest attempts — the same three-strike rule the rest
    of the project follows.  Half-written derivatives are removed: the original
    is untouched, so nothing of value is lost.
    """
    requeued = int(conn.execute(
        "UPDATE media_jobs SET state = CASE WHEN attempts >= 3 THEN 'failed' ELSE 'queued' END,"
        " detail = CASE WHEN attempts >= 3 THEN 'Stopped three times before finishing.'"
        " ELSE 'The box was switched off while this was running.' END"
        " WHERE state = 'running'").rowcount or 0)
    removed = 0
    folder = temp_dir()
    if folder.is_dir():
        for path in folder.iterdir():
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return {"requeued": requeued, "temp_removed": removed}


def running_job(conn: sqlite3.Connection, media_id: int):
    return conn.execute(
        "SELECT * FROM media_jobs WHERE media_id = ? AND state IN ('queued','running')"
        " ORDER BY id DESC LIMIT 1", (int(media_id),)).fetchone()


def last_job(conn: sqlite3.Connection, media_id: int, kind: str | None = None):
    """The newest job for a film — of one kind, when a caller asks for one.

    ``status_of()`` speaks about *preparation*, so it must be told about the
    newest ``prepare`` job: a poster taken successfully after a failed
    conversion says nothing about whether the film can play, and a poster that
    failed is not the reason a film cannot be watched.
    """
    if kind:
        return conn.execute(
            "SELECT * FROM media_jobs WHERE media_id = ? AND kind = ?"
            " ORDER BY id DESC LIMIT 1", (int(media_id), str(kind))).fetchone()
    return conn.execute(
        "SELECT * FROM media_jobs WHERE media_id = ? ORDER BY id DESC LIMIT 1",
        (int(media_id),)).fetchone()


# ---------------------------------------------------------------------------
#  the catalogue
# ---------------------------------------------------------------------------
SORTS = {
    "recent": "m.ts DESC, m.id DESC",
    "oldest": "m.ts ASC, m.id ASC",
    "title": "m.title COLLATE NOCASE ASC, m.id ASC",
    "longest": "m.duration IS NULL, m.duration DESC, m.id DESC",
    "largest": "m.size DESC, m.id DESC",
}


def item(conn: sqlite3.Connection, media_id: int):
    return conn.execute("SELECT * FROM media WHERE id = ?", (int(media_id),)).fetchone()


def _where(q: str, state: str, ready: str, category: str) -> tuple[str, list]:
    clauses, args = [], []
    if state:
        clauses.append("m.state = ?")
        args.append(state)
    if q:
        clauses.append("(m.title LIKE ? ESCAPE '\\' OR m.filename LIKE ? ESCAPE '\\')")
        needle = "%" + str(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args.extend([needle, needle])
    if category:
        clauses.append("m.category = ?")
        args.append(category)
    if ready == "ready":
        clauses.append("(m.plan = 'direct' OR m.play_stored <> '')")
    elif ready == "working":
        clauses.append("EXISTS (SELECT 1 FROM media_jobs j WHERE j.media_id = m.id"
                       " AND j.state IN ('queued','running'))")
    elif ready == "needs":
        clauses.append("m.plan IN ('remux','convert') AND m.play_stored = ''")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, args


def count(conn: sqlite3.Connection, q: str = "", state: str = "", ready: str = "",
          category: str = "") -> int:
    where, args = _where(q, state, ready, category)
    row = conn.execute("SELECT COUNT(*) FROM media m" + where, args).fetchone()
    return int(row[0] or 0)


def listing(conn: sqlite3.Connection, q: str = "", state: str = STATE_PUBLISHED,
            ready: str = "", category: str = "", sort: str = "recent", page: int = 1,
            per_page: int = PAGE) -> dict:
    where, args = _where(q, state, ready, category)
    order = SORTS.get(sort or "", SORTS["recent"])
    page = max(1, int(page or 1))
    rows = conn.execute(
        "SELECT m.*, (SELECT j.state FROM media_jobs j WHERE j.media_id = m.id"
        " AND j.kind = 'prepare' ORDER BY j.id DESC LIMIT 1) AS job_state,"
        " (SELECT j.detail FROM media_jobs j WHERE j.media_id = m.id"
        " AND j.kind = 'prepare' ORDER BY j.id DESC LIMIT 1) AS job_detail,"
        " (SELECT j.progress FROM media_jobs j WHERE j.media_id = m.id"
        " AND j.kind = 'prepare' ORDER BY j.id DESC LIMIT 1) AS job_progress"
        " FROM media m" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
        [*args, int(per_page), (page - 1) * int(per_page)]).fetchall()
    total = count(conn, q, state, ready, category)
    pages = max(1, (total + per_page - 1) // per_page)
    return {"rows": rows, "total": total, "page": page, "pages": pages,
            "per_page": per_page, "has_next": page < pages, "has_prev": page > 1}


def categories(conn: sqlite3.Connection, states: Iterable[str] | None = None) -> list[str]:
    """Shelf names in use — by default only those a reader could actually see."""
    wanted = [str(s) for s in (states or (STATE_PUBLISHED,))]
    marks = ",".join("?" for _ in wanted)
    return [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT category FROM media WHERE category <> '' AND state IN ("
        + marks + ") ORDER BY category", wanted).fetchall()]


def status_of(row, present: bool | None = None, job_state: str | None = None) -> str:
    """One word a page can print: ready, working, needs, failed, missing, hidden.

    ``listing()`` joins the latest job onto each row, so the shelf and the
    owner's page get the verdict for free.  A page that fetched the row on its
    own must pass ``job_state`` in: without it a film whose preparation *failed*
    is indistinguishable from one that was never prepared, and its own page said
    "Getting ready" for ever (film 4, 3 Nov 2026 — log §46).
    """
    state = str(row["state"] or "")
    if job_state is not None:
        job = str(job_state or "")
    else:
        job = str(row["job_state"] or "") if "job_state" in row.keys() else ""
    if present is False:
        return "missing"
    if job in ("queued", "running"):
        return "working"
    # A film with a playing copy is ready, whatever the last attempt said: an
    # owner who tries "Prepare again" on a working film, and watches that second
    # attempt fail, must not be told the film they can plainly play is broken.
    if str(row["plan"]) == DIRECT or str(row["play_stored"] or ""):
        return "ready"
    if job == "failed":
        return "failed"
    if str(row["plan"]) in (REMUX, CONVERT):
        return "needs"
    return "unknown"


STATUS_WORDS = {
    "ready": "Ready to play",
    "working": "Getting ready",
    "needs": "Needs preparing",
    "failed": "Could not be prepared",
    "missing": "The file is not here",
    "unknown": "Not looked at yet",
    "hidden": "Hidden",
}


def status_words(status: str) -> str:
    return STATUS_WORDS.get(str(status or ""), "Not looked at yet")


# ---------------------------------------------------------------------------
#  where the bytes are
# ---------------------------------------------------------------------------
def original_path(row) -> Path:
    return ark_storage.drop_dir() / str(row["stored"] or "")


def play_path(row) -> Path | None:
    stored = str(row["play_stored"] or "")
    if not stored:
        return None
    candidate = play_dir() / stored
    return candidate if candidate.is_file() else None


def poster_path(row) -> Path | None:
    name = str(row["poster"] or "")
    if not name:
        return None
    candidate = thumbs_dir() / name
    return candidate if candidate.is_file() else None


def original_present(row) -> bool:
    return original_path(row).is_file()


def has_bytes(row) -> bool:
    """Are the bytes a viewer needs on the card right now?

    A film with a playing copy needs nothing else: the copy *is* the film, and
    the upload it was made from may have been released to save room.  Before
    that, the original is all there is.
    """
    if str(row["play_stored"] or ""):
        return play_path(row) is not None
    return original_present(row)


def footprint(row) -> int:
    """What this film actually occupies on the card now (copy plus kept upload)."""
    total = int(row["size"] or 0) if original_present(row) else 0
    if str(row["play_stored"] or ""):
        copy = play_path(row)
        total += copy.stat().st_size if copy is not None else int(row["play_size"] or 0)
    return total


def playable(row) -> Path | None:
    """The file the player should serve, or ``None`` when nothing is ready.

    A recorded playing copy is served on its own: it was verified before it was
    published, and once the original has been released it is the only copy.
    """
    if str(row["play_stored"] or ""):
        return play_path(row)
    if str(row["plan"]) == DIRECT:
        path = original_path(row)
        return path if path.is_file() else None
    return None


def caption_path(sub) -> Optional[Path]:
    name = str(sub["stored"] or "")
    if not name:
        return None
    path = subs_dir() / name
    return path if path.is_file() else None


def captions(conn: sqlite3.Connection, media_id: int) -> list:
    return conn.execute(
        "SELECT * FROM media_subs WHERE media_id = ? ORDER BY id", (int(media_id),)).fetchall()


# ---------------------------------------------------------------------------
#  deleting
# ---------------------------------------------------------------------------
def _discard(path: Path, reason: str) -> Optional[int]:
    """Remove one film file under the recovery lock; answer the bytes freed.

    A film's bytes are recorded by a recovery copy but never pinned: they are
    bulk content, and holding a 20 GiB playing copy for two days after a person
    deletes the film would be a worse promise than the one this module makes.
    The lock still matters: a backup copying this file cannot have it vanish
    half-way through the copy, and a capture cannot name a file that is being
    removed.  ``None`` means the file is still there because another copy owns
    the moment; ``0`` means it was already gone; a positive number is what was
    actually freed.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    try:
        if not ark_recovery.discard(ark_db.DB_PATH, path, reason=reason):
            return None
    except Exception:                        # noqa: BLE001 — leave the bytes, never split a copy
        return None
    return int(size or 0)


def delete(conn: sqlite3.Connection, media_id: int) -> dict:
    """Remove one film: its work, its derivatives, and the bytes nobody else uses.

    Idempotent by construction.  Files are removed **before** rows: a crash in
    between leaves a record pointing at nothing (which every page already shows
    as *the file is not here*, and which a second delete can finish), rather than
    a file on the card that no record names — bytes nobody can ever reclaim.

    A shared payload is never removed: if another media row or another File Box
    row still names the same stored file, only this film's references go.
    """
    row = item(conn, media_id)
    if row is None:
        return {"removed": 0, "freed": 0}
    stored = str(row["stored"] or "")
    freed = 0
    busy = False
    for path in (poster_path(row), play_path(row)):
        if path is None:
            continue
        amount = _discard(path, "a film was deleted")
        if amount is None:
            busy = True
        else:
            freed += amount
    for caption in captions(conn, media_id):
        path = subs_dir() / str(caption["stored"])
        amount = _discard(path, "a film was deleted")
        if amount is None:
            busy = True
        else:
            freed += amount
    if busy:
        # A copy or a backup owns a file this second.  The film stays on the
        # shelf and the owner can try again; removing the rows now would leave
        # bytes on the card that no page names and no pass can reclaim.
        return {"removed": 0, "freed": freed, "reason": "busy"}
    conn.execute("DELETE FROM media_subs WHERE media_id = ?", (int(media_id),))
    conn.execute("DELETE FROM media_jobs WHERE media_id = ?", (int(media_id),))
    conn.execute("DELETE FROM media WHERE id = ?", (int(media_id),))

    if stored:
        others = conn.execute("SELECT COUNT(*) FROM media WHERE stored = ?",
                              (stored,)).fetchone()[0]
        file_row = conn.execute("SELECT * FROM files WHERE stored = ?",
                                (stored,)).fetchone()
        carriers = conn.execute("SELECT COUNT(*) FROM files WHERE stored = ?",
                                (stored,)).fetchone()[0]
        if not others and file_row is not None and int(carriers or 0) <= 1:
            path = ark_storage.drop_dir() / stored
            amount = _discard(path, "a film was deleted")
            if amount is None:
                return {"removed": 0, "freed": freed, "reason": "busy"}
            freed += amount
            conn.execute("DELETE FROM files WHERE id = ?", (int(file_row["id"]),))
    return {"removed": 1, "freed": freed}


def release_original(conn: sqlite3.Connection, media_id: int) -> dict:
    """Give up a film's upload once its playing copy can stand on its own.

    Deleting a film and releasing its original are different decisions: this one
    keeps the film.  It refuses in the four cases where the upload is still
    doing work:

    * a **direct** film is served from its original — there is no copy;
    * a film with **no playing copy** has nothing to fall back to;
    * a file the **File Box still offers** (``hidden = 0``) is a share the owner
      made, not a spare copy;
    * another media row names the same stored file.

    Bytes go before rows: an interrupted release leaves a missing original that
    the next attempt can finish, never bytes on the card that no record names.
    """
    row = item(conn, media_id)
    if row is None:
        return {"removed": 0, "freed": 0, "reason": "gone"}
    stored = str(row["stored"] or "")
    if str(row["plan"] or "") not in (REMUX, CONVERT) or not stored:
        return {"removed": 0, "freed": 0, "reason": "kept"}
    copy = play_path(row)
    try:
        if copy is None or copy.stat().st_size <= 0:
            return {"removed": 0, "freed": 0, "reason": "no copy"}
    except OSError:
        return {"removed": 0, "freed": 0, "reason": "no copy"}
    file_row = conn.execute("SELECT * FROM files WHERE stored = ?", (stored,)).fetchone()
    if file_row is not None and not int(file_row["hidden"] or 0):
        return {"removed": 0, "freed": 0, "reason": "shared"}
    others = conn.execute("SELECT COUNT(*) FROM media WHERE stored = ? AND id <> ?",
                          (stored, int(media_id))).fetchone()[0]
    if others:
        return {"removed": 0, "freed": 0, "reason": "shared"}
    path = ark_storage.drop_dir() / stored
    try:
        freed = path.stat().st_size
    except FileNotFoundError:
        freed = 0
    except OSError:
        return {"removed": 0, "freed": 0, "reason": "busy"}
    if not ark_recovery.discard(ark_db.DB_PATH, path,
                                reason="a film's upload was released"):
        return {"removed": 0, "freed": 0, "reason": "busy"}
    if file_row is not None:
        conn.execute("DELETE FROM files WHERE id = ?", (int(file_row["id"]),))
    conn.execute("UPDATE media SET stored = '' WHERE id = ?", (int(media_id),))
    return {"removed": 1, "freed": freed, "reason": "released"}


def release_ready_originals(conn: sqlite3.Connection) -> dict:
    """Sweep every film that has a playing copy and still holds its upload.

    Run after a film is prepared and once when the worker starts, so a crash
    between publishing the copy and releasing the upload cannot leave the larger
    file on the card for ever.
    """
    rows = conn.execute(
        "SELECT id FROM media WHERE plan IN (?, ?) AND play_stored <> ''"
        " AND stored <> '' ORDER BY id", (REMUX, CONVERT)).fetchall()
    removed = freed = shared = failed = 0
    for row in rows:
        try:
            result = release_original(conn, int(row[0]))
        except Exception:                          # noqa: BLE001 — one film never stops the sweep
            failed += 1
            continue
        removed += int(result.get("removed") or 0)
        freed += int(result.get("freed") or 0)
        if result.get("reason") == "shared":
            shared += 1
    return {"considered": len(rows), "removed": removed, "freed": freed,
            "kept_shared": shared, "failed": failed}


def delete_many(conn: sqlite3.Connection, ids: Iterable[int]) -> dict:
    removed = 0
    freed = 0
    busy = 0
    for media_id in list(ids):
        result = delete(conn, int(media_id))
        removed += int(result["removed"])
        freed += int(result["freed"])
        if result.get("reason") == "busy":
            busy += 1
    return {"removed": removed, "freed": freed, "busy": busy}


def largest(conn: sqlite3.Connection, limit: int = 12) -> list:
    return conn.execute(
        "SELECT * FROM media ORDER BY size DESC LIMIT ?", (int(limit),)).fetchall()


def shelf_totals(conn: sqlite3.Connection) -> dict:
    """How much the shelf holds, counted from rows — never guessed from a folder."""
    row = conn.execute(
        "SELECT COUNT(*),"
        " COALESCE(SUM(CASE WHEN stored <> '' THEN size ELSE 0 END), 0),"
        " COALESCE(SUM(play_size), 0) FROM media").fetchone()
    return {"items": int(row[0] or 0), "originals": int(row[1] or 0),
            "derivatives": int(row[2] or 0)}
