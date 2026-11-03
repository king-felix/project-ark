"""Project ARK — the work behind the shelf: one film at a time, and never in a request.

The page must answer in milliseconds even while a two-hour film is being
converted, so nothing here runs inside the web process.  A single background
worker (`pi/bin/ark-media-worker`) claims one job from the database at a time,
does it, and writes down what happened.  Three consequences are deliberate:

* **One at a time.**  A Pi 4 has four small cores and a portal to keep
  answering.  A queue that runs two conversions at once makes the box useless to
  everyone else; the second film waits, and the page says it is waiting.
* **Every tool call is bounded.**  ``ffmpeg`` and ``ffprobe`` get argument
  arrays (never a shell), a wall-clock deadline, and a size budget.  A broken
  file cannot pin a core for a day.
* **Every output is atomic.**  A derivative is written under ``media/tmp`` and
  renamed into place only when it is whole, so a power cut leaves the previous
  good file — or no file — never half a film that the player would offer.

A thumbnail is the one thing allowed to fail without failing the film: a video
nobody can look at is still a video you can watch.  Conversion is not: if it
fails, the item says so and the original is untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import subprocess
import tempfile
import time
from pathlib import Path

import ark_db
import ark_media
import ark_space

#: Bounded, and generous enough for a long film on a slow card.
THUMB_TIMEOUT = 30.0
REMUX_FACTOR = 0.6            # seconds of repacking per second of film
#: A wall-clock ceiling, not a prediction.
#:
#: The first figure here (2.3x *faster* than real time, so 3x the film's length)
#: was measured at 720p with the whole CPU available.  The worker does not have
#: the whole CPU — `ark-media-worker.service` caps it at `CPUQuota=150%` — and a
#: 1080p source is encoded at 1080p, not at 720p.  Re-measured on this box
#: (3 Nov 2026, FFmpeg 7.1.5, the same 1480x1080 10-bit HEVC cartoon): 24 s of
#: film took **39 s** unconstrained, **127 s** under the worker's own quota —
#: 5.3x the film's length, of which decoding alone is 1.5x.  The old 3x ceiling
#: was therefore *below* what the box needs, and film 4 was killed at 23 minutes
#: having needed 37 (log §46).  8x keeps about 1.5x headroom over that
#: measurement for content harder than a cartoon.
CONVERT_FACTOR = 8.0
MIN_TIMEOUT = 120.0
#: 12 h, so a two-hour film from such a source (~10.6 h at the measured rate)
#: fits.  A film that needs longer than this is past what one job on a Pi 4 can
#: honestly do, and fails with a sentence instead of pinning the card for a day.
MAX_TIMEOUT = 12 * 3600.0
#: Never let one conversion write more than this, whatever the input claims.
CONVERT_CEILING = 8 * 1024 ** 3

#: The short side of a playing copy.  Anything larger is downscaled: a phone
#: cannot show the difference, the card holds several times more films, and the
#: encode is where the waiting is.  Measured on this box, 24 s of the same
#: 1480x1080 10-bit HEVC cartoon: 127 s at 1080p, 95 s at 720p.
PLAY_HEIGHT = 720

#: The Pi's hardware decoder.  Measured on this box on the same 24 s: software
#: decode + 720p encode took 91 s, hardware decode + the same encode took 45 s —
#: and the two MP4s were **byte-identical** (md5 4782a8145dcf55a602ab95f76b7e736b),
#: so this is not a quality trade at all.  A device node is not a working
#: decoder, so a refusal falls back to the CPU (`make_derivative`).
HWACCEL = "drm"
HWACCEL_DEVICE = "/dev/dri/renderD128"


def hwaccel_args(tool: str | None = None) -> list[str]:
    """``-hwaccel drm`` when this box has the device, else nothing.

    ``ARK_MEDIA_HWACCEL=0`` turns it off for good — the escape hatch when a
    driver misbehaves; ``=1`` claims it without asking, which is only useful to
    a test.  Being present is not the same as working, which is why
    ``make_derivative`` tries again on the CPU when the tool refuses before it
    has written a byte: a box with no driver, or a source this decoder will not
    take, still converts films.
    """
    choice = os.environ.get("ARK_MEDIA_HWACCEL", "").strip().lower()
    if choice in ("0", "no", "off", "false"):
        return []
    if choice in ("1", "yes", "on", "true"):
        return ["-hwaccel", HWACCEL]
    return ["-hwaccel", HWACCEL] if Path(HWACCEL_DEVICE).exists() else []

#: Candidate positions as a fraction of the film: interior, never the fade in.
FRACTIONS = (0.12, 0.22, 0.34, 0.46, 0.58, 0.70, 0.82)


def log(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


# ---------------------------------------------------------------------------
#  running one tool, with a deadline and a way to stop it
# ---------------------------------------------------------------------------
class Stopped(Exception):
    """The job was cancelled or the box was asked to stop."""


def _cancelled(job_id: int) -> bool:
    """Stop if the owner pressed Stop — or if the job/film row is gone.

    Deleting a film removes its job rows; a worker that carried on encoding would
    write a derivative nothing names.  A missing row is therefore a stop, not an
    error.
    """
    try:
        with ark_db.read() as conn:
            state = ark_media.job_state(conn, job_id)
        return state in ("cancelled", "")
    except Exception:
        return False


def _run(cmd: list[str], *, job_id: int = 0, deadline: float, on_line=None) -> None:
    """Run a tool, streaming its ``-progress`` lines, and stop it when told.

    The process is killed rather than merely abandoned: a conversion left running
    after its job was cancelled would keep the card busy and the reservation
    honest for hours.
    """
    started = time.monotonic()
    # stderr goes to a real temporary file, never a pipe: a pipe nobody drains
    # while the loop is reading stdout fills up and the tool blocks forever — the
    # classic way a bounded job becomes an unbounded one.
    with tempfile.TemporaryFile(mode="w+b") as errfile:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errfile,
                                    stdin=subprocess.DEVNULL, bufsize=1, text=True,
                                    errors="replace")
        except OSError as exc:
            raise RuntimeError(f"The box could not start its video tool ({exc}).") from exc
        last = ""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line:
                    last = line
                    if on_line is not None:
                        on_line(line)
                if job_id and _cancelled(job_id):
                    proc.kill()
                    raise Stopped("cancelled")
                if time.monotonic() - started > deadline:
                    proc.kill()
                    raise RuntimeError("That took longer than this box will spend on it.")
            proc.wait(timeout=10)
            if proc.returncode != 0:
                errfile.seek(0)
                err = errfile.read().decode("utf-8", "replace") or last
                raise RuntimeError(_tail(err))
        finally:
            if proc.poll() is None:
                proc.kill()
            try:
                proc.stdout.close()
            except Exception:
                pass


def _tail(text: str) -> str:
    """The last meaningful line of a tool's complaint, fit for a page."""
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    if not lines:
        return "The video tool stopped without saying why."
    return lines[-1][:300]


def _progress_value(line: str, key: str) -> int | None:
    if not line.startswith(key + "="):
        return None
    try:
        return int(float(line.split("=", 1)[1]))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
#  how long, and how much
# ---------------------------------------------------------------------------
def _deadline(facts: dict, factor: float) -> float:
    duration = facts.get("duration") or 0
    return min(MAX_TIMEOUT, max(MIN_TIMEOUT, float(duration) * factor + 120.0))


def output_budget(facts: dict, size: int, kind: str) -> int:
    """How much room a derivative is allowed to take, before it starts.

    A repack is the same bytes in another box, so the input's size plus a small
    margin is an honest ceiling.  An encode has no such guarantee, so it is
    bounded by the *film*, not by the input: at most 8 GiB, and at most a
    generous bitrate for its length.  The reservation is taken from this number,
    which is why it must be a ceiling and not an average.
    """
    if kind == ark_media.REMUX:
        return int(size * 1.1) + 8 * 1024 ** 2
    duration = float(facts.get("duration") or 0)
    if duration > 0:
        return min(CONVERT_CEILING, int(duration * 1024 ** 2) + 64 * 1024 ** 2)
    return min(CONVERT_CEILING, max(64 * 1024 ** 2, int(size * 1.6)))


# ---------------------------------------------------------------------------
#  thumbnails
# ---------------------------------------------------------------------------
def _candidates(facts: dict, seed: str) -> list[float]:
    """Interior positions, in an order that is stable for this film but not obvious.

    A single fixed timestamp puts every poster on the same frame of the same
    scene; a random one makes the shelf shuffle on every refresh.  Seeding from
    the file's own fingerprint gives "somewhere in the middle, always the same
    place" — and the chosen time is written down, so the poster can be explained.
    """
    duration = facts.get("duration")
    order = list(FRACTIONS)
    random.Random(hashlib.sha256(str(seed or "").encode()).hexdigest()).shuffle(order)
    if duration is None:
        return [2.0, 5.0, 10.0]
    span = float(duration)
    if span <= 2.0:
        return [max(0.05, span / 2.0)]
    low, high = min(1.0, span * 0.05), max(span * 0.05, span - 1.0)
    return [round(min(high, max(low, span * f)), 2) for f in order[:3]]


def _score_frame(payload: bytes) -> float:
    """A crude, dependency-free "is this a useful picture?" score.

    The frame is decoded to a tiny greyscale bitmap; the standard deviation says
    how much is going on, and a near-black or near-white mean is penalised
    because a fade is not a poster.  No image library is involved — the Pi has
    none, and adding one for this would be a dependency the box does not need.
    """
    if not payload:
        return -1.0
    step = max(1, len(payload) // 2048)
    sample = payload[::step]
    n = len(sample)
    mean = sum(sample) / n
    variance = sum((b - mean) ** 2 for b in sample) / n
    stddev = variance ** 0.5
    penalty = 0.0
    if mean < 10 or mean > 246:
        penalty = 24.0
    return stddev - penalty


def _grab_frame(tool: str, source: Path, at: float, width: int = 96,
                timeout: float = THUMB_TIMEOUT) -> bytes:
    cmd = [tool, "-nostdin", "-v", "error", "-protocol_whitelist", "file",
           "-ss", f"{max(0.0, at):.3f}", "-i", str(source),
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-pix_fmt", "gray",
           "-f", "rawvideo", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError):
        return b""
    if done.returncode != 0:
        return b""
    return done.stdout or b""


def make_thumbnail(row, *, at: float | None = None, tool: str | None = None) -> dict:
    """Pick and write one poster.  Returns what was chosen; never raises to fail the film."""
    tool = tool or ark_media.ffmpeg_bin()
    if not tool:
        return {"ok": False, "error": "The box has no video tool installed yet."}
    source = ark_media.original_path(row)
    if not source.is_file():
        return {"ok": False, "error": "The file is not on the card any more."}
    facts = _facts(row)
    seed = str(row["sha256"] or row["stored"] or row["id"])
    plan = [float(at)] if at is not None else _candidates(facts, seed)
    best = None
    for position in plan:
        score = _score_frame(_grab_frame(tool, source, position))
        if score >= 0 and (best is None or score > best[1]):
            best = (position, score)
        if best is not None and best[1] >= 18:
            break
    chosen = best[0] if best else (plan[0] if plan else 1.0)

    ark_media.thumbs_dir().mkdir(parents=True, exist_ok=True)
    name = f"m{int(row['id'])}.jpg"
    target = ark_media.thumbs_dir() / name
    partial = ark_media.temp_dir() / (name + ".part")
    ark_media.temp_dir().mkdir(parents=True, exist_ok=True)
    cmd = [tool, "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file",
           "-ss", f"{max(0.0, chosen):.3f}", "-i", str(source), "-frames:v", "1",
           "-vf", "scale=-2:360", "-q:v", "4", "-f", "mjpeg", str(partial)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=THUMB_TIMEOUT, check=False,
                              stdin=subprocess.DEVNULL)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _unlink(partial)
        return {"ok": False, "error": f"The picture could not be taken ({exc})."}
    if done.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
        _unlink(partial)
        return {"ok": False, "error": _tail((done.stderr or b"").decode("utf-8", "replace"))}
    os.chmod(partial, 0o644)
    os.replace(partial, target)
    # The row is written by the caller, after the tool has finished: a subprocess
    # must never run while a write transaction is open on the box's one database.
    return {"ok": True, "at": chosen, "file": name, "bytes": target.stat().st_size}


def _facts(row) -> dict:
    try:
        facts = json.loads(str(row["probe"] or "{}"))
    except ValueError:
        facts = {}
    if not isinstance(facts, dict):
        facts = {}
    facts.setdefault("duration", row["duration"])
    return facts


# ---------------------------------------------------------------------------
#  derivatives
# ---------------------------------------------------------------------------
def _needs_encode(facts: dict) -> bool:
    vcodec = str(facts.get("vcodec") or "").lower()
    pix = str(facts.get("pix_fmt") or "").lower()
    return not (vcodec == "h264" and pix in ("", "yuv420p", "yuvj420p"))


def _audio_args(facts: dict) -> list[str]:
    if not facts.get("has_audio"):
        return ["-an"]
    acodec = str(facts.get("acodec") or "").lower()
    if acodec in ("aac", "mp3"):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]


def build_command(tool: str, source: Path, output: Path, facts: dict, plan: str,
                  budget: int, hwaccel: list[str] | None = None) -> list[str]:
    """The exact argument array for a repack or an encode.

    Separated from running it so a test can read the plan without spending the
    electricity — and so the workstation recipe printed to the owner is built
    from the same function the box uses.  ``hwaccel`` is inserted before ``-i``,
    where ffmpeg wants it, and is empty on a box without the decoder.
    """
    # `-progress pipe:1` is what makes the rest of this module work: without it
    # ffmpeg writes nothing to stdout, `_run` blocks until the process exits, and
    # cancellation, the deadline, the heartbeat and the reservation's `written`
    # count are all dead code.  This line is not decoration.
    cmd = [tool, "-nostdin", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-y", "-protocol_whitelist", "file", *(hwaccel or []),
           "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
           "-max_muxing_queue_size", "1024"]
    if plan == ark_media.REMUX or not _needs_encode(facts):
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p"]
        width = int(facts.get("width") or 0)
        height = int(facts.get("height") or 0)
        if width and height and min(width, height) > PLAY_HEIGHT:
            # The *short* side decides: a 1480x1080 film becomes 986x720, a
            # 1080x1920 phone clip becomes 720x1280, and nothing is upscaled.
            cmd += ["-vf", f"scale=-2:{PLAY_HEIGHT}" if height <= width
                    else f"scale={PLAY_HEIGHT}:-2"]
    cmd += _audio_args(facts)
    cmd += ["-movflags", "+faststart", "-f", "mp4", "-fs", str(int(budget)), str(output)]
    return cmd


def make_derivative(row, *, job_id: int, plan: str | None = None) -> dict:
    """Repack or convert one film.  The original is never touched."""
    facts = _facts(row)
    plan = plan or str(row["plan"] or "")
    if plan not in (ark_media.REMUX, ark_media.CONVERT):
        return {"ok": True, "skipped": True, "reason": "nothing to do"}
    tool = ark_media.ffmpeg_bin()
    if not tool:
        return {"ok": False, "error": "The box has no video tool installed yet."}
    source = ark_media.original_path(row)
    if not source.is_file():
        return {"ok": False, "error": "The file is not on the card any more."}
    size = int(row["size"] or 0)
    budget = output_budget(facts, size, plan)
    name = f"m{int(row['id'])}.mp4"
    target = ark_media.play_dir() / name
    partial = ark_media.temp_dir() / (name + ".part")
    ark_media.ensure_dirs()
    _unlink(partial)
    deadline = _deadline(facts, REMUX_FACTOR if plan == ark_media.REMUX else CONVERT_FACTOR)
    owner = f"media:{int(row['id'])}:{int(job_id)}"
    wrote = {"size": 0}
    trouble = {"said": False}

    def note(write, what: str) -> None:
        """Progress and the space ledger are telemetry; a film is not.

        A transient ``database is locked`` / ``readonly database`` — the daily
        self-heal, an owner's console, a `sqlite3` window that checkpoints the
        WAL on close — must not throw away a sixteen-minute encode. On the live
        box it did exactly that at 18:11 on 3 Nov 2026 (log §47.6). The final
        write that publishes the film is still strict: if *that* fails the job
        fails, because a copy nothing records is a copy nobody can watch.
        """
        try:
            write()
        except Exception as exc:                       # noqa: BLE001
            if not trouble["said"]:
                trouble["said"] = True
                log(f"media {int(row['id'])}: could not record {what} ({exc});"
                    " carrying on with the film")

    def on_line(line: str) -> None:
        value = _progress_value(line, "total_size")
        if value is not None:
            wrote["size"] = value

            def keep(value=value):
                with ark_db.tx() as tick:
                    ark_space.touch(tick, receipt.id, value)

            note(keep, "how much has been written")
        total = _progress_value(line, "out_time_us") or _progress_value(line, "out_time_ms")
        duration = float(facts.get("duration") or 0)
        if total is not None and duration > 0:
            percent = min(99.0, (total / 1_000_000.0) / duration * 100.0)

            def beat(percent=percent):
                with ark_db.tx() as tick:
                    ark_media.heartbeat(tick, job_id, percent)

            note(beat, "how far along it is")

    with ark_space.hold(ark_media.media_root(), budget, owner=owner,
                        label=f"media {int(row['id'])} {plan}") as receipt:
        # The hardware decoder is fast and writes the same bytes, but a device
        # that exists is not a device that works.  A refusal *before a single
        # byte is written* is tried again on the CPU; a failure halfway through
        # is a real failure and is reported as one.
        hw = hwaccel_args(tool)
        failure = ""
        for extra in ([hw, []] if hw else [[]]):
            _unlink(partial)
            wrote["size"] = 0
            try:
                _run(build_command(tool, source, partial, facts, plan, budget,
                                   hwaccel=extra),
                     job_id=job_id, deadline=deadline, on_line=on_line)
                failure = ""
                break
            except Stopped:
                _unlink(partial)
                return {"ok": False, "cancelled": True, "error": "Stopped."}
            except RuntimeError as exc:
                _unlink(partial)
                failure = str(exc)
                if not extra or wrote["size"]:
                    return {"ok": False, "error": failure}
                log(f"media {int(row['id'])}: the hardware decoder refused this file "
                    f"({failure}); using the CPU")
        if failure:
            return {"ok": False, "error": failure}
        if not partial.is_file() or partial.stat().st_size == 0:
            _unlink(partial)
            return {"ok": False, "error": "The video tool wrote nothing."}
        short = _short_output(partial, facts)
        if short:
            _unlink(partial)
            return {"ok": False, "error": short}
        os.chmod(partial, 0o644)
        os.replace(partial, target)
        written = target.stat().st_size
        with ark_db.tx() as conn:
            changed = conn.execute(
                "UPDATE media SET play_stored = ?, play_size = ? WHERE id = ?",
                (name, written, int(row["id"]))).rowcount
        if not changed:
            # Deleted while it was being prepared: do not leave an orphan copy.
            _unlink(target)
            return {"ok": False, "error": "That film is no longer on the shelf."}
    return {"ok": True, "file": name, "bytes": written}


# ---------------------------------------------------------------------------
#  one job
# ---------------------------------------------------------------------------
def run_job(job) -> dict:
    """Do one claimed job and write down the result.  Never raises."""
    job_id = int(job["id"])
    media_id = int(job["media_id"])
    kind = str(job["kind"] or "")
    try:
        with ark_db.read() as conn:
            row = ark_media.item(conn, media_id)
        if row is None:
            with ark_db.tx() as conn:
                ark_media.finish(conn, job_id, "failed", "That film is no longer on the shelf.")
            return {"ok": False, "error": "gone"}
        if kind == "thumbnail":
            return _job_thumbnail(job, media_id)
        return _job_prepare(job_id, media_id, row)
    except Stopped:
        with ark_db.tx() as conn:
            ark_media.finish(conn, job_id, "cancelled", "Stopped by the owner.")
        return {"ok": False, "cancelled": True}
    except Exception as exc:                       # noqa: BLE001 — a job never kills the worker
        # Whatever went wrong, its half-written copy is not a film anybody can
        # watch, and it is this film's own file — safe to remove by name.
        _unlink(ark_media.temp_dir() / f"m{media_id}.mp4.part")
        detail = f"{type(exc).__name__}: {exc}"[:300]
        try:
            with ark_db.tx() as conn:
                ark_media.finish(conn, job_id, "failed", detail)
        except Exception as recording:              # noqa: BLE001
            # A database that is momentarily unwritable must not take the
            # worker with it. The row stays 'running'; `recover()` puts it back
            # in the queue the next time the box starts.
            log(f"job {job_id}: could not record the failure ({recording})")
        log(f"job {job_id} failed: {detail}")
        return {"ok": False, "error": str(exc)}


def _job_thumbnail(job, media_id: int) -> dict:
    job_id = int(job["id"])
    with ark_db.read() as conn:
        row = ark_media.item(conn, media_id)
    if row is None:
        return {"ok": False, "error": "gone"}
    # An owner may name the second they want; the job carries it in `detail`
    # (`at=12.5`) so the worker needs no second table for one number.
    at = None
    match = re.match(r"at=([0-9]+(?:\.[0-9]+)?)", str(job["detail"] or ""))
    if match:
        at = float(match.group(1))
    with ark_db.tx() as conn:
        ark_media.heartbeat(conn, job_id, 5)
    result = make_thumbnail(row, at=at)
    with ark_db.tx() as conn:
        if result.get("ok"):
            changed = conn.execute(
                "UPDATE media SET poster = ?, poster_at = ? WHERE id = ?",
                (result["file"], float(result["at"]), media_id)).rowcount
            if not changed:
                # The film was deleted while the picture was being taken.
                _unlink(ark_media.thumbs_dir() / result["file"])
                ark_media.finish(conn, job_id, "cancelled",
                                 "The film was removed while its picture was being taken.")
                return {"ok": False, "error": "gone"}
            ark_media.finish(conn, job_id, "ready", "Picture taken.", 100)
        else:
            ark_media.finish(conn, job_id, "failed", str(result.get("error") or "")[:300])
    return result


def _job_prepare(job_id: int, media_id: int, row) -> dict:
    """Look at the bytes again, take a picture, and make it playable if needed."""
    with ark_db.tx() as conn:
        ark_media.heartbeat(conn, job_id, 2)
    source = ark_media.original_path(row)
    facts = ark_media.probe(source)
    if not facts.get("ok"):
        with ark_db.tx() as conn:
            ark_media.finish(conn, job_id, "failed", str(facts.get("error") or "")[:300])
        return {"ok": False, "error": facts.get("error")}
    plan = ark_media.plan_for(facts, source)
    with ark_db.tx() as conn:
        conn.execute(
            "UPDATE media SET container = ?, vcodec = ?, acodec = ?, width = ?, height = ?,"
            " duration = ?, rotation = ?, probe = ?, plan = ? WHERE id = ?",
            (str(facts.get("container") or ""), str(facts.get("vcodec") or ""),
             str(facts.get("acodec") or ""), int(facts.get("width") or 0),
             int(facts.get("height") or 0), facts.get("duration"),
             int(facts.get("rotation") or 0), json.dumps(_storable(facts)), plan, media_id))
        ark_media.heartbeat(conn, job_id, 10)
        row = ark_media.item(conn, media_id)

    if plan == ark_media.UNSUPPORTED:
        with ark_db.tx() as conn:
            ark_media.finish(conn, job_id, "failed",
                             "There is no picture in that file that this box can play.")
        return {"ok": False, "error": "unsupported"}

    thumb = {"ok": False, "error": "not tried"}
    if not row["poster"]:
        thumb = make_thumbnail(row)
        if thumb.get("ok"):
            with ark_db.tx() as conn:
                conn.execute("UPDATE media SET poster = ?, poster_at = ? WHERE id = ?",
                             (thumb["file"], float(thumb["at"]), media_id))
        else:
            log(f"media {media_id}: no picture ({thumb.get('error')})")

    if plan in (ark_media.REMUX, ark_media.CONVERT):
        with ark_db.tx() as conn:
            ark_media.heartbeat(conn, job_id, 15)
            row = ark_media.item(conn, media_id)
        made = make_derivative(row, job_id=job_id, plan=plan)
        if made.get("cancelled"):
            return made
        if not made.get("ok"):
            detail = f"Playing copy could not be made: {made.get('error')}"
            with ark_db.tx() as conn:
                ark_media.finish(conn, job_id, "failed", detail[:300])
            return made
        with ark_db.tx() as conn:
            if ark_media.job_state(conn, job_id) == "cancelled":
                return {"ok": False, "cancelled": True, "error": "Stopped."}
            ark_media.finish(conn, job_id, "ready",
                             "Ready to play." if plan == ark_media.REMUX
                             else "Converted for this box.", 100)
        return {"ok": True, "plan": plan, "thumbnail": thumb}

    with ark_db.tx() as conn:
        ark_media.finish(conn, job_id, "ready", "Ready to play.", 100)
    return {"ok": True, "plan": plan, "thumbnail": thumb}


def _short_output(path: Path, facts: dict) -> str:
    """A playing copy that stopped early is not a playing copy.

    ``-fs`` is a size ceiling, and a ceiling reached is a film that ends in the
    middle; ffmpeg can report success after it.  The output is measured against
    the source it came from before anything is published, so a truncated file can
    never be recorded as *ready*.
    """
    source_duration = float(facts.get("duration") or 0)
    if source_duration <= 0:
        return ""
    made = ark_media.probe(path)
    if not made.get("ok"):
        return "The playing copy could not be read back."
    made_duration = float(made.get("duration") or 0)
    if made_duration <= 0:
        return "The playing copy has no length, so it is not whole."
    if made_duration < source_duration * 0.97:
        return (f"The playing copy came out short ({made_duration:.0f}s of "
                f"{source_duration:.0f}s) — it was not kept.")
    return ""


def _storable(facts: dict) -> dict:
    return ark_media._storable(facts)


def _unlink(path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass


# ---------------------------------------------------------------------------
#  the worker's loop
# ---------------------------------------------------------------------------
def idle_seconds() -> float:
    try:
        return max(0.5, float(os.environ.get("ARK_MEDIA_IDLE", "3")))
    except ValueError:
        return 3.0


def work_once(pid: int = 0) -> bool:
    """Claim and finish one job.  Returns ``False`` when the queue was empty."""
    with ark_db.tx() as conn:
        job = ark_media.claim(conn, pid)
    if job is None:
        return False
    log(f"job {int(job['id'])} ({job['kind']}) for film {int(job['media_id'])}")
    result = run_job(job)
    log(f"job {int(job['id'])} -> {json.dumps(result, default=str)[:200]}")
    return True


def startup() -> dict:
    """Reconcile whatever a crash left behind, before claiming new work."""
    ark_media.ensure_dirs()
    with ark_db.tx() as conn:
        recovered = ark_media.recover(conn)
        released = ark_space.reconcile(conn=conn)
    return {**recovered, "reservations_released": released}
