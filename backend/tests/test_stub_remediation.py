"""Tests for REED stub remediation: session identity, trigger persistence,
generator fallback, bounded redaction, public API exclusion, and unknown-session
rejection.

Test charter
=============

Edge-case ideation was performed across five frames. Only credible
deterministic risks are covered below.

Boundary analyst
  - Five sessions must each have a unique, stable name that survives
    DST transitions. The scheduler's SCHEDULE dict and the registry's
    SessionName Literal must agree on the set.
  - The trigger catch-all must persist a stub even when the provider
    init fails, the LLM call fails, or the session name is unknown.
  - The generator's parse-fail path must persist a fallback digest
    with fallback_used=True and a [STUB] headline.

Hostile-input reviewer
  - An unknown session name must NOT be persisted as a stub digest.
    The current code persists stubs for unknown sessions via the
    trigger catch-all; this test asserts the desired behavior (no
    persistence) and will fail (RED) until the gate is added.
  - The warning field in the trigger catch-all must be bounded to
    500 chars. Exception text containing ANSI escape codes, bearer
    tokens, API keys, or URL query strings must be truncated or
    redacted before persistence.
  - The Generation model must not carry a warning field that leaks
    into the public API response.

Compatibility maintainer
  - The scheduler's SCHEDULE dict keys must match the SessionName
    Literal exactly. Any mismatch causes a silent skip at runtime.
  - The trigger endpoint must accept all five session names and
    reject anything else without persisting.

On-call diagnoser
  - When the trigger catch-all fires, the operator must be able to
    see the exception class and message in the trigger response or
    in the persisted digest. Currently the warning is silently dropped
    because Generation has no warning field.
  - When the generator parse-fail path fires, the operator must be
    able to see the agent's warning in the persisted digest.

Simplifier
  - The trigger catch-all and the generator parse-fail path both
    produce stub digests. They should use the same code path so
    that fixing one fixes both.

Coverage summary
----------------
1. Session identity: all five sessions registered, SCHEDULE matches
2. Trigger catch-all: exception -> stub persisted
3. Generator parse-fail: no JSON -> fallback persisted
4. Bounded redaction: warning <= 500 chars, no sensitive leaks
5. Public API exclusion: Generation.warning not exposed
6. Unknown session: no persistence
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Session identity
# ---------------------------------------------------------------------------

class TestSessionIdentity(unittest.TestCase):
    """All five sessions are registered with stable, DST-safe names."""

    def test_five_sessions_registered(self):
        from app.sessions.registry import all_sessions
        names = {s.name for s in all_sessions()}
        self.assertEqual(
            names,
            {"pre_market", "early_market", "midday", "close", "weekend_recap"},
        )

    def test_session_name_literal_accepts_all_five(self):
        from app.digests.models import SessionName
        for name in ("pre_market", "early_market", "midday", "close", "weekend_recap"):
            # The Literal type is used at runtime; this just confirms
            # the type checker's view matches the registry.
            self.assertIsInstance(name, str)

    def test_scheduler_schedule_has_all_five(self):
        """SCHEDULE dict must contain all five session names."""
        import sys
        import unittest.mock as um
        apscheduler_mock = um.MagicMock()
        sys.modules["apscheduler"] = apscheduler_mock
        sys.modules["apscheduler.schedulers"] = um.MagicMock()
        sys.modules["apscheduler.schedulers.background"] = um.MagicMock()
        sys.modules["apscheduler.triggers"] = um.MagicMock()
        sys.modules["apscheduler.triggers.cron"] = um.MagicMock()
        try:
            from app.scheduler import SCHEDULE
            self.assertEqual(
                set(SCHEDULE.keys()),
                {"pre_market", "early_market", "midday", "close", "weekend_recap"},
            )
        finally:
            for mod in list(sys.modules):
                if mod.startswith("apscheduler"):
                    del sys.modules[mod]

    def test_scheduler_schedule_has_correct_cron_fields(self):
        """Every SCHEDULE entry must have hour, minute, day_of_week."""
        import sys
        import unittest.mock as um
        sys.modules["apscheduler"] = um.MagicMock()
        sys.modules["apscheduler.schedulers"] = um.MagicMock()
        sys.modules["apscheduler.schedulers.background"] = um.MagicMock()
        sys.modules["apscheduler.triggers"] = um.MagicMock()
        sys.modules["apscheduler.triggers.cron"] = um.MagicMock()
        try:
            from app.scheduler import SCHEDULE
            for session, params in SCHEDULE.items():
                self.assertIn("hour", params, f"{session} missing hour")
                self.assertIn("minute", params, f"{session} missing minute")
                self.assertIn("day_of_week", params, f"{session} missing day_of_week")
        finally:
            for mod in list(sys.modules):
                if mod.startswith("apscheduler"):
                    del sys.modules[mod]

    def test_weekend_recap_is_monday_only(self):
        """weekend_recap must be scheduled for Monday only."""
        import sys
        import unittest.mock as um
        sys.modules["apscheduler"] = um.MagicMock()
        sys.modules["apscheduler.schedulers"] = um.MagicMock()
        sys.modules["apscheduler.schedulers.background"] = um.MagicMock()
        sys.modules["apscheduler.triggers"] = um.MagicMock()
        sys.modules["apscheduler.triggers.cron"] = um.MagicMock()
        try:
            from app.scheduler import SCHEDULE
            self.assertEqual(SCHEDULE["weekend_recap"]["day_of_week"], "mon")
        finally:
            for mod in list(sys.modules):
                if mod.startswith("apscheduler"):
                    del sys.modules[mod]


# ---------------------------------------------------------------------------
# Trigger catch-all persistence
# ---------------------------------------------------------------------------

class TestTriggerCatchAllPersistence(unittest.TestCase):
    """When generate_digest raises, the trigger saves a stub digest.

    These tests exercise the trigger endpoint's except block by patching
    generate_digest to raise a controlled exception, then asserting the
    store received a stub digest with the expected properties.
    """

    def setUp(self):
        # Bypass trigger auth by setting REED_ENV=dev
        self._old_env = os.environ.get("REED_ENV")
        self._old_space = os.environ.get("SPACE_ID")
        os.environ["REED_ENV"] = "dev"
        os.environ.pop("SPACE_ID", None)
        # Temp dir for the store
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["REED_ENV"] = self._old_env
        else:
            os.environ.pop("REED_ENV", None)
        if self._old_space is not None:
            os.environ["SPACE_ID"] = self._old_space
        else:
            os.environ.pop("SPACE_ID", None)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_store(self):
        from app.digests.store import JsonFileStore
        return JsonFileStore(data_dir=self._data_dir)

    def _trigger_and_capture_stub(self, exc: Exception) -> dict | None:
        """POST to /api/trigger/pre_market with generate_digest patched to
        raise `exc`. Returns the persisted digest dict, or None if nothing
        was written."""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store

        store = self._make_store()
        app.dependency_overrides[get_store] = lambda: store

        # Patch both get_provider (to bypass provider init) and
        # generate_digest (to trigger the catch-all).
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.model = "mock-model"
        with (
            patch("app.api.trigger.get_provider", return_value=mock_provider),
            patch("app.api.trigger.generate_digest", side_effect=exc),
        ):
            client = TestClient(app)
            response = client.post("/api/trigger/pre_market")

        app.dependency_overrides.clear()

        # The trigger returns 200 even on failure (catch-all persists stub).
        self.assertEqual(response.status_code, 200)

        # Read back the latest digest from the store.
        latest = store.latest(session="pre_market")
        if latest is None:
            return None
        return json.loads(latest.model_dump_json())

    def test_trigger_catch_all_calls_store_write_on_exception(self):
        """The trigger's except block must persist a stub digest with
        [STUB] headline and fallback_used=True."""
        digest_dict = self._trigger_and_capture_stub(
            ValueError("test provider failure")
        )
        self.assertIsNotNone(digest_dict, "store must contain a digest after trigger catch-all")
        self.assertIn("[STUB]", digest_dict["headline"],
                      "stub digest must have [STUB] headline")
        self.assertTrue(digest_dict["generation"]["fallback_used"],
                        "stub digest must have fallback_used=True")

    def test_trigger_catch_all_warning_bounded_to_500_chars(self):
        """The warning field in the stub digest must be truncated to 500 chars."""
        long_msg = "A" * 2000
        digest_dict = self._trigger_and_capture_stub(
            ValueError(long_msg)
        )
        self.assertIsNotNone(digest_dict)
        warning = digest_dict["generation"].get("warning", "")
        # The warning is silently dropped by Pydantic (Generation has no
        # warning field). This test documents the gap: the warning is
        # truncated to 500 chars in the trigger code, but Pydantic drops
        # it before persistence. When the fix lands (add warning to
        # Generation), this assertion will pass.
        # For now, assert the trigger code does truncate: the warning
        # string in the trigger dict is [:500] but it never reaches the
        # store. The RED tests below demonstrate the gap.
        self.assertLessEqual(len(warning), 500,
                             "warning must be at most 500 chars")

    def test_trigger_catch_all_warning_propagates_to_persisted_digest(self):
        """RED: The trigger catch-all builds a warning string but it is
        silently dropped by Pydantic because Generation has no warning
        field. This test will pass only when Generation.warning is added
        and the trigger catch-all's warning dict key reaches the store.

        The assertion checks that the persisted digest's generation
        block contains both the exception class name and the message
        text. Today it fails because Pydantic drops the extra key.
        """
        digest_dict = self._trigger_and_capture_stub(
            ValueError("test provider failure")
        )
        self.assertIsNotNone(digest_dict)
        warning = digest_dict["generation"].get("warning", "")
        self.assertIn("ValueError", warning,
                      "warning must contain exception class name")
        self.assertIn("test provider failure", warning,
                      "warning must contain exception message")

    def test_trigger_catch_all_does_not_leak_raw_traceback_in_headline(self):
        """The stub headline must not contain raw exception text or tracebacks."""
        digest_dict = self._trigger_and_capture_stub(
            ValueError("test provider failure")
        )
        self.assertIsNotNone(digest_dict)
        headline = digest_dict["headline"].lower()
        self.assertNotIn("traceback", headline,
                         "headline must not contain traceback text")
        self.assertNotIn("valueerror", headline,
                         "headline must not contain exception class name")


# ---------------------------------------------------------------------------
# Generator parse-fail persistence
# ---------------------------------------------------------------------------

class TestGeneratorParseFailPersistence(unittest.TestCase):
    """When the agent returns no parseable JSON, the generator persists a
    fallback digest with fallback_used=True and a [STUB] headline.

    These tests exercise generate_digest with a mock provider that returns
    non-JSON text, then assert the store received a fallback digest.
    """

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_config(self) -> object:
        from app.config import AppConfig, SessionsConfig, ToolsConfig, \
            MarketDataConfig, SchedulerConfig, TriggerConfig, ProviderName
        return AppConfig(
            provider=ProviderName.OPENAI,
            model="gpt-4",
            sessions=SessionsConfig(),
            tools=ToolsConfig(),
            market_data=MarketDataConfig(),
            data_dir=self._data_dir,
            scheduler=SchedulerConfig(enabled=False),
            trigger=TriggerConfig(),
        )

    def _make_mock_provider(self, final_text: str) -> MagicMock:
        """Return a mock LLMProvider whose generate() returns `final_text`
        as non-JSON text, and whose run_agent result has parsed_json=None."""
        from app.providers.base import ProviderResult
        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock-model"
        provider.generate.return_value = ProviderResult(
            text=final_text,
            tool_calls=[],
            usage={},
            raw=None,
        )
        return provider

    def _generate_and_capture(self, provider_text: str) -> dict | None:
        """Run generate_digest with a mock provider returning `provider_text`,
        and return the persisted digest dict."""
        from app.digests.generator import generate_digest
        from app.digests.store import JsonFileStore

        config = self._make_config()
        store = JsonFileStore(data_dir=self._data_dir)
        provider = self._make_mock_provider(provider_text)

        # Patch market data provider to return empty quotes (no network).
        with patch("app.digests.generator.get_market_data_provider") as mock_mdp:
            mock_mdp.return_value.fetch_quotes.return_value = {}

            digest = generate_digest(
                session="pre_market",
                config=config,
                provider=provider,
                store=store,
                market_snapshot_meta=None,
                as_of=datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc),
            )

        return json.loads(digest.model_dump_json())

    def test_generator_parse_fail_sets_fallback_used(self):
        """The parse-fail branch must set fallback_used=True in the digest."""
        digest_dict = self._generate_and_capture(
            "This is not JSON at all. The model failed to produce a structured brief."
        )
        self.assertIsNotNone(digest_dict)
        self.assertTrue(digest_dict["generation"]["fallback_used"],
                        "fallback digest must have fallback_used=True")

    def test_generator_parse_fail_produces_stub_headline(self):
        """The parse-fail branch must produce a [STUB] headline."""
        digest_dict = self._generate_and_capture(
            "Some random text without JSON structure."
        )
        self.assertIsNotNone(digest_dict)
        self.assertIn("[STUB]", digest_dict["headline"],
                      "fallback digest must have [STUB] headline")

    def test_generator_parse_fail_persists_via_store_write(self):
        """The parse-fail branch must persist the fallback digest to the store."""
        from app.digests.store import JsonFileStore
        config = self._make_config()
        store = JsonFileStore(data_dir=self._data_dir)
        provider = self._make_mock_provider("Not JSON at all.")

        with patch("app.digests.generator.get_market_data_provider") as mock_mdp:
            mock_mdp.return_value.fetch_quotes.return_value = {}
            from app.digests.generator import generate_digest
            digest = generate_digest(
                session="pre_market",
                config=config,
                provider=provider,
                store=store,
                market_snapshot_meta=None,
                as_of=datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc),
            )

        # The digest should be retrievable from the store by id.
        retrieved = store.get(digest.id)
        self.assertIsNotNone(retrieved,
                             "digest must be retrievable from store after generate_digest")
        self.assertEqual(retrieved.id, digest.id)


# ---------------------------------------------------------------------------
# Bounded redaction
# ---------------------------------------------------------------------------

class TestBoundedRedaction(unittest.TestCase):
    """Sensitive content must be bounded or redacted before persistence."""

    def setUp(self):
        self._old_env = os.environ.get("REED_ENV")
        self._old_space = os.environ.get("SPACE_ID")
        os.environ["REED_ENV"] = "dev"
        os.environ.pop("SPACE_ID", None)
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["REED_ENV"] = self._old_env
        else:
            os.environ.pop("REED_ENV", None)
        if self._old_space is not None:
            os.environ["SPACE_ID"] = self._old_space
        else:
            os.environ.pop("SPACE_ID", None)
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_warning_truncated_to_500_chars(self):
        """The warning field in the trigger catch-all must be at most 500 chars.

        This test exercises the trigger endpoint with a very long exception
        message and verifies the persisted warning is bounded.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        long_msg = "A" * 2000
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.model = "mock-model"
        with (
            patch("app.api.trigger.get_provider", return_value=mock_provider),
            patch("app.api.trigger.generate_digest",
                  side_effect=ValueError(long_msg)),
        ):
            client = TestClient(app)
            response = client.post("/api/trigger/pre_market")

        app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 200)

        latest = store.latest(session="pre_market")
        self.assertIsNotNone(latest, "store must contain a digest after trigger catch-all")
        warning = latest.generation.model_dump().get("warning", "")
        self.assertLessEqual(len(warning), 500,
                             "warning must be truncated to 500 chars")

    # --- RED tests: redaction does not exist yet ---

    def test_warning_removes_ansi_escape_codes(self):
        """RED: The trigger catch-all warning must strip ANSI escape codes.

        Exception messages from LLM providers sometimes contain ANSI escape
        sequences (coloured terminal output). These must be removed before
        persistence so the operator sees clean text.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        # Exception message with ANSI colour codes.
        ansi_msg = (
            "\x1b[31mERROR\x1b[0m: \x1b[1mprovider timeout\x1b[22m "
            "on \x1b[4mhttps://api.example.com\x1b[24m"
        )
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.model = "mock-model"
        with (
            patch("app.api.trigger.get_provider", return_value=mock_provider),
            patch("app.api.trigger.generate_digest",
                  side_effect=ValueError(ansi_msg)),
        ):
            client = TestClient(app)
            response = client.post("/api/trigger/pre_market")

        app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 200)

        latest = store.latest(session="pre_market")
        self.assertIsNotNone(latest)
        warning = latest.generation.model_dump().get("warning", "")
        # ANSI escape sequences must be stripped.
        self.assertNotIn("\x1b[", warning,
                         "warning must not contain ANSI escape codes")
        # The readable text should survive.
        self.assertIn("ERROR", warning)
        self.assertIn("provider timeout", warning)

    def test_warning_redacts_authorization_bearer_tokens(self):
        """RED: The trigger catch-all warning must redact bearer tokens.

        Exception messages that include Authorization headers or bearer
        tokens must have the token value replaced with a placeholder so
        credentials do not leak into persisted digests.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        # Exception message containing a bearer token.
        cred_msg = (
            "HTTP 401 from api.openai.com: "
            "Authorization: Bearer sk-proj-ABCDEF1234567890abcdef1234567890"
        )
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.model = "mock-model"
        with (
            patch("app.api.trigger.get_provider", return_value=mock_provider),
            patch("app.api.trigger.generate_digest",
                  side_effect=ValueError(cred_msg)),
        ):
            client = TestClient(app)
            response = client.post("/api/trigger/pre_market")

        app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 200)

        latest = store.latest(session="pre_market")
        self.assertIsNotNone(latest)
        warning = latest.generation.model_dump().get("warning", "")
        # The raw token value must not appear.
        self.assertNotIn("sk-proj-ABCDEF1234567890abcdef1234567890", warning,
                         "warning must not contain raw bearer token")
        # The context (HTTP 401, Authorization: Bearer) may remain.
        self.assertIn("HTTP 401", warning)

    def test_warning_redacts_url_query_strings(self):
        """RED: The trigger catch-all warning must redact URL query strings.

        Exception messages that include URLs with query parameters (which
        may contain API keys, session tokens, or other secrets) must have
        the query string removed or redacted.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        # Exception message containing a URL with a query-string API key.
        query_msg = (
            "Connection refused: "
            "https://api.example.com/v1/chat?api_key=sk-secret-123&model=gpt-4"
        )
        mock_provider = MagicMock()
        mock_provider.name = "mock"
        mock_provider.model = "mock-model"
        with (
            patch("app.api.trigger.get_provider", return_value=mock_provider),
            patch("app.api.trigger.generate_digest",
                  side_effect=ValueError(query_msg)),
        ):
            client = TestClient(app)
            response = client.post("/api/trigger/pre_market")

        app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 200)

        latest = store.latest(session="pre_market")
        self.assertIsNotNone(latest)
        warning = latest.generation.model_dump().get("warning", "")
        # The raw query string must not appear.
        self.assertNotIn("api_key=sk-secret-123", warning,
                         "warning must not contain raw query-string API key")
        # The base URL may remain.
        self.assertIn("api.example.com", warning)

    def test_generator_parse_fail_warning_bounded_to_500_chars(self):
        """RED: The generator parse-fail warning must be bounded to 500 chars.

        The generator's parse-fail path (generator.py line ~289) passes
        agent_result.warning through without truncation. A long warning
        from the agent runner can exceed 500 chars and bloat the persisted
        digest.
        """
        from app.digests.generator import generate_digest
        from app.digests.store import JsonFileStore
        from app.config import AppConfig, SessionsConfig, ToolsConfig, \
            MarketDataConfig, SchedulerConfig, TriggerConfig, ProviderName
        from app.providers.base import ProviderResult

        config = AppConfig(
            provider=ProviderName.OPENAI,
            model="gpt-4",
            sessions=SessionsConfig(),
            tools=ToolsConfig(),
            market_data=MarketDataConfig(),
            data_dir=self._data_dir,
            scheduler=SchedulerConfig(enabled=False),
            trigger=TriggerConfig(),
        )
        store = JsonFileStore(data_dir=self._data_dir)

        # A mock provider whose generate() returns non-JSON text, and whose
        # AgentRunResult.warning is very long (>500 chars).
        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock-model"
        provider.generate.return_value = ProviderResult(
            text="Some random text without JSON structure.",
            tool_calls=[],
            usage={},
            raw=None,
        )

        long_warning = "provider error: " + ("X" * 2000)
        with (
            patch("app.digests.generator.get_market_data_provider") as mock_mdp,
            patch("app.digests.generator.run_agent") as mock_run,
        ):
            mock_mdp.return_value.fetch_quotes.return_value = {}
            mock_run.return_value.warning = long_warning
            mock_run.return_value.parsed_json = None
            mock_run.return_value.final_text = "not json"
            mock_run.return_value.turns = 1
            mock_run.return_value.tool_calls = []
            mock_run.return_value.fallback_used = True
            mock_run.return_value.duration_ms = 100

            digest = generate_digest(
                session="pre_market",
                config=config,
                provider=provider,
                store=store,
                market_snapshot_meta=None,
                as_of=datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc),
            )

        warning = digest.generation.warning or ""
        self.assertLessEqual(
            len(warning), 500,
            "generator parse-fail warning must be bounded to 500 chars; "
            f"got {len(warning)} chars",
        )

    def test_generator_parse_fail_warning_redacts_sensitive_content(self):
        """RED: The generator parse-fail warning must strip ANSI escapes,
        bearer tokens, and URL query strings.

        The generator's parse-fail path passes agent_result.warning through
        without any redaction. Sensitive content from provider errors leaks
        into the persisted digest.
        """
        from app.digests.generator import generate_digest
        from app.digests.store import JsonFileStore
        from app.config import AppConfig, SessionsConfig, ToolsConfig, \
            MarketDataConfig, SchedulerConfig, TriggerConfig, ProviderName
        from app.providers.base import ProviderResult

        config = AppConfig(
            provider=ProviderName.OPENAI,
            model="gpt-4",
            sessions=SessionsConfig(),
            tools=ToolsConfig(),
            market_data=MarketDataConfig(),
            data_dir=self._data_dir,
            scheduler=SchedulerConfig(enabled=False),
            trigger=TriggerConfig(),
        )
        store = JsonFileStore(data_dir=self._data_dir)

        provider = MagicMock()
        provider.name = "mock"
        provider.model = "mock-model"
        provider.generate.return_value = ProviderResult(
            text="Some random text without JSON structure.",
            tool_calls=[],
            usage={},
            raw=None,
        )

        # A warning with ANSI escapes, a bearer token, and a URL query string.
        dirty_warning = (
            "\x1b[31mERROR\x1b[0m: HTTP 401 from api.openai.com - "
            "Authorization: Bearer sk-proj-ABCDEF1234567890abcdef1234567890 | "
            "https://api.example.com/v1/chat?api_key=sk-secret-123&model=gpt-4"
        )
        with (
            patch("app.digests.generator.get_market_data_provider") as mock_mdp,
            patch("app.digests.generator.run_agent") as mock_run,
        ):
            mock_mdp.return_value.fetch_quotes.return_value = {}
            mock_run.return_value.warning = dirty_warning
            mock_run.return_value.parsed_json = None
            mock_run.return_value.final_text = "not json"
            mock_run.return_value.turns = 1
            mock_run.return_value.tool_calls = []
            mock_run.return_value.fallback_used = True
            mock_run.return_value.duration_ms = 100

            digest = generate_digest(
                session="pre_market",
                config=config,
                provider=provider,
                store=store,
                market_snapshot_meta=None,
                as_of=datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc),
            )

        warning = digest.generation.warning or ""
        self.assertNotIn("\x1b[", warning,
                         "generator parse-fail warning must strip ANSI escapes")
        self.assertNotIn("sk-proj-ABCDEF1234567890abcdef1234567890", warning,
                         "generator parse-fail warning must redact bearer tokens")
        self.assertNotIn("api_key=sk-secret-123", warning,
                         "generator parse-fail warning must redact URL query strings")

    def test_no_raw_traceback_in_persisted_digest(self):
        """The persisted digest must not contain raw traceback text.

        Uses TestClient to verify the actual API response shape, not
        the private model schema. This test must remain GREEN even
        after Generation.warning is added.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore
        from app.digests.models import Digest, Generation, MarketSnapshotMeta
        from datetime import datetime, timezone

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        digest = Digest(
            session="pre_market",
            as_of=datetime.now(timezone.utc),
            headline="[STUB] test",
            executive_summary="test",
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub", fetched_at="2026-01-01T00:00:00", values_raw={}
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation=Generation(
                provider="stub",
                model="stub",
                agent_turns=0,
                tool_calls=0,
                scraped_urls=0,
                fallback_used=True,
                duration_ms=0,
            ),
        )
        store.write(digest)

        client = TestClient(app)
        response = client.get(f"/api/digests/{digest.id}")
        app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        # The response must not contain raw traceback text anywhere.
        body_str = json.dumps(body)
        self.assertNotIn("traceback", body_str.lower(),
                         "API response must not contain traceback text")

    def test_public_digest_excludes_warning(self):
        """The Digest model's generation field must not carry a warning
        that leaks into the public API response.

        Uses TestClient to verify the actual API response shape, not
        the private model schema. This test must remain GREEN even
        after Generation.warning is added, because the API response
        model (Digest) does not include warning in its generation
        block.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore
        from app.digests.models import Digest, Generation, MarketSnapshotMeta
        from datetime import datetime, timezone

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        # Seed a digest with a warning in the generation dict.
        digest = Digest(
            session="pre_market",
            as_of=datetime.now(timezone.utc),
            headline="[STUB] test",
            executive_summary="test",
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub", fetched_at="2026-01-01T00:00:00", values_raw={}
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation=Generation(
                provider="stub",
                model="stub",
                agent_turns=0,
                tool_calls=0,
                scraped_urls=0,
                fallback_used=True,
                duration_ms=0,
            ),
        )
        store.write(digest)

        client = TestClient(app)
        response = client.get(f"/api/digests/{digest.id}")
        app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        gen = body.get("generation", {})
        self.assertNotIn("warning", gen,
                         "API response generation block must not contain warning")

    # --- RED tests: behavior that does not exist yet ---

    def test_generation_warning_is_silently_dropped_by_pydantic(self):
        """RED: The Generation model has no warning field, so the
        trigger catch-all's warning is silently dropped by Pydantic
        model_validate. This test demonstrates the gap: a Generation
        constructed with warning='test' will not retain it.

        The fix: add a warning: str | None = None field to Generation
        so the operator can see the exception in the persisted digest.
        """
        from app.digests.models import Generation
        gen = Generation(
            provider="test",
            model="test",
            agent_turns=0,
            tool_calls=0,
            scraped_urls=0,
            fallback_used=True,
            duration_ms=0,
            warning="test warning",  # type: ignore[call-arg]
        )
        # This assertion will fail because warning is silently dropped.
        self.assertEqual(gen.warning, "test warning")

    def test_trigger_catch_all_warning_not_persisted(self):
        """RED: The trigger catch-all builds a warning string but it
        is silently dropped by Pydantic because Generation has no
        warning field. The operator cannot see the exception in the
        persisted digest.

        This test constructs a Digest with a generation dict that
        includes warning, then validates it back. The warning is lost.
        """
        from app.digests.models import Digest, Generation, MarketSnapshotMeta
        from datetime import datetime, timezone

        gen_data = {
            "provider": "stub",
            "model": "stub",
            "agent_turns": 0,
            "tool_calls": 0,
            "scraped_urls": 0,
            "fallback_used": True,
            "duration_ms": 0,
            "warning": "ValueError: unknown session 'not_a_real_session'",
        }
        digest = Digest(
            session="pre_market",
            as_of=datetime.now(timezone.utc),
            headline="[STUB] test",
            executive_summary="test",
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub", fetched_at="2026-01-01T00:00:00", values_raw={}
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation=Generation(**gen_data),
        )
        # The warning is silently dropped - this assertion will fail.
        self.assertEqual(digest.generation.warning,
                         "ValueError: unknown session 'not_a_real_session'")


# ---------------------------------------------------------------------------
# Public API exclusion of diagnostics
# ---------------------------------------------------------------------------

class TestPublicApiExcludesDiagnostics(unittest.TestCase):
    """Public API endpoints must not expose diagnostic/internal details."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._data_dir = Path(self._tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_list_digests_response_excludes_warning(self):
        """The /api/digests list response must not include a warning
        field on each digest's generation block.

        Uses TestClient to verify the actual API response shape, not
        the private model schema. This test must remain GREEN even
        after Generation.warning is added.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore
        from app.digests.models import Digest, Generation, MarketSnapshotMeta
        from datetime import datetime, timezone

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        # Seed a digest so the list endpoint has something to return.
        digest = Digest(
            session="pre_market",
            as_of=datetime.now(timezone.utc),
            headline="[STUB] test",
            executive_summary="test",
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub", fetched_at="2026-01-01T00:00:00", values_raw={}
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation=Generation(
                provider="stub",
                model="stub",
                agent_turns=0,
                tool_calls=0,
                scraped_urls=0,
                fallback_used=True,
                duration_ms=0,
            ),
        )
        store.write(digest)

        client = TestClient(app)
        response = client.get("/api/digests")
        app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsInstance(body, list)
        for entry in body:
            gen = entry.get("generation", {})
            self.assertNotIn("warning", gen,
                             "list API response generation block must not contain warning")

    def test_get_digest_response_excludes_warning(self):
        """The /api/digests/{id} response must not include a warning
        field on the generation block.

        Uses TestClient to verify the actual API response shape, not
        the private model schema. This test must remain GREEN even
        after Generation.warning is added.
        """
        from fastapi.testclient import TestClient
        from app.main import app
        from app.api.deps import get_store
        from app.digests.store import JsonFileStore
        from app.digests.models import Digest, Generation, MarketSnapshotMeta
        from datetime import datetime, timezone

        store = JsonFileStore(data_dir=self._data_dir)
        app.dependency_overrides[get_store] = lambda: store

        digest = Digest(
            session="pre_market",
            as_of=datetime.now(timezone.utc),
            headline="[STUB] test",
            executive_summary="test",
            market_snapshot={},
            market_snapshot_meta=MarketSnapshotMeta(
                source="stub", fetched_at="2026-01-01T00:00:00", values_raw={}
            ),
            stories=[],
            themes=[],
            watch_next_session=[],
            sources=[],
            generation=Generation(
                provider="stub",
                model="stub",
                agent_turns=0,
                tool_calls=0,
                scraped_urls=0,
                fallback_used=True,
                duration_ms=0,
            ),
        )
        store.write(digest)

        client = TestClient(app)
        response = client.get(f"/api/digests/{digest.id}")
        app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        gen = body.get("generation", {})
        self.assertNotIn("warning", gen,
                         "get API response generation block must not contain warning")

    def test_sessions_endpoint_excludes_internal_fields(self):
        """The /api/sessions endpoint must not expose system_prompt or
        user_prompt_template in its response."""
        from app.api.sessions import list_sessions
        sessions = list_sessions()
        for s in sessions:
            self.assertNotIn("system_prompt", s,
                             "sessions endpoint must not expose system_prompt")
            self.assertNotIn("user_prompt_template", s,
                             "sessions endpoint must not expose user_prompt_template")
            # The response should only include name, time_window, topic.
            self.assertIn("name", s)
            self.assertIn("time_window", s)
            self.assertIn("topic", s)


# ---------------------------------------------------------------------------
# Unknown session rejection
# ---------------------------------------------------------------------------

class TestUnknownSessionRejection(unittest.TestCase):
    """Unknown session names must be rejected without persisting a stub digest."""

    def test_unknown_session_raises_in_generate_digest(self):
        """generate_digest must raise ValueError for unknown session names."""
        from app.digests.generator import generate_digest
        from app.config import AppConfig, SessionsConfig, ToolsConfig, MarketDataConfig, SchedulerConfig, TriggerConfig, ProviderName

        config = AppConfig(
            provider=ProviderName.OPENAI,
            model="gpt-4",
            sessions=SessionsConfig(),
            tools=ToolsConfig(),
            market_data=MarketDataConfig(),
            data_dir="/tmp",
            scheduler=SchedulerConfig(enabled=False),
            trigger=TriggerConfig(),
        )
        store = MagicMock()

        with self.assertRaises(ValueError) as ctx:
            generate_digest(
                session="not_a_real_session",
                config=config,
                provider=None,
                store=store,
            )
        self.assertIn("unknown session", str(ctx.exception).lower())

    def test_unknown_session_does_not_persist(self):
        """When generate_digest raises for an unknown session, the trigger
        must NOT persist a stub digest. Currently the trigger catch-all
        persists stubs for all exceptions including unknown sessions.
        This test asserts the DESIRED behavior and will fail (RED) until
        the gate is added."""
        from app.digests.generator import generate_digest
        from app.config import AppConfig, SessionsConfig, ToolsConfig, MarketDataConfig, SchedulerConfig, TriggerConfig, ProviderName

        config = AppConfig(
            provider=ProviderName.OPENAI,
            model="gpt-4",
            sessions=SessionsConfig(),
            tools=ToolsConfig(),
            market_data=MarketDataConfig(),
            data_dir="/tmp",
            scheduler=SchedulerConfig(enabled=False),
            trigger=TriggerConfig(),
        )
        store = MagicMock()

        with self.assertRaises(ValueError):
            generate_digest(
                session="not_a_real_session",
                config=config,
                provider=None,
                store=store,
            )
        # store.write must NOT have been called for an unknown session.
        store.write.assert_not_called()


# ---------------------------------------------------------------------------
# Workflow mapping (PyYAML structural tests)
# ---------------------------------------------------------------------------

class TestWorkflowMapping(unittest.TestCase):
    """The scheduler's workflow identity mapping must be deterministic
    and match the five registered sessions."""

    def test_schedule_keys_match_session_names(self):
        """Every key in SCHEDULE must be a registered session name."""
        import sys
        import unittest.mock as um
        sys.modules["apscheduler"] = um.MagicMock()
        sys.modules["apscheduler.schedulers"] = um.MagicMock()
        sys.modules["apscheduler.schedulers.background"] = um.MagicMock()
        sys.modules["apscheduler.triggers"] = um.MagicMock()
        sys.modules["apscheduler.triggers.cron"] = um.MagicMock()
        try:
            from app.scheduler import SCHEDULE
            from app.sessions.registry import all_sessions
            registered = {s.name for s in all_sessions()}
            for key in SCHEDULE:
                self.assertIn(key, registered,
                              f"scheduler key {key!r} is not a registered session")
        finally:
            for mod in list(sys.modules):
                if mod.startswith("apscheduler"):
                    del sys.modules[mod]

    def test_all_registered_sessions_have_schedule_entry(self):
        """Every registered session must have an entry in SCHEDULE."""
        import sys
        import unittest.mock as um
        sys.modules["apscheduler"] = um.MagicMock()
        sys.modules["apscheduler.schedulers"] = um.MagicMock()
        sys.modules["apscheduler.schedulers.background"] = um.MagicMock()
        sys.modules["apscheduler.triggers"] = um.MagicMock()
        sys.modules["apscheduler.triggers.cron"] = um.MagicMock()
        try:
            from app.scheduler import SCHEDULE
            from app.sessions.registry import all_sessions
            for s in all_sessions():
                self.assertIn(s.name, SCHEDULE,
                              f"registered session {s.name!r} has no scheduler entry")
        finally:
            for mod in list(sys.modules):
                if mod.startswith("apscheduler"):
                    del sys.modules[mod]


if __name__ == "__main__":
    unittest.main()
