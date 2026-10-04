"""Phase 12 per-channel metadata: validation, prefilled values, and owner-typed values that are never replaced."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.publications import (MetadataError, fit_social, platform_metadata, social_text, validate_channel_metadata,
                              schedule_time)
from app.workflow.context import NodeInputs
from app.workflow.nodes.publish import PublishNodeHandler
from app.workflow.nodes.text import _publish_metadata, parse_platforms


class ChannelValidationTest(unittest.TestCase):
    def test_each_channel_has_its_own_rules(self):
        self.assertEqual(validate_channel_metadata("tiktok", " Rừng ", "Xem nhé", ["#rừng", "cú"], "private"),
                         ("Rừng", "Xem nhé", ["rừng", "cú"], "private"))
        cases = [
            ("tiktok", ("T", "x", [], "public"), "invalid_privacy"),
            ("tiktok", ("T", "x" * 2196, ["dài"]), "invalid_description"),  # with "

#dài" it exceeds 2,200
            ("tiktok", ("T", "x", ["hai từ"]), "invalid_tags"),
            ("tiktok", ("T", "x", [f"t{n}" for n in range(31)]), "invalid_tags"),
            ("facebook", ("T", "x", [], "unlisted"), "invalid_privacy"),
            ("facebook", ("T", "x" * 5001, []), "invalid_description"),
            ("facebook", ("", "x", []), "invalid_title"),
            ("facebook", ("a\nb", "x", []), "invalid_title"),
            ("facebook", ("T", "bad\x00", []), "invalid_description"),
            ("youtube", ("T", "<b>", []), "invalid_description"),
            ("myspace", ("T", "x", []), "invalid_channel"),
        ]
        for channel, args, code in cases:
            with self.subTest(channel=channel, code=code), self.assertRaises(MetadataError) as caught:
                validate_channel_metadata(channel, *args)
            self.assertEqual(caught.exception.code, code)
        # Facebook allows < and > (YouTube does not); emoji count as two UTF-16 units like on the platforms.
        validate_channel_metadata("facebook", "T", "a <3 b", [], "public")
        with self.assertRaises(MetadataError):
            validate_channel_metadata("tiktok", "T", "😀" * 1101, [], "private")

    def test_prefilled_values_always_fit(self):
        fitted = fit_social("tiktok", "<Tiêu đề>", "x" * 3000, ["rừng đêm", "#cú", "cú", *[f"t{n}" for n in range(40)]])
        validate_channel_metadata("tiktok", fitted["title"], fitted["description"], fitted["tags"], fitted["privacy_status"])
        self.assertEqual(fitted["tags"][:2], ["rừngđêm", "cú"])
        self.assertLessEqual(len(social_text(fitted["description"], fitted["tags"])), 2200)
        self.assertEqual(social_text("Mô tả", ["a", "b"]), "Mô tả\n\n#a #b")
        platforms = platform_metadata({"title": "Rừng", "description": "Mô tả dài", "tags": ["rừng"],
                                       "privacy_status": "public"}, tiktok_caption="Xem đến cuối!")
        self.assertEqual(platforms["youtube"]["privacy_status"], "public")
        self.assertEqual((platforms["tiktok"]["description"], platforms["facebook"]["description"]),
                         ("Xem đến cuối!", "Mô tả dài"))
        self.assertEqual(platforms["facebook"]["privacy_status"], "private")  # a draft unless the owner chooses

    def test_metadata_step_reply_feeds_every_channel(self):
        reply = '{"title": "Rừng", "description": "Mô tả", "tags": ["rừng"], "tiktok_caption": "Cap", "facebook_description": "FB"}'
        platforms = parse_platforms(reply, {"title": "Rừng", "description": "Mô tả", "tags": ["rừng"]})
        self.assertEqual((platforms["tiktok"]["description"], platforms["facebook"]["description"]), ("Cap", "FB"))
        port = _publish_metadata({"metadata": {"title": "Rừng"}, "platforms": platforms})
        self.assertEqual((port["title"], set(port["platforms"])), ("Rừng", {"youtube", "tiktok", "facebook"}))
        self.assertEqual(_publish_metadata({"metadata": {"title": "Old"}}), {"title": "Old"})  # older outputs

    def test_schedule_times(self):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        self.assertIsNone(schedule_time(None, now))
        self.assertIsNone(schedule_time(now - timedelta(hours=1), now))  # already passed: now
        self.assertEqual(schedule_time(datetime(2026, 10, 2, 9, 0), now), datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))
        later = datetime(2026, 10, 2, 16, 0, tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(schedule_time(later, now), datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))
        with self.assertRaises(MetadataError):
            schedule_time(now + timedelta(days=366), now)


class PublishHandoffTest(unittest.TestCase):
    def handoff(self, config, prepared):
        asset = SimpleNamespace(id="asset-1", filename="final.mp4", step_id="step-render")
        context = SimpleNamespace(db=SimpleNamespace(get=lambda model, key: SimpleNamespace(node_type="render")),
                                  run=SimpleNamespace(id="run-1"), project=SimpleNamespace(title="Dự án"))
        inputs = NodeInputs(config=config, values={"metadata": prepared} if prepared else {})
        with patch("app.workflow.nodes.publish.final_video", return_value=asset):
            return PublishNodeHandler().execute(context, {"id": "publish"}, inputs).output

    def test_typed_values_win_then_metadata_then_youtube(self):
        prepared = {"title": "Tiêu đề AI", "description": "Mô tả AI", "tags": ["ai"],
                    "platforms": {"tiktok": {"description": "Caption AI"}, "facebook": {"description": "FB AI"}}}
        output = self.handoff({"title": "Tiêu đề của tôi", "tiktok_caption": "Caption của tôi"}, prepared)
        self.assertEqual(output["metadata"]["title"], "Tiêu đề của tôi")
        self.assertEqual(output["platforms"]["tiktok"]["description"], "Caption của tôi")
        self.assertEqual(output["platforms"]["facebook"]["description"], "FB AI")
        self.assertEqual((output["sources"]["title"], output["sources"]["tiktok"], output["sources"]["facebook"]),
                         ("setting", "setting", "metadata"))
        # The YouTube hand-off keeps its Phase 9 shape.
        self.assertEqual(set(output["metadata"]), {"title", "description", "tags", "privacy_status"})
        plain = self.handoff({}, None)
        self.assertEqual((plain["metadata"]["title"], plain["platforms"]["tiktok"]["description"],
                          plain["sources"]["facebook"]), ("Dự án", "Dự án", "youtube"))


if __name__ == "__main__":
    unittest.main()
