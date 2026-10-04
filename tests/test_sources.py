"""Phase 10 content sources: URL safety (SSRF), page extraction, documents and subtitles. Offline."""
import socket
import unittest

import httpx

from app import sources
from app.sources import SourceError, check_url, document_source, extract_page, fetch_page, page_source, parse_subtitles


def resolver_for(*addresses):
    def resolve(host, port, type=None):
        return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
                for address in addresses]
    return resolve


ARTICLE = ("<html lang='vi'><head><title>Tiêu đề trang</title><meta property='og:title' content='Rừng đêm'>"
           "<script>alert('x')</script><style>p{}</style></head><body><nav>Menu Trang chủ</nav>"
           "<article><h1>Rừng đêm</h1><p>" + "Con cú bay qua tán cây trong đêm yên tĩnh. " * 8 + "</p></article>"
           "<footer>Bản quyền</footer></body></html>")


class URLSafetyTest(unittest.TestCase):
    def test_blocks_everything_but_public_https(self):
        blocked = ["http://example.com/", "ftp://example.com/", "https://localhost/", "https://127.0.0.1/",
                   "https://10.1.2.3/", "https://172.16.0.1/", "https://192.168.1.1/", "https://169.254.169.254/latest",
                   "https://[::1]/", "https://[fd00::1]/", "https://[fe80::1]/", "https://[::ffff:127.0.0.1]/",
                   "https://0.0.0.0/", "https://metadata.google.internal/", "https://printer.local/",
                   "https://user:pass@example.com/", "https://example.com:8443/", "https://224.0.0.1/",
                   "https://100.100.100.200/"]
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(SourceError) as caught:
                check_url(url)
            self.assertIn(caught.exception.code, ("blocked_url", "invalid_url"))
        self.assertEqual(check_url("https://example.com/a/b?x=1"), ("example.com", "/a/b?x=1"))
        self.assertEqual(check_url("https://Example.com:443"), ("example.com", "/"))

    def test_every_resolved_address_must_be_public(self):
        self.assertEqual(sources.resolve_public("example.com", resolver_for("93.184.216.34")), "93.184.216.34")
        for addresses in (("10.0.0.5",), ("93.184.216.34", "127.0.0.1"), ("::ffff:192.168.0.1",), ("fd12::1",),
                          ("169.254.169.254",)):
            with self.subTest(addresses=addresses), self.assertRaises(SourceError) as caught:
                sources.resolve_public("rebind.example", resolver_for(*addresses))
            self.assertEqual(caught.exception.code, "blocked_url")

        def failing(host, port, type=None):
            raise socket.gaierror("no such host")
        with self.assertRaises(SourceError) as caught:
            sources.resolve_public("missing.example", failing)
        self.assertEqual(caught.exception.code, "unreachable")


class FetchTest(unittest.TestCase):
    def fetch(self, handler, url="https://example.com/post", resolver=None):
        seen = []

        def record(request):
            seen.append(request)
            return handler(request)
        with httpx.Client(transport=httpx.MockTransport(record)) as client:
            return fetch_page(url, client=client, resolver=resolver or resolver_for("93.184.216.34")), seen

    def test_connects_to_the_checked_address_with_the_host_name(self):
        page, seen = self.fetch(lambda request: httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"},
                                                               content=ARTICLE.encode()))
        self.assertEqual(seen[0].url.host, "93.184.216.34")
        self.assertEqual(seen[0].headers["Host"], "example.com")
        self.assertEqual(seen[0].extensions.get("sni_hostname"), "example.com")
        source = page_source(page)
        self.assertEqual((source["source_type"], source["title"], source["language"]), ("url", "Rừng đêm", "vi"))
        self.assertIn("Con cú bay", source["text"])
        self.assertNotIn("alert", source["text"])
        self.assertNotIn("Menu", source["text"])
        self.assertNotIn("Bản quyền", source["text"])
        self.assertEqual(source["source_url"], "https://example.com/post")

    def test_redirects_are_checked_again(self):
        def redirect(target):
            return lambda request: httpx.Response(302, headers={"Location": target})
        with self.assertRaises(SourceError) as caught:
            self.fetch(redirect("https://127.0.0.1/admin"))
        self.assertEqual(caught.exception.code, "blocked_url")
        with self.assertRaises(SourceError) as caught:
            self.fetch(redirect("http://example.com/plain"))
        self.assertEqual(caught.exception.code, "blocked_url")
        with self.assertRaises(SourceError) as caught:
            self.fetch(redirect("https://example.com/again"))
        self.assertEqual(caught.exception.code, "fetch_failed")  # more than MAX_REDIRECTS
        # A redirect to a host that resolves privately (DNS rebinding) is refused too.
        calls = []

        def resolver(host, port, type=None):
            calls.append(host)
            return resolver_for("93.184.216.34" if host == "example.com" else "10.0.0.7")(host, port, type)
        with self.assertRaises(SourceError) as caught:
            self.fetch(redirect("https://internal.example.net/"), resolver=resolver)
        self.assertEqual((caught.exception.code, calls), ("blocked_url", ["example.com", "internal.example.net"]))

    def test_size_type_timeout_and_status_limits(self):
        big = b"x" * (sources.MAX_PAGE_BYTES + 1)
        cases = [
            (lambda r: httpx.Response(200, headers={"Content-Type": "text/html"}, content=big), "too_large"),
            (lambda r: httpx.Response(200, headers={"Content-Type": "text/html",
                                                    "Content-Length": str(10 ** 9)}, content=b"<p>x</p>"), "too_large"),
            (lambda r: httpx.Response(200, headers={"Content-Type": "application/pdf"}, content=b"%PDF"),
             "unsupported_type"),
            (lambda r: httpx.Response(403, headers={"Content-Type": "text/html"}, content=b"denied"), "fetch_failed"),
        ]
        for handler, code in cases:
            with self.subTest(code=code), self.assertRaises(SourceError) as caught:
                self.fetch(handler)
            self.assertEqual(caught.exception.code, code)

        def slow(request):
            raise httpx.ReadTimeout("slow", request=request)
        with self.assertRaises(SourceError) as caught:
            self.fetch(slow)
        self.assertEqual(caught.exception.code, "timeout")

    def test_a_page_without_readable_text_is_refused(self):
        page, _ = self.fetch(lambda r: httpx.Response(200, headers={"Content-Type": "text/html"},
                                                      content=b"<html><body><div id='app'></div><script>x</script></body></html>"))
        with self.assertRaises(SourceError) as caught:
            page_source(page)
        self.assertEqual(caught.exception.code, "no_text")


class DocumentTest(unittest.TestCase):
    def test_srt_and_vtt_keep_their_timestamps(self):
        srt = "1\n00:00:01,500 --> 00:00:03,000\nXin chào\n\n2\n00:01:02,250 --> 00:01:04,000\n<i>Tạm biệt</i>\n"
        self.assertEqual(parse_subtitles(srt), [{"start": 1.5, "end": 3.0, "text": "Xin chào"},
                                                {"start": 62.25, "end": 64.0, "text": "Tạm biệt"}])
        vtt = "WEBVTT\n\n00:05.000 --> 00:07.500 align:start\nHello there\n\n01:00:00.000 --> 01:00:01.000\nLate\n"
        self.assertEqual(parse_subtitles(vtt), [{"start": 5.0, "end": 7.5, "text": "Hello there"},
                                                {"start": 3600.0, "end": 3601.0, "text": "Late"}])
        source = document_source(srt.encode(), "application/x-subrip", title="phim.srt", asset_id="a1")
        self.assertEqual((source["source_type"], source["text"], source["asset_id"]), ("document", "Xin chào Tạm biệt", "a1"))
        self.assertEqual(len(source["segments"]), 2)

    def test_text_documents_must_be_utf8_and_small(self):
        source = document_source("# Tiêu đề\n\nNội dung".encode("utf-8-sig"), "text/markdown", title="a.md", asset_id="a")
        self.assertEqual((source["text"], source["segments"]), ("# Tiêu đề\n\nNội dung", None))
        for data, code in ((b"\x00\x01binary", "not_text"), ("é".encode("latin-1"), "not_text"),
                           (b"x" * (sources.MAX_DOCUMENT_BYTES + 1), "too_large")):
            with self.subTest(code=code), self.assertRaises(SourceError) as caught:
                document_source(data, "text/plain", title="a.txt", asset_id="a")
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(SourceError):
            document_source(b"no cues here", "text/vtt", title="a.vtt", asset_id="a")

    def test_long_text_is_capped(self):
        source = sources.make_source("text", title="  a   b ", text="x" * (sources.MAX_SOURCE_CHARS + 10))
        self.assertEqual((len(source["text"]), source["truncated"], source["title"]),
                         (sources.MAX_SOURCE_CHARS, True, "a b"))

    def test_extracts_the_article_rather_than_the_page_chrome(self):
        title, text, language = extract_page(ARTICLE)
        self.assertEqual((title, language), ("Rừng đêm", "vi"))
        self.assertTrue(text.startswith("Rừng đêm"))


if __name__ == "__main__":
    unittest.main()
