"""Phase 13 scheduling: scheduled publications, the scheduler worker, rescheduling and cancelling. Offline."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_harness import run_program  # noqa: E402

SCHEDULING = r'''
from app import scheduler_worker
connect_youtube()
connect_channel("tiktok")
now = datetime.now(timezone.utc)
later = (now + timedelta(hours=2)).isoformat()

def publish_jobs(run_id):
    with Session() as db:
        return sorted((job.logical_key, job.state) for job in db.scalars(
            select(WorkflowJob).where(WorkflowJob.logical_key.like(f"publish:%:{run_id}%"))))

# A future time stores the publications as scheduled, with no upload job yet.
run_id, _ = approved_run()
created = publish(run_id, target("youtube"), target("tiktok"), scheduled_for=later, status=201).json()["publications"]
assert [p["state"] for p in created] == ["scheduled", "scheduled"], created
assert created[0]["scheduled_for"].startswith(later[:16]) and created[0]["can_reschedule"], created
assert publish_jobs(run_id) == []
# A time that has already passed means "now".
run_now, _ = approved_run()
immediate = publish(run_now, target("youtube"), scheduled_for=(now - timedelta(minutes=5)).isoformat(), status=201)
assert immediate.json()["publications"][0]["state"] == "queued"
bad = publish(run_now, target("tiktok"), scheduled_for=(now + timedelta(days=400)).isoformat())
assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_schedule", bad.text

# Nothing is due yet; at the scheduled time each channel gets its own job, exactly once.
assert scheduler_worker.run_once(now=now) == 0
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 2
assert publish_jobs(run_id) == [(f"publish:tiktok:{run_id}", "queued"), (f"publish:youtube:{run_id}", "queued")]
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 0  # idempotent
assert [publication(p["id"])["state"] for p in created] == ["queued", "queued"]

# Restart safety: a pass whose transaction never committed leaves the publication scheduled.
run2, _ = approved_run()
pending = publish(run2, target("youtube"), scheduled_for=later, status=201).json()["publications"][0]
db = Session()
publications.dispatch_due(db, now=now + timedelta(hours=3))
db.rollback()
db.close()
assert publication(pending["id"])["state"] == "scheduled" and publish_jobs(run2) == []
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 1
assert publish_jobs(run2) == [(f"publish:youtube:{run2}", "queued")]

# Rescheduling and cancelling work until the upload starts.
run3, _ = approved_run()
item = publish(run3, target("youtube"), target("tiktok"), scheduled_for=later, status=201).json()["publications"]
moved = client.put(f"/api/publications/{item[0]['id']}/schedule",
                   json={"scheduled_for": (now + timedelta(days=1)).isoformat()})
assert moved.status_code == 200 and moved.json()["scheduled_for"].startswith((now + timedelta(days=1)).isoformat()[:16]), moved.text
cancelled = client.post(f"/api/publications/{item[1]['id']}/cancel")
assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled", cancelled.text
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 0  # moved to tomorrow; the other was cancelled
listed = client.get("/api/publications", params={"start": (now + timedelta(hours=20)).isoformat(),
                                                  "end": (now + timedelta(hours=30)).isoformat()}).json()["publications"]
assert [p["id"] for p in listed] == [item[0]["id"]], listed
# A queued upload that no worker has claimed can still be moved: its job is withdrawn.
queued = publish(run_now, target("tiktok"), status=201).json()["publications"][0]
moved = client.put(f"/api/publications/{queued['id']}/schedule", json={"scheduled_for": later})
assert moved.status_code == 200 and moved.json()["state"] == "scheduled", moved.text
assert (f"publish:tiktok:{run_now}", "failed") in publish_jobs(run_now)
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 1
assert sum(1 for key, state in publish_jobs(run_now) if key.startswith(f"publish:tiktok:{run_now}:retry:") and state == "queued") == 1
# Once a worker holds the job, it is too late.
with Session.begin() as db:
    claimed = jobs.claim_due_jobs(db, worker_id="social-test", logical_key_prefix=f"publish:tiktok:{run_now}")
assert claimed
late = client.post(f"/api/publications/{queued['id']}/cancel")
assert late.status_code == 409 and late.json()["detail"]["code"] == "upload_started", late.text
assert client.put(f"/api/publications/{queued['id']}/schedule", json={"scheduled_for": later}).status_code == 409

# A cancelled publication can be published again later; the row is reused.
again = publish(run3, target("tiktok", title="Lần hai"), status=201).json()["publications"][0]
assert (again["id"], again["state"], again["title"]) == (item[1]["id"], "queued", "Lần hai"), again

# A channel disconnected before the scheduled time fails clearly, and can be retried after reconnecting.
run4, _ = approved_run()
waiting = publish(run4, target("tiktok"), scheduled_for=later, status=201).json()["publications"][0]
assert client.delete("/api/channels/tiktok").status_code == 204
assert scheduler_worker.run_once(now=now + timedelta(hours=3)) == 1
failed = publication(waiting["id"])
assert (failed["state"], failed["last_error"], failed["can_retry"]) == ("failed", "schedule:connection_required", True), failed
connect_channel("tiktok")
retried = client.post(f"/api/publications/{waiting['id']}/retry")
assert retried.status_code == 202 and retried.json()["state"] == "queued", retried.text
assert publish_jobs(run4) == [(f"publish:tiktok:{run4}", "queued")]

# The calendar sees every channel; a member's view never includes tokens.
no_secrets(client.get("/api/publications").text)
print("scheduling ok")
'''


class SchedulingTest(unittest.TestCase):
    def test_scheduled_publications_are_dispatched_once_and_can_be_moved_or_cancelled(self):
        completed = run_program(SCHEDULING)
        self.assertEqual(completed.returncode, 0, completed.stdout[-2000:] + completed.stderr[-6000:])


if __name__ == "__main__":
    unittest.main()
