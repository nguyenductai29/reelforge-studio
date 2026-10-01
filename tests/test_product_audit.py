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


class Phase18FrontendTest(unittest.TestCase):
    def test_header_has_the_notification_bell_between_generation_and_account(self):
        shell = source("components/reelforge/app-shell.tsx")
        order = [shell.index(tag) for tag in ("<GenerationCenter />", "<NotificationCenter />", "<AccountMenu />")]
        self.assertEqual(order, sorted(order))
        self.assertIn("<NotificationStream>", shell)
        center = source("components/reelforge/notifications.tsx")
        self.assertIn('new EventSource("/api/notifications/stream")', center)
        self.assertIn('"99+"', center)
        self.assertIn("refetchInterval: fallback ? 30000 : false", source("lib/queries.ts"))

    def test_support_and_notification_pages_exist(self):
        for path in ("app/support/page.tsx", "app/support/[ticketId]/page.tsx", "app/notifications/page.tsx"):
            with self.subTest(path=path):
                self.assertTrue((FRONTEND / path).is_file())
        self.assertIn('href="/support"', source("components/reelforge/app-shell.tsx"))

    def test_admin_keeps_its_fixed_height_with_support_and_verification_tabs(self):
        admin = source("app/admin/page.tsx")
        self.assertIn("h-[calc(100dvh-6.5rem)] min-h-0 flex-col gap-3 overflow-hidden", admin)
        self.assertIn('"support", "reconciliation", "operations", "verification"', admin)
        for tab in ("support", "verification"):
            with self.subTest(tab=tab):
                self.assertIn(f'<TabsContent value="{tab}" className="mt-3 flex min-h-0 flex-1 flex-col">', admin)
        self.assertIn("grid min-h-0 flex-1", source("components/reelforge/admin/admin-verification.tsx"))

    def test_payment_setup_lives_only_in_admin(self):
        users_pages = [path for path in frontend_files() if "admin" not in path.as_posix()]
        offenders = [str(path.relative_to(FRONTEND)) for path in users_pages
                     if "payment-config" in path.read_text(encoding="utf-8") and path.name != "queries.ts"]
        self.assertEqual(offenders, [])
        self.assertIn("PaymentGatewaysDialog", source("components/reelforge/admin/admin-payments.tsx"))


class Phase19FrontendTest(unittest.TestCase):
    GATEWAYS = "components/reelforge/admin/payment-gateways.tsx"

    def test_system_admins_manage_both_gateways_in_admin(self):
        ui = source(self.GATEWAYS)
        self.assertIn('api(`admin/payment-config/${provider}`, jsonRequest("PUT", body))', ui)
        self.assertIn('`admin/payment-config/${provider}/${on ? "enable" : "disable"}`', ui)
        self.assertIn("`admin/payment-config/${provider}/check`", ui)
        self.assertIn('payos: ["client_id", "api_key", "checksum_key"]', ui)
        self.assertIn('onepay: ["merchant_id", "access_code", "hash_key", "query_user", "query_password"]', ui)

    def test_saved_secrets_are_never_rendered(self):
        ui = source(self.GATEWAYS)
        # An input only ever holds what the admin is typing now; saved values exist only as masked identifiers.
        self.assertIn('value={update.action === "replace" ? update.value : ""}', ui)
        self.assertIn('type={field.secret ? "password" : "text"}', ui)
        self.assertIn("key={`${card.updated_at}-${card.enabled}-${card.mode}`}", ui)
        types = source("lib/types.ts")
        field_type = types[types.index("export type PaymentField = {"):types.index("export type PaymentIssue")]
        self.assertIn("masked?: string", field_type)
        self.assertNotIn("value", field_type)
        self.assertIn('export type SecretUpdate = { action: "keep" }', types)

    def test_sandbox_and_production_are_obvious_and_production_is_confirmed(self):
        ui = source(self.GATEWAYS)
        self.assertIn("export function ModeBadge", ui)
        self.assertIn("{card.mode && <ModeBadge mode={card.mode} />}", ui)
        self.assertIn("<AlertDialog open={confirming !== null}", ui)
        self.assertIn('mode === "production" && !wasLiveProduction', ui)
        for locale, phrase in (("vi", "tiền thật"), ("en", "Real customer payments can now be accepted"),
                               ("ja", "実際の顧客決済")):
            with self.subTest(locale=locale):
                self.assertIn(phrase, source(f"lib/i18n/{locale}.ts"))

    def test_billing_names_methods_for_buyers_without_internals(self):
        for locale, vietqr, card in (("vi", "VietQR / Chuyển khoản", "Thẻ tín dụng / ghi nợ"),
                                     ("en", "VietQR / Bank Transfer", "Credit / Debit Card"),
                                     ("ja", "VietQR / 銀行振込", "クレジット / デビットカード")):
            with self.subTest(locale=locale):
                text = source(f"lib/i18n/{locale}.ts")
                self.assertIn(f'vietqr: {{ name: "{vietqr}"', text)
                self.assertIn(f'card: {{ name: "{card}"', text)
                methods = text[text.index("    methods: {"):text.index("    chooseMethod:")]
                missing = next(line for line in text.splitlines() if line.strip().startswith("noPaymentMethod:"))
                for internal in ("payOS", "OnePAY", "server", "máy chủ", "サーバー", "merchant"):
                    self.assertNotIn(internal, methods + missing)

    def test_gateway_configuration_lives_only_in_admin(self):
        outside = [path for path in frontend_files()
                   if "admin" not in path.as_posix() and path.name != "queries.ts"]
        for path in outside:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=str(path.relative_to(FRONTEND))):
                self.assertNotIn("payment-config", text)
                self.assertNotIn("PaymentGatewaysDialog", text)

    def test_admin_shows_plan_purchasability_and_missing_gateways(self):
        admin = source("app/admin/page.tsx")
        self.assertIn("function purchasability(plan: Plan, paymentReady: boolean)", admin)
        self.assertIn("{!paymentReady && <p className=\"text-xs text-warning\">{a.noGateway}</p>}", admin)



class Phase20FrontendTest(unittest.TestCase):
    def test_admin_has_system_settings_for_every_section(self):
        admin = source("app/admin/page.tsx")
        self.assertIn('"verification", "system", "audit"] as const', admin)
        self.assertIn('<TabsContent value="system" className="mt-3 flex min-h-0 flex-1 flex-col">', admin)
        system = source("components/reelforge/admin/admin-system.tsx")
        self.assertIn('const NAV = ["security", "general", "email", "ai", "social", "storage", "backups", "runtime", "credits",'
                      ' "notifications"]', system)
        self.assertIn("`admin/system-config/${section}`", system)
        self.assertIn("`admin/system-config/ai/${provider}/test`", system)
        # Secret inputs never hold a saved value; the page scrolls inside the fixed admin layout.
        self.assertIn('value={update.action === "replace" ? update.value : ""}', system)
        self.assertIn('type="password"', system)
        self.assertIn("scrollbar-thin relative min-h-0 flex-1 overflow-y-auto", system)
        self.assertNotIn("api_key:", source("lib/types.ts").split("export type SystemSetting = {")[1].split("};")[0])

    def test_storage_root_change_needs_explicit_confirmation(self):
        system = source("components/reelforge/admin/admin-system.tsx")
        self.assertIn('"root_change_requires_confirmation"', system)
        self.assertIn("confirm_root_change: confirmRootChange", system)

    def test_vietqr_tab_offers_manual_and_payos_modes_with_a_preview(self):
        ui = source("components/reelforge/admin/payment-gateways.tsx")
        self.assertIn('(["manual", "payos"] as const)', ui)
        self.assertIn('"admin/payment-config/bank_qr/preview"', ui)
        self.assertIn("use_for_vietqr: true", ui)
        self.assertIn("<GatewayPanel key={`${vietqr.updated_at}-${vietqr.enabled}-${savedMode}`} setup={vietqr}", ui)

    def test_buyers_see_the_qr_and_report_the_transfer_but_never_confirm(self):
        billing = source("app/billing/page.tsx")
        self.assertIn("<TransferCard transfer={transfer.details} />", billing)
        self.assertIn("/transferred`", billing)
        self.assertNotIn("/confirm", billing)
        for path in frontend_files():
            if "admin" in path.as_posix():
                continue
            with self.subTest(path=str(path.relative_to(FRONTEND))):
                self.assertNotIn("admin/payments/", path.read_text(encoding="utf-8"))

    def test_admin_confirmation_states_amount_and_order(self):
        payments = source("components/reelforge/admin/admin-payments.tsx")
        self.assertIn("p.confirmText(formatMoney(reviewing.order.amount_vnd)", payments)
        self.assertIn("{ amount_vnd: order.amount_vnd }", payments)
        for locale, phrase in (("vi", "Xác nhận đã nhận"), ("en", "Confirm that"), ("ja", "入金を確認します")):
            with self.subTest(locale=locale):
                self.assertIn(phrase, source(f"lib/i18n/{locale}.ts"))



class Phase21FrontendTest(unittest.TestCase):
    def test_vietqr_tab_shows_one_mode_at_a_time_and_bins_only_for_other_banks(self):
        ui = source("components/reelforge/admin/payment-gateways.tsx")
        # Manual mode renders the bank form only; payOS mode renders the payOS credentials only.
        self.assertRegex(ui, r'\{vietqr && \(vietqrMode === "manual" \? \(\s*<BankQRPanel')
        self.assertIn("{otherBank && (", ui)
        self.assertIn('<option value="other">{q.otherBank}</option>', ui)

    def test_admin_review_shows_transfer_details_and_a_quick_filter(self):
        payments = source("components/reelforge/admin/admin-payments.tsx")
        self.assertIn('{ key: "transfer", header: p.columns.transfer', payments)
        self.assertIn("p.reportedOn(formatDateTime(o.transfer_reported_at))", payments)
        self.assertIn('filtered(setStatus)("awaiting_confirmation")', payments)
        self.assertIn("reviewing.order.workspace_name, reviewing.order.plan_code.toUpperCase()", payments)

    def test_buyers_see_their_transfer_is_waiting(self):
        billing = source("app/billing/page.tsx")
        self.assertIn('order.status === "awaiting_confirmation"', billing)
        self.assertIn("t.billing.transfer.waitingBanner", billing)
        for locale in ("vi", "en", "ja"):
            with self.subTest(locale=locale):
                text = source(f"lib/i18n/{locale}.ts")
                self.assertRegex(text, r'awaiting_confirmation: "[^"]+",\s*rejected: "[^"]+"')

    def test_system_settings_show_provider_status_and_environment_dependence(self):
        system = source("components/reelforge/admin/admin-system.tsx")
        self.assertIn("<ProviderStatus data={data} provider={provider} />", system)
        self.assertIn("data.legacy_in_use", system)
        self.assertIn("data.environment.filter((row) => row.set)", system)
        self.assertNotIn("row.value", system)


if __name__ == "__main__":
    unittest.main()
