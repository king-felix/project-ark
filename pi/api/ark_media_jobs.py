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
CONVERT_FACTOR = 3.0          # seconds of encoding per second of film, at most
MIN_TIMEOUT = 120.0
MAX_TIMEOUT = 6 * 3600.0
#: Never let one conversion write more than this, whatever the input claims.
CONVERT_CEILING = 8 * 1024 ** 3

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
    try:
        with ark_db.read() as conn:
            return ark_media.job_state(conn, job_id) == "cancelled"
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
    cmd = [tool, "-nostdin", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", str(source),
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


def make_thumbnail(conn, row, *, at: float | None = None, tool: str | None = None) -> dict:
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
    cmd = [tool, "-nostdin", "-v", "error", "-y", "-ss", f"{max(0.0, chosen):.3f}",
           "-i", str(source), "-frames:v", "1", "-vf", "scale=-2:360", "-q:v", "4",
           "-f", "mjpeg", str(partial)]
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
    conn.execute("UPDATE media SET poster = ?, poster_at = ? WHERE id = ?",
                 (name, float(chosen), int(row["id"])))
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
                  budget: int) -> list[str]:
    """The exact argument array for a repack or an encode.

    Separated from running it so a test can read the plan without spending the
    electricity — and so the workstation recipe printed to the owner is built
    from the same function the box uses.
    """
    cmd = [tool, "-nostdin", "-v", "error", "-y", "-i", str(source),
           "-map", "0:v:0", "-map", "0:a:0?", "-max_muxing_queue_size", "1024"]
    if plan == ark_media.REMUX or not _needs_encode(facts):
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p"]
        height = int(facts.get("height") or 0)
        if height > 1080:
            cmd += ["-vf", "scale=-2:1080"]
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

    def on_line(line: str) -> None:
        value = _progress_value(line, "total_size")
        if value is not None:
            wrote["size"] = value
            with ark_db.tx() as tick:
                ark_space.touch(tick, receipt.id, value)
        total = _progress_value(line, "out_time_us") or _progress_value(line, "out_time_ms")
        duration = float(facts.get("duration") or 0)
        if total is not None and duration > 0:
            percent = min(99.0, (total / 1_000_000.0) / duration * 100.0)
            with ark_db.tx() as tick:
                ark_media.heartbeat(tick, job_id, percent)

    with ark_space.hold(ark_media.media_root(), budget, owner=owner,
                        label=f"media {int(row['id'])} {plan}") as receipt:
        cmd = build_command(tool, source, partial, facts, plan, budget)
        try:
            _run(cmd, job_id=job_id, deadline=deadline, on_line=on_line)
        except Stopped:
            _unlink(partial)
            return {"ok": False, "cancelled": True, "error": "Stopped."}
        except RuntimeError as exc:
            _unlink(partial)
            return {"ok": False, "error": str(exc)}
        if not partial.is_file() or partial.stat().st_size == 0:
            _unlink(partial)
            return {"ok": False, "error": "The video tool wrote nothing."}
        os.chmod(partial, 0o644)
        os.replace(partial, target)
        written = target.stat().st_size
        with ark_db.tx() as conn:
            conn.execute("UPDATE media SET play_stored = ?, play_size = ? WHERE id = ?",
                         (name, written, int(row["id"])))
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
            return _job_thumbnail(job_id, media_id)
        return _job_prepare(job_id, media_id, row)
    except Stopped:
        with ark_db.tx() as conn:
            ark_media.finish(conn, job_id, "cancelled", "Stopped by the owner.")
        return {"ok": False, "cancelled": True}
    except Exception as exc:                       # noqa: BLE001 — a job never kills the worker
        with ark_db.tx() as conn:
            ark_media.finish(conn, job_id, "failed", f"{type(exc).__name__}: {exc}"[:300])
        log(f"job {job_id} failed: {type(exc).__name__}: {exc}")
        return {"ok": False, "error": str(exc)}


def _job_thumbnail(job_id: int, media_id: int) -> dict:
    with ark_db.read() as conn:
        row = ark_media.item(conn, media_id)
    if row is None:
        return {"ok": False, "error": "gone"}
    with ark_db.tx() as conn:
        ark_media.heartbeat(conn, job_id, 5)
    with ark_db.tx() as conn:
        result = make_thumbnail(conn, row)
    with ark_db.tx() as conn:
        if result.get("ok"):
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
        with ark_db.tx() as conn:
            thumb = make_thumbnail(conn, row)
        if not thumb.get("ok"):
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
            ark_media.finish(conn, job_id, "ready",
                             "Ready to play." if plan == ark_media.REMUX
                             else "Converted for this box.", 100)
        return {"ok": True, "plan": plan, "thumbnail": thumb}

    with ark_db.tx() as conn:
        ark_media.finish(conn, job_id, "ready", "Ready to play.", 100)
    return {"ok": True, "plan": plan, "thumbnail": thumb}


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
