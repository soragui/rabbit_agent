"""s14: Cron Scheduler — time-based automatic task triggering."""
import json
import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime

from config import CRON_JOBS_FILE


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool
    durable: bool


scheduled_jobs: dict[str, CronJob] = {}
cron_queue: list[CronJob] = []
cron_lock = threading.Lock()
_last_fired: dict[str, str] = {}
_lock = threading.Lock()


def _cron_field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if "/" in part:
            step_part, step = part.split("/", 1)
            step = int(step)
            if value % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= value <= int(hi):
                return True
        elif part == str(value):
            return True
    return False


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    dow_val = (dt.weekday() + 1) % 7
    m = _cron_field_matches(minute, dt.minute)
    h = _cron_field_matches(hour, dt.hour)
    if not (m and h and _cron_field_matches(month, dt.month)):
        return False
    du, wu = dom == "*", dow == "*"
    if du and wu:
        return True
    if du:
        return _cron_field_matches(dow, dow_val)
    if wu:
        return _cron_field_matches(dom, dt.day)
    return _cron_field_matches(dom, dt.day) or _cron_field_matches(dow, dow_val)


def validate_cron(expr: str) -> str | None:
    fields = expr.strip().split()
    if len(fields) != 5:
        return "Cron expression must have 5 fields"
    try:
        for f in fields:
            if f == "*":
                continue
            for part in f.split(","):
                int(part.replace("/", " ").replace("-", " ").split()[0])
    except ValueError:
        return f"Invalid cron field: {f}"
    return None


def _save_durable():
    durable = [asdict(j) for j in scheduled_jobs.values() if j.durable]
    CRON_JOBS_FILE.write_text(json.dumps({"tasks": durable}, indent=2))


def _load_durable():
    if not CRON_JOBS_FILE.exists():
        return
    for jd in json.loads(CRON_JOBS_FILE.read_text()).get("tasks", []):
        job = CronJob(**jd)
        scheduled_jobs[job.id] = job


def schedule_job(cron: str, prompt: str, recurring: bool = True, durable: bool = True) -> str:
    err = validate_cron(cron)
    if err:
        return err
    job_id = f"cron_{int(time.time())}_{random.randint(0, 9999):04d}"
    scheduled_jobs[job_id] = CronJob(id=job_id, cron=cron, prompt=prompt,
                                     recurring=recurring, durable=durable)
    if durable:
        _save_durable()
    return f"Scheduled {job_id}: '{cron}' -> '{prompt[:50]}'"


def cancel_job(job_id: str) -> str:
    if job_id not in scheduled_jobs:
        return f"Job {job_id} not found"
    scheduled_jobs.pop(job_id)
    _save_durable()
    return f"Cancelled {job_id}"


def list_crons() -> str:
    if not scheduled_jobs:
        return "(no cron jobs)"
    lines = []
    for j in scheduled_jobs.values():
        lines.append(f"  {j.id}: {j.cron} '{j.prompt[:50]}' "
                     f"({'recurring' if j.recurring else 'one-shot'}, "
                     f"{'durable' if j.durable else 'session'})")
    return "\n".join(lines)


def has_queue() -> bool:
    with cron_lock:
        return len(cron_queue) > 0


def consume_queue() -> list[CronJob]:
    with cron_lock:
        jobs = list(cron_queue)
        cron_queue.clear()
    return jobs


def scheduler_loop():
    """Daemon thread: check cron expressions every second."""
    _load_durable()
    while True:
        time.sleep(1)
        now = datetime.now()
        minute_marker = now.strftime("%Y-%m-%d %H:%M")
        with cron_lock:
            for job in list(scheduled_jobs.values()):
                try:
                    if cron_matches(job.cron, now):
                        if _last_fired.get(job.id) != minute_marker:
                            cron_queue.append(job)
                            _last_fired[job.id] = minute_marker
                            print(f"\n  [cron] Fired: {job.id} — {job.prompt[:50]}")
                        if not job.recurring:
                            scheduled_jobs.pop(job.id, None)
                            if job.durable:
                                _save_durable()
                except Exception as e:
                    print(f"  [cron error] {job.id}: {e}")
