"""Phase 16 product audit, checked statically: everything the production UI offers must actually work.

The step library, templates, channel lists and settings are read from the frontend sources and matched
against the backend registry, so a fake control (a "coming soon" badge, a disabled placeholder switch, a
library item without an executor) fails here before it ships.
"""
import json
from pathlib import Path
import re
import unittest

from app.publications import CHANNELS
from app.workflow import default_registry
from app.workflow.nodes import PendingAITaskHandler, PendingServiceHandler, UnsupportedNodeHandler
from app.workflow.templates import TEMPLATES, template_graph

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend" / "src"
WORKFLOW_TS = (FRONTEND / "lib" / "workflow.ts").read_text(encoding="utf-8")
PLACEHOLDERS = (PendingAITaskHandler, PendingServiceHandler, UnsupportedNodeHandler)
# Advanced features the audit hid instead of faking (docs/FINAL_PRODUCT_AUDIT.md).
HIDDEN_ITEMS = ("voiceClone", "soundEffects", "audioMixer", "timeline", "transition", "overlayText", "cropResize",
                "aspectRatio", "imageToVideo", "stockMedia", "storyboard", "scenePlanner", "research", "youtubeUrl",
                "download")


def source(path: str) -> str:
    return (FRONTEND / path).read_text(encoding="utf-8")


def frontend_files():
    return [path for path in FRONTEND.rglob("*") if path.suffix in {".ts", ".tsx"}]


def library_items() -> list[tuple[str, str]]:
    start = WORKFLOW_TS.index("export const nodeLibrary")
    end = WORKFLOW_TS.index("export const libraryItemById")
    return re.findall(r'\{ id: "(\w+)", kind: "\w+", type: "(\w+)"', WORKFLOW_TS[start:end])


def executable_types() -> set[str]:
    start = WORKFLOW_TS.index("export const EXECUTABLE")
    body = WORKFLOW_TS[start:WORKFLOW_TS.index("]);", start)]
    types = set(re.findall(r'"(\w+)"', body))
    for spread in re.findall(r"\.\.\.(\w+)", body):
        declaration = re.search(rf"export const {spread}\b[^=]*= new Set\(\[([^\]]*)\]", WORKFLOW_TS)
        assert declaration, spread
        types |= set(re.findall(r'"(\w+)"', declaration.group(1)))
    return types


class StepLibraryTest(unittest.TestCase):
    def test_every_library_item_adds_a_step_the_backend_runs(self):
        items = library_items()
        self.assertGreater(len(items), 30)
        executable = executable_types()
        for item_id, node_type in items:
            with self.subTest(item=item_id):
                self.assertIn(node_type, default_registry.node_types)
                self.assertNotIsInstance(default_registry.resolve(node_type), PLACEHOLDERS)
                self.assertIn(node_type, executable)

    def test_every_preset_is_a_valid_setting_of_its_step(self):
        start = WORKFLOW_TS.index("export const nodeLibrary")
        presets = re.findall(r'\{ id: "(\w+)", kind: "\w+", type: "(\w+)", config: (\{[^}]*\}) \}',
                             WORKFLOW_TS[start:WORKFLOW_TS.index("export const libraryItemById")])
        self.assertGreaterEqual(len(presets), 5)
        for item_id, node_type, literal in presets:
            with self.subTest(item=item_id):
                config = json.loads(re.sub(r"([{,]\s*)(\w+):", r'\1"\2":', literal))
                default_registry.resolve(node_type).validate_config(config)

    def test_the_editor_only_marks_real_executors_as_executable(self):
        for node_type in executable_types():
            with self.subTest(node_type=node_type):
                self.assertNotIsInstance(default_registry.resolve(node_type), PLACEHOLDERS)

    def test_music_is_a_real_step(self):
        self.assertIn(("backgroundMusic", "music"), library_items())

    def test_advanced_features_without_a_backend_are_hidden(self):
        ids = {item_id for item_id, _ in library_items()}
        for hidden in HIDDEN_ITEMS:
            with self.subTest(item=hidden):
                self.assertNotIn(hidden, ids)


class TemplatesTest(unittest.TestCase):
    def templates(self):
        start = WORKFLOW_TS.index("export const workflowTemplates")
        return re.findall(r'\{\s*id: "([\w-]+)",\s*(graph|backend)(?:: "(\w+)")?', WORKFLOW_TS[start:])

    def test_every_template_creates_a_runnable_workflow(self):
        templates = self.templates()
        ids = {template_id for template_id, _, _ in templates}
        self.assertTrue({"movie-review", "article-to-video", "product-video", "movie-recap"} <= ids)
        for template_id, kind, backend in templates:
            if kind != "backend":
                continue
            with self.subTest(template=template_id):
                self.assertIn(backend, TEMPLATES)
                graph = template_graph(backend)
                for node in graph["nodes"]:
                    self.assertNotIsInstance(default_registry.resolve(node["type"]), PLACEHOLDERS)

    def test_no_template_is_marked_as_unavailable(self):
        start = WORKFLOW_TS.index("export const workflowTemplates")
        block = WORKFLOW_TS[start:WORKFLOW_TS.index("];", start)]
        self.assertNotIn("soon", block.lower())
        # Every template entry (not the nodes inside a graph) declares how it is built.
        self.assertEqual(len(re.findall(r'\{\s*id: "[\w-]+",(?!\s*type:)', block)), len(self.templates()))


class ComingSoonTest(unittest.TestCase):
    def test_no_coming_soon_component_remains(self):
        offenders = [str(path.relative_to(FRONTEND)) for path in frontend_files()
                     if re.search(r"\b(SoonBadge|ComingSoonBanner)\b", path.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_no_coming_soon_text_in_any_language(self):
        for locale, phrase in (("vi", "Sắp có"), ("en", "Coming soon"), ("ja", "近日公開")):
            with self.subTest(locale=locale):
                text = source(f"lib/i18n/{locale}.ts")
                self.assertEqual([line for line in text.splitlines() if phrase.lower() in line.lower()], [])
                self.assertEqual(re.findall(r"\b\w*[sS]oon\w*:", text), [])

    def test_no_hardcoded_disabled_placeholder_controls(self):
        offenders = [str(path.relative_to(FRONTEND)) for path in frontend_files()
                     if "components/ui" not in path.as_posix()
                     and re.search(r"<Switch disabled\b|onChange=\{\(\) => undefined\}", path.read_text(encoding="utf-8"))]
        self.assertEqual(offenders, [])

    def test_single_ai_tool_pages_redirect_to_templates(self):
        for tool in ("writer", "image", "video", "movie-recap", "repurpose"):
            with self.subTest(tool=tool):
                self.assertIn('redirect("/create")', source(f"app/ai/{tool}/page.tsx"))


class ChannelsTest(unittest.TestCase):
    def test_instagram_is_hidden_until_it_has_a_real_integration(self):
        self.assertEqual(CHANNELS, ("youtube", "tiktok", "facebook"))
        for path in ("app/channels/page.tsx", "app/publishing/page.tsx", "components/reelforge/primitives.tsx",
                     "components/reelforge/publish-dialog.tsx", "lib/types.ts"):
            with self.subTest(path=path):
                self.assertNotIn("instagram", source(path).lower())

    def test_tiktok_and_facebook_are_operational_channels(self):
        channels = source("app/channels/page.tsx")
        self.assertIn('const CHANNELS: ChannelId[] = ["youtube", "tiktok", "facebook"];', channels)
        self.assertIn('(["youtube", "tiktok", "facebook"] as const)', source("app/publishing/page.tsx"))


class PagesTest(unittest.TestCase):
    def test_calendar_loads_the_visible_range_and_shows_statuses(self):
        calendar = source("app/calendar/page.tsx")
        self.assertIn("useCalendarPublications(", calendar)
        self.assertIn("t.status.publication[p.state]", calendar)
        self.assertNotIn("usePublications()", calendar)

    def test_publishing_queue_and_billing_history_are_paginated(self):
        self.assertIn("usePublicationsPage(offset, PAGE)", source("app/publishing/page.tsx"))
        billing = source("app/billing/page.tsx")
        self.assertIn("useBillingOrders(", billing)
        self.assertIn("<DataTable", billing)

    def test_settings_offer_only_working_preferences(self):
        settings = source("app/settings/page.tsx")
        for binding in ('"settings/profile"', "default_platform", "default_tone", "default_duration",
                        "default_publish_time"):
            with self.subTest(binding=binding):
                self.assertIn(binding, settings)
        for removed in ("twoFactor", "teammates", "retention", "autoSchedule", "recaps", "thumbnails", "providerIds"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, settings)

    def test_library_scripts_and_media_project_are_real(self):
        self.assertIn("useScripts(", source("app/library/page.tsx"))
        self.assertIn("jsonRequest(\"PATCH\", { project_id: projectId })", source("app/media/page.tsx"))


if __name__ == "__main__":
    unittest.main()
