"""Autonomous job runner: 6, 12 or 24 hour jobs with a target, ticks and checkpoints.

Runs as a background thread inside the Streamlit server process. Jobs are stored in the database, so a restart
resumes any job that has not passed its end time. The server process must stay running for jobs to progress
(run the app on an always-on machine or VPS for 12 and 24 hour jobs).
"""
import threading
import time

from . import db, agents, security

_lock = threading.Lock()
_started = False


def start_job(agent, objective, target, hours, tick_minutes, actor):
    if hours <= 0 or tick_minutes <= 0:
        raise ValueError("Hours and tick minutes must be positive.")
    if not agents.get_agent(agent):
        raise ValueError("Unknown agent.")
    now = time.time()
    jid = db.x("INSERT INTO jobs(ts,agent,objective,target,hours,tick_minutes,status,ends,last_tick) VALUES(?,?,?,?,?,?,?,?,?)",
               (now, agent, objective, target, hours, tick_minutes, "RUNNING", now + hours * 3600, 0))
    security.audit(actor, "JOB_STARTED", f"#{jid} {agent} {hours}h target={target[:80]}")
    return jid


def stop_job(job_id, actor):
    db.x("UPDATE jobs SET status='STOPPED' WHERE id=? AND status='RUNNING'", (job_id,))
    security.audit(actor, "JOB_STOPPED", f"#{job_id}")


def checkpoints(job_id):
    return db.q("SELECT * FROM checkpoints WHERE job_id=? ORDER BY id", (job_id,))


def _note(job_id, text):
    db.x("INSERT INTO checkpoints(job_id,ts,note) VALUES(?,?,?)", (job_id, time.time(), text[:4000]))


def tick(job):
    prev = "\n".join(f"- {c['note'][:300]}" for c in checkpoints(job["id"])[-5:]) or "(none yet)"
    prompt = (f"Autonomous job #{job['id']}. Objective: {job['objective']}\nTarget: {job['target']}\n"
              f"Recent checkpoints:\n{prev}\n\nDo the next concrete step using your tools. Reply with a 2-3 sentence checkpoint: "
              f"what you did, progress toward the target, and what you will do next.")
    try:
        note = agents.run_agent(job["agent"], prompt)
    except Exception as e:
        note = f"ERROR during tick: {e}"
    _note(job["id"], note)
    db.x("UPDATE jobs SET last_tick=? WHERE id=?", (time.time(), job["id"]))


def finish(job):
    cps = "\n".join(f"- {c['note'][:400]}" for c in checkpoints(job["id"]))
    report = f"Job #{job['id']} finished its {job['hours']}h window.\nTarget: {job['target']}\nCheckpoints:\n{cps or '(none)'}"
    if agents.has_key() and cps:
        try:
            report = agents.run_agent(job["agent"], f"Write the final report for this job against its target.\n{report}")
        except Exception:
            pass
    db.x("UPDATE jobs SET status='COMPLETED', report=? WHERE id=?", (report, job["id"]))
    security.audit("runner", "JOB_COMPLETED", f"#{job['id']}")


def _loop():
    while True:
        try:
            now = time.time()
            from . import recurring
            recurring.generate_due()  # scheduled payments become drafts that wait for a human approval
            for job in db.q("SELECT * FROM jobs WHERE status='RUNNING'"):
                if now >= job["ends"]:
                    finish(job)
                elif now - job["last_tick"] >= job["tick_minutes"] * 60:
                    tick(job)
        except Exception:
            pass
        time.sleep(15)


def ensure_started():
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_loop, daemon=True, name="orchestra-runner").start()
            _started = True
