"""Phase 3.6 runtime support, all offline: runtime env, pre-flight, smoke commands, errors, timeouts and logs."""
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import provider_check, runtime_env, smoke_test, text_worker
from app.logs import REDACTED, StructuredFormatter, configure_logging, log_event, payload_summary
from app.models import AITool, Base, CreditAccount, Project, User, Workflow, WorkflowRun, Workspace
from app.provider_check import smoke_video_settings
from app.providers.catalog import VIDEO_PROVIDERS
from app.providers.errors import CATEGORIES, ProviderError, error_category, status_error
from app.providers.text import TEXT_PROVIDERS, TextProviderError, TextResult, TextUsage
from app.providers.text.base import DEFAULT_TIMEOUT
from app.video_files import DOWNLOAD_TIMEOUT, mp4_duration_seconds, valid_mp4
from app.workflow import ExecutionContext, WorkflowExecutor

ROOT = Path(__file__).resolve().parents[1]
SECRET = "sk-live-SENTINEL-0123456789abcdef"
ALL_KEYS = [spec.key_env for spec in TEXT_PROVIDERS.values()] + [spec.key_env for spec in VIDEO_PROVIDERS.values()]
NO_KEYS = {name: "" for name in ALL_KEYS + ["REELFORGE_SMOKE_TEXT_PROVIDER", "REELFORGE_SMOKE_TEXT_MODEL",
                                           "REELFORGE_SMOKE_VIDEO_PROVIDER", "REELFORGE_SMOKE_VIDEO_MODEL",
                                           "REELFORGE_LIVE_TESTS", "REELFORGE_ENV_FILE", "RUNWAY_OUTPUT_HOSTS"]}


def mp4(seconds=4, timescale=1000):
    """A minimal MP4 with an mvhd box that says how long it is."""
    body = (b"\x00\x00\x00\x00" + (0).to_bytes(4, "big") * 2 + timescale.to_bytes(4, "big")
            + (seconds * timescale).to_bytes(4, "big") + b"\x00" * 80)
    mvhd = (8 + len(body)).to_bytes(4, "big") + b"mvhd" + body
    return (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00\x00\x00\x10mdat12345678"
            + (8 + len(mvhd)).to_bytes(4, "big") + b"moov" + mvhd)


class Lines:
    def __init__(self):
        self.lines = []

    def __call__(self, line=""):
        self.lines.append(str(line))

    @property
    def text(self):
        return "\n".join(self.lines)


class RuntimeEnvTest(unittest.TestCase):
    def test_file_values_load_without_overriding_the_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            path.write_text("# comment\n\nOPENAI_API_KEY='from-file-123'\nFAL_KEY=\nRUNWARE_API_KEY=\"file-runware\"\n"
                            "VIDEO_CREDITS_PER_CLIP=12\n", encoding="utf-8")
            self.assertEqual(runtime_env.parse_env_file(path)["OPENAI_API_KEY"], "from-file-123")
            with patch.dict(os.environ, {**NO_KEYS, "RUNWARE_API_KEY": "from-shell"}):
                used, loaded = runtime_env.load_runtime_env(path)
                self.assertEqual(used, path)
                self.assertEqual(sorted(loaded), ["OPENAI_API_KEY", "VIDEO_CREDITS_PER_CLIP"])
                self.assertEqual(os.environ["OPENAI_API_KEY"], "from-file-123")
                self.assertEqual(os.environ["RUNWARE_API_KEY"], "from-shell")
                self.assertEqual(os.environ["FAL_KEY"], "")

    def test_bad_lines_and_missing_files_are_reported_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            path.write_text("OPENAI_API_KEY sk-secret-value\n", encoding="utf-8")
            with self.assertRaises(runtime_env.RuntimeEnvError) as caught:
                runtime_env.parse_env_file(path)
            self.assertNotIn("sk-secret-value", str(caught.exception))
            with patch.dict(os.environ, {"REELFORGE_ENV_FILE": str(Path(directory) / "missing.env")}):
                with self.assertRaises(runtime_env.RuntimeEnvError):
                    runtime_env.env_file_path()

    def test_example_file_lists_names_only(self):
        values = runtime_env.parse_env_file(ROOT / ".env.runtime.example")
        self.assertTrue(set(runtime_env.PROVIDER_KEYS) <= set(values))
        self.assertTrue({"RUNWAY_OUTPUT_HOSTS", "REELFORGE_TOKEN_ENCRYPTION_KEY"} <= set(values))
        self.assertEqual({name: value for name, value in values.items() if value}, {})
        self.assertNotIn("REELFORGE_LIVE_TESTS", values)

    def test_fingerprints_identify_keys_without_revealing_them(self):
        fingerprint = runtime_env.key_fingerprint(SECRET)
        self.assertEqual(len(fingerprint), 8)
        self.assertNotIn(fingerprint, SECRET)
        self.assertEqual(fingerprint, runtime_env.key_fingerprint(f" {SECRET}\n"))


class ProviderCheckTest(unittest.TestCase):
    def run_check(self, env, **kwargs):
        out = Lines()
        with patch.dict(os.environ, {**NO_KEYS, **env}):
            checks = provider_check.run_checks(**kwargs)
            ready = provider_check.report(checks, None, out=out)
        return ready, checks, out.text

    def test_missing_keys_are_reported(self):
        ready, checks, text = self.run_check({}, text="openai", video="runware")
        self.assertFalse(ready)
        self.assertIn("API key: OPENAI_API_KEY missing", text)
        self.assertIn("Problem: RUNWARE_API_KEY is missing", text)
        self.assertIn("Not ready for smoke test.", text)
        ready, _, text = self.run_check({})
        self.assertFalse(ready)
        self.assertIn("no text provider selected", text)

    def test_configured_keys_are_recognized_and_never_printed(self):
        env = {"OPENAI_API_KEY": SECRET, "RUNWARE_API_KEY": SECRET + "-video"}
        ready, checks, text = self.run_check(env)
        self.assertTrue(ready, text)
        self.assertEqual([(c.provider, c.model, c.model_state, c.key_state) for c in checks],
                         [("openai", "gpt-4.1-mini", "recognized", "configured"),
                          ("runware", "bytedance:seedance@2.5", "recognized", "configured")])
        self.assertIn(f"fingerprint {runtime_env.key_fingerprint(SECRET)}", text)
        self.assertIn("Smoke request: 4s, 480p, 16:9, no audio", text)
        self.assertIn("Ready for smoke test.", text)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECRET[:12], text)

    def test_selection_by_environment_and_unsupported_choices(self):
        ready, checks, _ = self.run_check({"GEMINI_API_KEY": SECRET, "REELFORGE_SMOKE_TEXT_PROVIDER": "gemini",
                                           "REELFORGE_SMOKE_TEXT_MODEL": "gemini-exp-1"}, only="text")
        self.assertTrue(ready)
        self.assertEqual((checks[0].provider, checks[0].model, checks[0].model_state),
                         ("gemini", "gemini-exp-1", "accepted"))
        ready, checks, text = self.run_check({"GEMINI_API_KEY": SECRET}, text="gemini", text_model="bad model!",
                                             only="text")
        self.assertFalse(ready)
        self.assertEqual(checks[0].model_state, "unsupported")
        ready, _, text = self.run_check({"FAL_KEY": SECRET}, video="fal", video_model="fal-ai/other", only="video")
        self.assertIn("is not supported by the fal adapter", text)
        ready, _, text = self.run_check({}, video="pika", only="video")
        self.assertIn("unsupported video provider 'pika'", text)
        ready, _, text = self.run_check({"RUNWAYML_API_SECRET": SECRET}, video="runway", only="video")
        self.assertIn("RUNWAY_OUTPUT_HOSTS", text)
        ready, _, text = self.run_check({"OPENAI_API_KEY": "has space"}, text="openai", only="text")
        self.assertIn("OPENAI_API_KEY is invalid", text)

    def test_smoke_requests_use_the_cheapest_settings(self):
        self.assertEqual(smoke_video_settings("fal", "fal-ai/veo3.1/fast"),
                         {"duration": "4s", "resolution": "720p", "generate_audio": False, "aspect_ratio": "16:9"})
        self.assertEqual(smoke_video_settings("runway", "gen4.5")["duration"], "2s")
        self.assertEqual(smoke_video_settings("replicate", "google/veo-3.1-fast")["resolution"], "720p")
        self.assertEqual(smoke_video_settings("dola", "seedance-2.5")["generate_audio"], None)
        for name, spec in VIDEO_PROVIDERS.items():
            for model in spec.module.VIDEO_MODELS:
                with self.subTest(provider=name):
                    request = spec.module.VideoRequest(model_id=model, prompt=smoke_test.VIDEO_PROMPT,
                                                       **smoke_video_settings(name, model))
                    spec.module.validate_video_request(request)

    def test_command_exit_status(self):
        with patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": SECRET}), \
                patch.object(provider_check, "load_runtime_env", return_value=(None, [])), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(provider_check.main(["--only", "text"]), 0)
            self.assertEqual(provider_check.main(["--only", "video"]), 1)
        self.assertNotIn(SECRET, stdout.getvalue())


class FakeTextProvider:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls, self.closed = reply, error, [], False

    def __call__(self, name):
        self.name = name
        return self

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return TextResult(text=self.reply, usage=TextUsage.of(12, 20), provider=self.name, model=kwargs["model"],
                          raw_metadata={"finish_reason": "stop"})

    def close(self):
        self.closed = True


class FakeVideoClient:
    """Stands in for any video adapter client; states are returned in order."""

    def __init__(self, states, *, submit_error=None):
        self.states = list(states)
        self.submit_error = submit_error
        self.requests = []

    def __call__(self, key):
        self.key = key
        return self

    def submit(self, request):
        self.requests.append(request)
        if self.submit_error:
            raise self.submit_error
        return type("Submission", (), {"request_id": "job-123"})()

    def status(self, submission):
        state = self.states.pop(0)
        return type("State", (), {"state": state, "error": None})()

    def result(self, submission):
        return type("Result", (), {"video_url": "https://media.example/clip.mp4"})()

    def close(self):
        pass


class SmokeCommandTest(unittest.TestCase):
    def test_live_commands_refuse_without_explicit_intent(self):
        with patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": SECRET}), \
                patch.object(smoke_test, "smoke_text", side_effect=AssertionError("must not run")), \
                patch.object(smoke_test, "smoke_video", side_effect=AssertionError("must not run")), \
                patch.object(smoke_test, "load_runtime_env", side_effect=AssertionError("must not load")), \
                patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(smoke_test.main(["text"]), 3)
            self.assertEqual(smoke_test.main(["video", "--provider", "fal"]), 3)
        self.assertIn("Refusing to call a paid provider API", stdout.getvalue())
        self.assertFalse(smoke_test.live_intent(False))
        with patch.dict(os.environ, {"REELFORGE_LIVE_TESTS": "1"}):
            self.assertTrue(smoke_test.live_intent(False))
        self.assertTrue(smoke_test.live_intent(True))

    def test_text_smoke_reports_usage_and_a_short_preview(self):
        fake = FakeTextProvider("Posting on a schedule " * 20)
        out = Lines()
        with patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": SECRET}):
            self.assertEqual(smoke_test.smoke_text(provider_factory=fake, out=out), 0)
        call = fake.calls[0]
        self.assertEqual((call["model"], call["prompt"], call["max_tokens"]),
                         ("gpt-4.1-mini", smoke_test.TEXT_PROMPT, 256))
        self.assertTrue(fake.closed)
        self.assertIn("Tokens: input 12, output 20, total 32", out.text)
        preview = next(line for line in out.lines if line.startswith("Preview: "))
        self.assertLessEqual(len(preview), len("Preview: ") + smoke_test.PREVIEW_CHARS)
        self.assertIn("PASSED", out.text)
        self.assertNotIn(SECRET, out.text)

    def test_text_smoke_reports_the_error_category(self):
        out = Lines()
        error = TextProviderError("authentication_error", "openai returned HTTP 401", http_status=401)
        with patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": SECRET}):
            self.assertEqual(smoke_test.smoke_text(provider_factory=FakeTextProvider(error=error), out=out), 1)
        self.assertIn("FAILED: authentication_error (code authentication_error, retryable False, HTTP 401)", out.text)
        self.assertIn("Hint: Check the API key", out.text)
        with patch.dict(os.environ, NO_KEYS):
            self.assertEqual(smoke_test.smoke_text(provider="openai", provider_factory=FakeTextProvider("x"),
                                                   out=Lines()), 2)

    def test_video_smoke_polls_downloads_and_validates(self):
        client = FakeVideoClient(["queued", "running", "completed"])

        def download(url, target):
            target.write_bytes(mp4(seconds=4))
            return target.stat().st_size

        out = Lines()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {**NO_KEYS, "FAL_KEY": SECRET}):
            code = smoke_test.smoke_video(provider="fal", output_dir=Path(directory), client_factory=client,
                                          download=download, sleep=lambda seconds: None, out=out)
            saved = list(Path(directory).glob("fal-*.mp4"))
        self.assertEqual(code, 0, out.text)
        self.assertEqual(len(saved), 1)
        request = client.requests[0]
        self.assertEqual((request.duration, request.resolution, request.generate_audio, request.aspect_ratio),
                         ("4s", "720p", False, "16:9"))
        self.assertEqual(client.key, SECRET)
        for expected in ("Job ID: job-123", "Duration: 4.0 s", "File size: ", "State: running", "PASSED"):
            self.assertIn(expected, out.text)
        self.assertNotIn(SECRET, out.text)

    def test_video_smoke_failures_are_reported(self):
        out = Lines()
        with patch.dict(os.environ, {**NO_KEYS, "FAL_KEY": SECRET}):
            code = smoke_test.smoke_video(provider="fal", client_factory=FakeVideoClient(["failed"]),
                                          sleep=lambda s: None, out=out)
        self.assertEqual(code, 1)
        self.assertIn("FAILED: the provider reported unknown", out.text)
        out = Lines()
        with patch.dict(os.environ, {**NO_KEYS, "FAL_KEY": SECRET}):
            code = smoke_test.smoke_video(provider="fal", client_factory=FakeVideoClient(["running"] * 3),
                                          timeout_seconds=-1, sleep=lambda s: None, out=out)
        self.assertEqual(code, 1)
        self.assertIn("was not cancelled", out.text)
        out = Lines()
        unknown = ProviderError("submission_unknown", "fal submission timed out", category="timeout")
        with patch.dict(os.environ, {**NO_KEYS, "FAL_KEY": SECRET}):
            code = smoke_test.smoke_video(provider="fal", client_factory=FakeVideoClient([], submit_error=unknown),
                                          out=out)
        self.assertEqual(code, 1)
        self.assertIn("FAILED: timeout (code submission_unknown", out.text)
        self.assertIn("may have accepted the job anyway", out.text)

    def test_mp4_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.write_bytes(mp4(seconds=6, timescale=600))
            self.assertTrue(valid_mp4(path))
            self.assertEqual(mp4_duration_seconds(path), 6.0)
            path.write_bytes(mp4()[:40])
            self.assertFalse(valid_mp4(path))
            self.assertIsNone(mp4_duration_seconds(path))


class ErrorNormalizationTest(unittest.TestCase):
    def test_status_mapping_and_categories(self):
        self.assertEqual([status_error(code) for code in (400, 401, 402, 404, 408, 429, 503, 302)],
                         [("invalid_request", False), ("authentication_error", False), ("billing_error", False),
                          ("not_found", False), ("timeout", True), ("rate_limited", True),
                          ("provider_unavailable", True), ("invalid_response", False)])
        cases = {"not_found": "invalid_request", "unsafe_url": "invalid_response", "missing_key": "configuration_error",
                 "submission_unknown": "network_error", "content_policy_violation": "content_rejected",
                 "timeoutProvider": "timeout", "providerRateLimitExceeded": "rate_limited",
                 "runner_disconnected": "generation_failed", "empty_output": "empty_output"}
        for code, category in cases.items():
            with self.subTest(code=code):
                self.assertEqual(error_category(code), category)
        self.assertTrue(all(error_category(code) in CATEGORIES for code in cases))

    def clients(self, handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        for name, spec in VIDEO_PROVIDERS.items():
            model = next(iter(spec.module.VIDEO_MODELS))
            kwargs = {"base_url": "https://gateway.example"} if name == "dola" else {}
            request = spec.module.VideoRequest(model_id=model, prompt="A calm lake",
                                               **smoke_video_settings(name, model))
            yield name, spec.client_type("test-key", http_client=http, **kwargs), request

    def test_video_submit_errors_share_categories(self):
        expectations = {401: ("authentication_error", "authentication_error"), 402: ("billing_error", "billing_error"),
                        429: ("rate_limited", "rate_limited"), 400: ("invalid_request", "invalid_request")}
        for status, (code, category) in expectations.items():
            for name, client, request in self.clients(lambda r, s=status: httpx.Response(s, json={})):
                with self.subTest(provider=name, status=status), self.assertRaises(ProviderError) as caught:
                    client.submit(request)
                self.assertEqual((caught.exception.code, caught.exception.category), (code, category))
                self.assertEqual(caught.exception.http_status, status)
        for name, client, request in self.clients(lambda r: httpx.Response(503, json={})):
            with self.subTest(provider=name, status=503), self.assertRaises(ProviderError) as caught:
                client.submit(request)
            # A 5xx on submit may hide an accepted job: never retried, never refunded.
            self.assertEqual(caught.exception.category, "provider_unavailable")
            self.assertFalse(caught.exception.retryable)

    def test_video_transport_failures_on_submit_keep_an_unknown_outcome(self):
        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)

        def down(request):
            raise httpx.ConnectError("down", request=request)

        for handler, category in ((slow, "timeout"), (down, "network_error")):
            for name, client, request in self.clients(handler):
                with self.subTest(provider=name, category=category), self.assertRaises(ProviderError) as caught:
                    client.submit(request)
                self.assertEqual((caught.exception.code, caught.exception.category), ("submission_unknown", category))
                self.assertNotIn("test-key", str(caught.exception))

    def test_provider_detail_is_kept_for_logs_but_not_in_the_message(self):
        from app.providers.text.openai import OpenAITextProvider

        def rejected(request):
            return httpx.Response(404, json={"error": {"message": "The model `gpt-nope` does not exist", "code": "x"}})

        provider = OpenAITextProvider("test-key", http_client=httpx.Client(transport=httpx.MockTransport(rejected)))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="gpt-nope", prompt="x")
        error = caught.exception
        self.assertEqual((error.code, error.category), ("not_found", "invalid_request"))
        self.assertEqual(error.provider_detail, "The model `gpt-nope` does not exist")
        self.assertNotIn("does not exist", str(error))
        self.assertEqual(error.describe()["provider_detail"], "The model `gpt-nope` does not exist")
        long_text = httpx.Response(400, text="x " * 500)
        for name, client, request in self.clients(lambda r: long_text):
            with self.subTest(provider=name), self.assertRaises(ProviderError) as caught:
                client.submit(request)
            self.assertLessEqual(len(caught.exception.provider_detail), 200)
            self.assertNotIn("x x", str(caught.exception))

    def test_text_errors_carry_categories(self):
        from app.providers.text.openai import OpenAITextProvider

        def blocked(request):
            return httpx.Response(200, json={"choices": [{"message": {"content": None, "refusal": "no"},
                                                          "finish_reason": "stop"}]})

        provider = OpenAITextProvider("test-key", http_client=httpx.Client(transport=httpx.MockTransport(blocked)))
        with self.assertRaises(TextProviderError) as caught:
            provider.generate(model="gpt-4.1-mini", prompt="x")
        self.assertEqual((caught.exception.code, caught.exception.category), ("content_rejected", "content_rejected"))
        self.assertIsInstance(caught.exception, ProviderError)


class TimeoutTest(unittest.TestCase):
    def test_text_providers_use_explicit_timeouts(self):
        self.assertEqual((DEFAULT_TIMEOUT.connect, DEFAULT_TIMEOUT.read), (10.0, 120.0))
        for name, spec in TEXT_PROVIDERS.items():
            with self.subTest(provider=name):
                provider = spec.provider_type("test-key")
                self.assertEqual(provider.http_client.timeout, DEFAULT_TIMEOUT)
                provider.close()

    def test_video_requests_carry_an_explicit_timeout(self):
        seen = []

        def record(request):
            seen.append(request.extensions.get("timeout"))
            return httpx.Response(500, json={})

        for name, client, request in ErrorNormalizationTest.clients(None, record):
            with self.subTest(provider=name):
                self.assertEqual(client.timeout_seconds, 10.0)
                with self.assertRaises(ProviderError):
                    client.submit(request)
        self.assertEqual(len(seen), len(VIDEO_PROVIDERS))
        self.assertTrue(all(timeout and timeout["read"] == 10.0 and timeout["connect"] == 10.0 for timeout in seen))
        self.assertEqual((DOWNLOAD_TIMEOUT.connect, DOWNLOAD_TIMEOUT.read), (30.0, 120.0))


class LoggingTest(unittest.TestCase):
    def formatted(self, record_fields, message="event"):
        logger = logging.getLogger("app.test.logs")
        record = logger.makeRecord(logger.name, logging.INFO, __file__, 1, message, (), None,
                                   extra={"event": message, "fields": record_fields})
        return StructuredFormatter().format(record)

    def test_secrets_are_redacted_by_field_name_and_by_value(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}):
            line = self.formatted({"api_key": "abc", "authorization": "Bearer x", "max_tokens": 64,
                                   "note": f"leaked {SECRET} here"})
        entry = json.loads(line)
        self.assertEqual((entry["api_key"], entry["authorization"], entry["max_tokens"]), (REDACTED, REDACTED, 64))
        self.assertEqual(entry["note"], f"leaked {REDACTED} here")
        self.assertNotIn(SECRET, line)

    def test_payload_summary_keeps_prompts_out(self):
        summary = payload_summary({"provider": "openai", "model": "gpt", "prompt": "private brief", "language": "vi",
                                   "system_prompt": "sys", "credits": 1})
        self.assertEqual(summary, {"provider": "openai", "model": "gpt", "language": "vi", "credits": 1,
                                   "prompt_chars": 13, "system_prompt_chars": 3})

    def test_configure_logging_is_idempotent(self):
        logger = logging.getLogger("app")
        before = list(logger.handlers)
        stream = io.StringIO()
        try:
            configure_logging(stream)
            configure_logging(stream)
            self.assertEqual(len(logger.handlers), len(before) + 1)
            log_event(logging.getLogger("app.test"), "job_claimed", job_id="j1")
            self.assertEqual(json.loads(stream.getvalue())["event"], "job_claimed")
        finally:
            for handler in logger.handlers[len(before):]:
                logger.removeHandler(handler)
            logger.propagate = True


@patch.dict(os.environ, {**NO_KEYS, "OPENAI_API_KEY": SECRET, "TEXT_CREDITS_PER_GENERATION": "1"})
class WorkerLoggingTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(directory.name) / 'logs.db'}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        with self.Session.begin() as db:
            db.add(User(id="user-1", email="logs@example.com", password_hash="hash"))
            db.flush()
            db.add(Workspace(id="space-1", name="Logs", owner_id="user-1"))
            db.flush()
            db.add(Project(id="project-1", workspace_id="space-1", title="Rừng", topic="Bí mật riêng của khách hàng"))
            db.add(Workflow(id="workflow-1", workspace_id="space-1", name="Flow"))
            db.add(CreditAccount(workspace_id="space-1", balance=5))
            db.add(AITool(id="tool-1", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1-mini"))
            db.add(AITool(id="tool-2", workspace_id="space-1", task="script", provider="openai", model="gpt-4.1"))

    def start(self, graph):
        with self.Session.begin() as db:
            run = WorkflowRun(id=str(uuid4()), workspace_id="space-1", workflow_id="workflow-1", project_id="project-1",
                              graph_snapshot=json.dumps(graph), status="running", created_at=datetime.now(timezone.utc))
            db.add(run)
            db.flush()
            WorkflowExecutor().start_run(ExecutionContext(db, workspace=db.get(Workspace, "space-1"),
                                                          project=db.get(Project, "project-1"), run=run, graph=graph))
            return run.id

    def events(self, logs):
        return [record.event for record in logs.records if hasattr(record, "event")]

    def test_run_and_worker_events_carry_ids_but_no_secrets_or_prompts(self):
        graph = {"nodes": [{"id": "writer", "type": "ai_writer", "x": 0, "y": 0,
                            "config": {"tool_id": "tool-2", "language": "en", "tone": "cinematic",
                                       "platform": "youtube", "duration": 60, "max_tokens": 900,
                                       "instructions": "Giữ bí mật này"}}], "edges": []}
        with self.assertLogs("app", "INFO") as logs:
            run_id = self.start(graph)
            fake = FakeTextProvider("A short script.")
            self.assertTrue(text_worker.run_one(provider_factory=fake, session_factory=self.Session))
        events = self.events(logs)
        for expected in ("workflow_step_started", "workflow_step_queued", "credit_reserved", "workflow_run_started",
                         "job_claimed", "provider_request_started", "provider_request_completed", "job_completed",
                         "workflow_step_completed", "workflow_run_status_changed"):
            self.assertIn(expected, events)
        queued = next(r for r in logs.records if getattr(r, "event", None) == "workflow_step_queued").fields
        self.assertEqual((queued["run_id"], queued["workflow_id"], queued["workspace_id"]),
                         (run_id, "workflow-1", "space-1"))
        # The settings in the snapshot and the request built from them, side by side.
        self.assertEqual({key: queued["job"][key] for key in ("provider", "model", "tool_id", "language", "max_tokens")},
                         {"provider": "openai", "model": "gpt-4.1", "tool_id": "tool-2", "language": "en",
                          "max_tokens": 900})
        self.assertEqual(queued["settings"], {"tool_id": "tool-2", "language": "en", "tone": "cinematic",
                                              "platform": "youtube", "duration": 60, "max_tokens": 900,
                                              "instructions_chars": 14})
        # ...and what actually reached the provider.
        call = fake.calls[0]
        self.assertEqual((call["model"], call["max_tokens"]), ("gpt-4.1", 900))
        for phrase in ("in English", "Tone: cinematic.", "YouTube (long-form)", "about 60 seconds", "Giữ bí mật này"):
            self.assertIn(phrase, call["prompt"])
        # Every line exactly as the configured handler would write it.
        written = "\n".join(StructuredFormatter().format(record) for record in logs.records)
        self.assertNotIn(SECRET, written)
        self.assertNotIn("Giữ bí mật này", written)
        self.assertNotIn("Bí mật riêng của khách hàng", written)
        self.assertIn('"prompt_chars"', written)

    def test_provider_failures_and_refunds_are_logged(self):
        self.start({"nodes": [{"id": "writer", "type": "ai_writer", "x": 0, "y": 0}], "edges": []})
        error = TextProviderError("authentication_error", "openai returned HTTP 401", http_status=401)
        with self.assertLogs("app", "INFO") as logs:
            text_worker.run_one(provider_factory=FakeTextProvider(error=error), session_factory=self.Session)
        failed = next(r for r in logs.records if getattr(r, "event", None) == "provider_request_failed").fields
        self.assertEqual(failed["error"], {"code": "authentication_error", "category": "authentication_error",
                                           "retryable": False, "http_status": 401})
        self.assertIn("credit_refunded", self.events(logs))
        self.assertIn("workflow_step_failed", self.events(logs))

    def test_provider_detail_reaches_logs_scrubbed_but_never_the_step(self):
        run_id = self.start({"nodes": [{"id": "writer", "type": "ai_writer", "x": 0, "y": 0}], "edges": []})
        error = TextProviderError("invalid_request", "openai returned HTTP 400", http_status=400,
                                  provider_detail=f"Invalid key {SECRET} for model")
        with self.assertLogs("app", "INFO") as logs:
            text_worker.run_one(provider_factory=FakeTextProvider(error=error), session_factory=self.Session)
        written = "\n".join(StructuredFormatter().format(record) for record in logs.records)
        self.assertIn(f"Invalid key {REDACTED} for model", written)
        self.assertNotIn(SECRET, written)
        with self.Session() as db:
            from app.models import WorkflowRunStep
            step = db.query(WorkflowRunStep).filter_by(run_id=run_id).one()
            self.assertNotIn("Invalid key", step.output)
            self.assertEqual(json.loads(step.output)["error"],
                             {"code": "invalid_request", "retryable": False, "category": "invalid_request"})

    def test_a_rejected_start_logs_only_the_rejection(self):
        with self.Session.begin() as db:
            db.get(CreditAccount, "space-1").balance = 0
        with self.assertLogs("app", "INFO") as logs, self.assertRaises(Exception):
            self.start({"nodes": [{"id": "writer", "type": "ai_writer", "x": 0, "y": 0}], "edges": []})
        self.assertEqual(self.events(logs), ["workflow_run_rejected"])


if __name__ == "__main__":
    unittest.main()
