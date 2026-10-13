"""How a sandbox session starts: the server's record of its steps and log.

Tests images/server/omnigent_startup.py on its own, with Pods and events
shaped like the Kubernetes client's.
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "images" / "server"))
import omnigent_startup as startup  # noqa: E402

SESSION = "conv_startup"


def pod(
    *,
    scheduled: str | None = "True",
    reason: str | None = None,
    message: str | None = None,
    node: str | None = "vm-omnigent",
    init: object | None = None,
    host: object | None = None,
) -> NS:
    conditions = []
    if scheduled is not None:
        conditions.append(NS(type="PodScheduled", status=scheduled, reason=reason, message=message))
    return NS(
        spec=NS(node_name=node),
        status=NS(
            conditions=conditions,
            init_container_statuses=[init] if init else [],
            container_statuses=[host] if host else [],
        ),
    )


def container(name: str, *, running: bool = False, waiting: str | None = None,
              message: str | None = None, exit_code: int | None = None) -> NS:
    return NS(
        name=name,
        state=NS(
            running=NS() if running else None,
            waiting=NS(reason=waiting, message=message) if waiting else None,
            terminated=NS(exit_code=exit_code, reason="Error") if exit_code is not None else None,
        ),
        last_state=NS(terminated=None),
        restart_count=0,
    )


def event(reason: str, message: str, *, uid: str = "e1", kind: str = "Pod",
          type_: str = "Normal", at: datetime | None = None) -> NS:
    return NS(
        reason=reason,
        message=message,
        type=type_,
        count=1,
        metadata=NS(uid=uid),
        involved_object=NS(kind=kind),
        last_timestamp=at or datetime.now(timezone.utc),
        event_time=None,
    )


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        startup._records.clear()
        startup._host_sessions.clear()
        startup._wakes.clear()
        startup._runner_exits.clear()
        self.published: list[tuple[str, dict]] = []
        startup.set_publisher(lambda sid, record: self.published.append((sid, record)))
        self.addCleanup(startup.set_publisher, None)

    def record(self) -> dict:
        snap = startup.snapshot(SESSION)
        assert snap is not None
        return snap

    def step(self, step_id: str) -> dict:
        return next(s for s in self.record()["steps"] if s["id"] == step_id)

    def log_texts(self) -> list[str]:
        page = startup.log_page(SESSION)
        assert page is not None
        return [line["text"] for line in page["lines"]]


class StagesTests(Fixture):
    def test_a_launch_goes_from_creating_the_sandbox_to_the_agent_ready(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        self.assertEqual(self.record()["kind"], "launch")
        self.assertEqual(self.step("sandbox")["state"], "running")
        self.assertEqual([s["id"] for s in self.record()["steps"]], ["sandbox", "host", "agent"])
        startup.on_stage(SESSION, "starting")
        startup.on_stage(SESSION, "connecting")
        self.assertEqual(self.step("host")["state"], "done")
        self.assertEqual(self.step("agent")["state"], "running")
        startup.on_stage(SESSION, "ready")
        record = self.record()
        self.assertEqual(record["outcome"], "ready")
        self.assertIsNotNone(record["ended_at"])
        self.assertTrue(all(s["state"] == "done" for s in record["steps"]))
        self.assertEqual(self.published[-1], (SESSION, record))

    def test_a_wake_is_marked_as_one(self) -> None:
        startup.expect_wake(SESSION)
        startup.on_stage(SESSION, "provisioning")
        self.assertEqual(self.record()["kind"], "wake")
        self.assertIn("Waking the stopped sandbox", self.log_texts())

    def test_stages_without_a_record_are_ignored(self) -> None:
        startup.on_stage(SESSION, "connecting")
        startup.step(SESSION, "host", "done")
        self.assertIsNone(startup.snapshot(SESSION))

    def test_a_new_start_replaces_a_finished_one(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.on_stage(SESSION, "failed", "managed sandbox launch failed: boom")
        first = self.record()["started_at"]
        startup.on_stage(SESSION, "provisioning")
        self.assertEqual(self.record()["outcome"], "running")
        self.assertGreaterEqual(self.record()["started_at"], first)
        self.assertIsNone(self.record()["failure"])

    def test_cancelling_ends_it_and_ignores_the_ready_that_follows(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.cancelled(SESSION)
        startup.on_stage(SESSION, "ready")
        self.assertEqual(self.record()["outcome"], "cancelled")

    def test_a_finished_step_does_not_go_back_to_running(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.step(SESSION, "image", "done", "Already on the VM")
        startup.step(SESSION, "image", "running")
        self.assertEqual(self.step("image")["state"], "done")
        self.assertEqual(self.step("image")["detail"], "Already on the VM")

    def test_steps_keep_their_order(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.step(SESSION, "setup", "running", child="roadpass")
        startup.step(SESSION, "schedule", "done")
        self.assertEqual(
            [s["id"] for s in self.record()["steps"]],
            ["sandbox", "schedule", "host", "setup", "agent"],
        )

    def test_the_current_task_finds_its_session(self) -> None:
        async def launch() -> str | None:
            startup.bind(SESSION)
            return await asyncio.to_thread(startup.current_session)

        self.assertEqual(asyncio.run(launch()), SESSION)
        self.assertIsNone(startup.current_session())


class PodTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        startup.on_stage(SESSION, "provisioning")
        startup.on_stage(SESSION, "starting")

    def test_a_scheduled_pod_says_where(self) -> None:
        startup.observe_pod(SESSION, pod())
        self.assertEqual(self.step("sandbox")["state"], "done")
        self.assertEqual(self.step("schedule")["state"], "done")
        self.assertEqual(self.step("schedule")["detail"], "on vm-omnigent")
        self.assertEqual(self.step("image")["state"], "running")

    def test_no_room_is_a_warning_in_plain_words(self) -> None:
        startup.observe_pod(SESSION, pod(
            scheduled="False", reason="Unschedulable",
            message="0/1 nodes are available: 1 Insufficient memory.",
        ))
        step = self.step("schedule")
        self.assertEqual((step["state"], step["tone"]), ("running", "warning"))
        self.assertIn("doesn't have enough free memory", step["detail"])
        startup.observe_pod(SESSION, pod())
        step = self.step("schedule")
        self.assertEqual((step["state"], step["tone"]), ("done", None))

    def test_an_image_that_can_not_be_pulled_is_a_warning(self) -> None:
        startup.observe_pod(SESSION, pod(init=container(
            "workspace-prep", waiting="ImagePullBackOff",
            message='failed to pull "ghcr.io/x": 401 Unauthorized',
        )))
        step = self.step("image")
        self.assertEqual(step["tone"], "warning")
        self.assertIn("refused", step["detail"])

    def test_a_running_container_means_the_image_is_there(self) -> None:
        startup.observe_pod(SESSION, pod(init=container("workspace-prep", running=True)))
        self.assertEqual(self.step("image")["state"], "done")
        startup.observe_pod(SESSION, pod(
            init=container("workspace-prep", exit_code=0),
            host=container("host", running=True),
        ))
        self.assertEqual(self.step("host")["state"], "running")
        self.assertEqual(self.step("host")["detail"], "Connecting to Omnigent")

    def test_image_events_fill_in_the_detail_and_are_logged_once(self) -> None:
        events = [
            event("Scheduled", "Successfully assigned omnigent-sandboxes/x to vm-omnigent", uid="a"),
            event("Pulled", 'Container image "ghcr.io/x" already present on machine', uid="b"),
        ]
        startup.observe_events(SESSION, events)
        startup.observe_events(SESSION, events)
        self.assertEqual(self.step("image")["detail"], "Already on the VM")
        texts = self.log_texts()
        self.assertEqual(sum("already present" in t for t in texts), 1)
        self.assertIn("Scheduled: Successfully assigned omnigent-sandboxes/x to vm-omnigent", texts)

    def test_a_download_says_how_long_it_took(self) -> None:
        startup.observe_events(SESSION, [
            event("Pulling", 'Pulling image "ghcr.io/x"', uid="a"),
            event("Pulled", 'Successfully pulled image "ghcr.io/x" in 21.3s (21.3s including waiting)', uid="b"),
        ])
        self.assertEqual(self.step("image")["detail"], "Downloaded in 21s")

    def test_events_from_before_the_start_are_skipped(self) -> None:
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        startup.observe_events(SESSION, [event("Killing", "Stopping container host", at=old)])
        self.assertFalse(any("Stopping container" in t for t in self.log_texts()))

    def test_sandbox_events_say_what_they_are_about(self) -> None:
        startup.observe_events(SESSION, [
            event("FailedCreate", 'pods "x" is forbidden: exceeded quota', kind="Sandbox", type_="Warning"),
        ])
        page = startup.log_page(SESSION)
        assert page is not None
        line = page["lines"][-1]
        self.assertEqual(line["text"], 'Sandbox FailedCreate: pods "x" is forbidden: exceeded quota')
        self.assertEqual(line["level"], "warning")


class CloneLogTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        startup.on_stage(SESSION, "provisioning")

    def test_prep_lines_move_each_repository(self) -> None:
        startup.observe_container_log(SESSION, "workspace-prep", "\n".join([
            "omnigent-prep: cloning omnigent",
            "omnigent-prep: cloning roadpass",
            "Cloning into '/home/omnigent/workspace/omnigent.tmp/clone'...",
        ]))
        clone = self.step("clone")
        self.assertEqual(clone["state"], "running")
        self.assertEqual([c["name"] for c in clone["children"]], ["omnigent", "roadpass"])
        self.assertIn("Cloning into '/home/omnigent/workspace/omnigent.tmp/clone'...", self.log_texts())
        self.assertFalse(any(t.startswith("omnigent-prep:") for t in self.log_texts()))
        startup.observe_container_log(SESSION, "workspace-prep", "\n".join([
            "omnigent-prep: cloning omnigent",
            "omnigent-prep: cloning roadpass",
            "Cloning into '/home/omnigent/workspace/omnigent.tmp/clone'...",
            "omnigent-prep: cloned omnigent",
            "omnigent-prep: cloned roadpass",
        ]))
        clone = self.step("clone")
        self.assertEqual(clone["state"], "done")
        self.assertEqual(self.log_texts().count("Cloning into '/home/omnigent/workspace/omnigent.tmp/clone'..."), 1)

    def test_checkouts_kept_from_last_time_skip_the_step(self) -> None:
        startup.observe_container_log(SESSION, "workspace-prep", "omnigent-prep: kept roadpass\n")
        clone = self.step("clone")
        self.assertEqual(clone["state"], "skipped")
        self.assertEqual(clone["children"][0]["detail"], "kept from last time")

    def test_a_failed_clone_fails_the_step_and_logs_git_as_an_error(self) -> None:
        startup.observe_container_log(SESSION, "workspace-prep", "\n".join([
            "omnigent-prep: cloning roadpass",
            "fatal: Authentication failed for 'https://github.com/acme/roadpass.git/'",
            "omnigent-prep: failed roadpass",
        ]))
        self.assertEqual(self.step("clone")["state"], "failed")
        page = startup.log_page(SESSION)
        assert page is not None
        self.assertEqual(page["lines"][-1]["level"], "error")

    def test_a_restarted_container_starts_its_log_over(self) -> None:
        startup.observe_container_log(SESSION, "host", "a\nb\nc\n")
        startup.observe_container_log(SESSION, "host", "d\n")
        self.assertEqual(self.log_texts()[-4:], ["a", "b", "c", "d"])


GIT_AUTH = (
    "managed sandbox host startup failed: Kubernetes sandbox pod 'omnigent-n3bby' "
    "workspace prep failed (exit 128, Error, after 2 attempts). Recent events: BackOff: "
    "Back-off restarting failed container. Container 'workspace-prep' log tail: "
    "omnigent-prep: cloning roadpass\nCloning into '/home/omnigent/workspace/roadpass.tmp/clone'...\n"
    "remote: Invalid username or token. Password authentication is not supported for Git operations.\n"
    "fatal: Authentication failed for 'https://github.com/acme/roadpass.git/'\n"
    "omnigent-prep: failed roadpass Inspect with `kubectl describe pod omnigent-n3bby -n omnigent-sandboxes`."
)


class ExplainTests(Fixture):
    def test_a_rejected_git_token_names_the_repository(self) -> None:
        result = startup.explain(GIT_AUTH)
        self.assertEqual(result["step"], "clone")
        self.assertEqual(result["title"], "Couldn't clone roadpass")
        self.assertIn("GitHub refused the Git token for roadpass", result["body"])
        self.assertEqual(result["child"], "roadpass")
        self.assertEqual(result["source"], "workspace-prep")
        self.assertIn("fatal: Authentication failed", result["log"])
        self.assertNotIn("Inspect with", result["log"])

    def test_a_missing_branch(self) -> None:
        error = GIT_AUTH.replace(
            "fatal: Authentication failed for 'https://github.com/acme/roadpass.git/'",
            "warning: Could not find remote branch fix/x to clone.\nfatal: Remote branch fix/x not found in upstream origin",
        ).replace("remote: Invalid username or token. Password authentication is not supported for Git operations.\n", "")
        self.assertEqual(startup.explain(error)["body"], "The branch fix/x doesn't exist in roadpass.")

    def test_no_room_on_the_vm(self) -> None:
        result = startup.explain(
            "managed sandbox host startup failed: Kubernetes sandbox pod 'x' did not start within "
            "300s (last phase 'Pending'; last reason: Unschedulable: 0/1 nodes are available: 1 "
            "Insufficient memory.). Inspect with `kubectl describe pod x -n y`."
        )
        self.assertEqual((result["step"], result["title"]), ("schedule", "No room on the VM"))
        self.assertIn("enough free memory", result["body"])

    def test_an_image_that_can_not_be_pulled(self) -> None:
        result = startup.explain(
            "managed host wake failed: Kubernetes sandbox pod 'x' did not start within 300s "
            "(last phase 'Pending'; last reason: ImagePullBackOff: Back-off pulling image "
            '"ghcr.io/x": 401 Unauthorized).'
        )
        self.assertEqual(result["step"], "image")
        self.assertIn("unauthorized", result["body"])

    def test_a_host_that_never_connects_brings_its_log(self) -> None:
        result = startup.explain(
            "managed sandbox host startup failed: managed host did not come online within 120s. "
            "Container 'host' log tail: Traceback (most recent call last):\nValueError: bad config"
        )
        self.assertEqual((result["step"], result["title"]), ("host", "The host started but never connected"))
        self.assertEqual(result["source"], "host")
        self.assertIn("ValueError: bad config", result["log"])

    def test_an_agent_that_exits_brings_its_log(self) -> None:
        result = startup.explain(
            "the agent exited while starting: runner process exited with code 1\nImportError: x"
        )
        self.assertEqual(result["step"], "agent")
        self.assertIn("ImportError: x", result["log"])

    def test_anything_else_keeps_the_message_without_prefixes(self) -> None:
        result = startup.explain("managed sandbox launch failed: spend limit reached")
        self.assertEqual(result["title"], "The sandbox couldn't start")
        self.assertEqual(result["body"], "spend limit reached")

    def test_a_failed_stage_fails_the_step_it_names(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.on_stage(SESSION, "starting")
        startup.observe_container_log(SESSION, "workspace-prep", "omnigent-prep: cloning roadpass\n")
        startup.on_stage(SESSION, "failed", GIT_AUTH)
        record = self.record()
        self.assertEqual(record["outcome"], "failed")
        self.assertEqual(self.step("clone")["state"], "failed")
        self.assertEqual(self.step("clone")["children"][0]["state"], "failed")
        self.assertEqual(record["failure"]["title"], "Couldn't clone roadpass")
        self.assertEqual(record["failure"]["raw"], GIT_AUTH)
        self.assertNotIn("step", record["failure"])

    def test_a_failure_with_no_step_named_fails_the_running_one(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.on_stage(SESSION, "connecting")
        startup.on_stage(SESSION, "failed", "something odd")
        self.assertEqual(self.step("agent")["state"], "failed")


class HostReportTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        startup.on_stage(SESSION, "provisioning")
        startup.attach_host("host_1", SESSION)

    def test_reports_for_an_unknown_host_are_not_accepted(self) -> None:
        self.assertFalse(startup.host_report("host_2", {"steps": []}))

    def test_setup_scripts_and_services_move_their_steps(self) -> None:
        self.assertTrue(startup.host_report("host_1", {
            "steps": [
                {"step": "setup", "child": "roadpass", "state": "running", "detail": "Running its setup script"},
                {"step": "host", "child": "Tailscale", "state": "done", "detail": "omnigent-1.ts.net"},
                {"step": "host", "child": "Something", "state": "done"},
                {"step": "agent", "child": "x", "state": "done"},
                {"step": "setup", "child": "x", "state": "exploded"},
            ],
            "log": [
                {"source": "setup", "text": "+ pnpm install", "at": 1.0},
                {"source": "k8s", "text": "not from the Pod"},
            ],
        }))
        self.assertEqual(self.step("setup")["state"], "running")
        self.assertEqual([c["name"] for c in self.step("setup")["children"]], ["roadpass"])
        self.assertEqual([c["name"] for c in self.step("host")["children"]], ["Tailscale"])
        self.assertEqual(self.step("agent")["state"], "pending")
        self.assertIn("+ pnpm install", self.log_texts())
        self.assertNotIn("not from the Pod", self.log_texts())

    def test_a_failed_setup_script_is_a_warning_with_its_log(self) -> None:
        startup.on_stage(SESSION, "ready")
        startup.host_report("host_1", {"steps": [{
            "step": "setup", "child": "roadpass", "state": "failed", "detail": "Exit status 127",
            "log_tail": "+ pnpm install\nbash: line 6: pnpm: command not found\n",
        }]})
        record = self.record()
        self.assertEqual(record["outcome"], "ready")
        self.assertEqual(record["warning"]["title"], "roadpass's setup script failed")
        self.assertIn("pnpm: command not found", record["warning"]["log"])
        self.assertTrue(record["warning"]["body"].startswith("Exit status 127. The agent started anyway."))

    def test_the_log_stays_live_while_a_setup_script_runs(self) -> None:
        startup.host_report("host_1", {"steps": [{"step": "setup", "child": "roadpass", "state": "running"}]})
        startup.on_stage(SESSION, "ready")
        page = startup.log_page(SESSION)
        assert page is not None
        self.assertTrue(page["live"])
        self.assertEqual(self.step("setup")["state"], "running")
        startup.host_report("host_1", {"steps": [{"step": "setup", "child": "roadpass", "state": "done"}]})
        page = startup.log_page(SESSION)
        assert page is not None
        self.assertFalse(page["live"])


class LogTests(Fixture):
    def test_pages_continue_after_the_last_line(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        for i in range(5):
            startup.log(SESSION, "host", f"line {i}")
        first = startup.log_page(SESSION, 0, limit=3)
        assert first is not None
        rest = startup.log_page(SESSION, first["next"])
        assert rest is not None
        self.assertEqual(
            [l["text"] for l in first["lines"] + rest["lines"]][1:],
            [f"line {i}" for i in range(5)],
        )
        self.assertEqual(rest["next"], 6)

    def test_old_lines_are_dropped_past_the_limit(self) -> None:
        startup.on_stage(SESSION, "provisioning")
        startup.log(SESSION, "host", "\n".join(f"l{i}" for i in range(startup.MAX_LOG_LINES + 10)))
        page = startup.log_page(SESSION, 0, limit=10_000)
        assert page is not None
        self.assertEqual(len(page["lines"]), startup.MAX_LOG_LINES)
        self.assertEqual(page["lines"][-1]["text"], f"l{startup.MAX_LOG_LINES + 9}")


class RunnerExitTests(Fixture):
    def test_a_wait_ends_when_the_runner_exits(self) -> None:
        async def scenario() -> str:
            waiter = asyncio.ensure_future(startup.runner_exit("runner_1"))
            await asyncio.sleep(0)
            startup.runner_exited("runner_1", "runner process exited with code 1")
            return await asyncio.wait_for(waiter, 1)

        self.assertEqual(asyncio.run(scenario()), "runner process exited with code 1")

    def test_an_exit_before_the_wait_is_kept_for_it(self) -> None:
        startup.runner_exited("runner_2", "gone")
        self.assertEqual(asyncio.run(startup.runner_exit("runner_2")), "gone")

    def test_the_pod_budget_only_grows(self) -> None:
        startup.note_pod_budget(300)
        startup.note_pod_budget(90)
        self.assertGreaterEqual(startup.pod_budget_s(), 300)


if __name__ == "__main__":
    unittest.main()
