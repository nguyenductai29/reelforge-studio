"""A tiny SMTP server for tests and E2E runs: accepts every message and keeps it (no network beyond loopback).

    python tests/smtp_sink.py --port 2525 --dir /tmp/mails   # E2E: one .eml file per message

In Python tests::

    sink = SMTPSink().start()
    ... point Admin → Email at 127.0.0.1:sink.port with security "none" ...
    sink.messages  # [(mail_from, [rcpt], raw_bytes)]
    sink.stop()

It speaks just enough SMTP for ``smtplib`` (EHLO/HELO, optional AUTH PLAIN/LOGIN, MAIL, RCPT,
DATA, RSET, NOOP, QUIT); no TLS.
"""
import argparse
import base64
from pathlib import Path
import socketserver
import threading
import time


class _Handler(socketserver.StreamRequestHandler):
    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode("ascii"))

    def handle(self):
        sink = self.server.sink
        self._send("220 reelforge-test-sink ESMTP")
        mail_from, rcpt = None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            verb = line.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.wfile.write(b"250-reelforge-test-sink\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n")
            elif verb == "AUTH":
                parts = line.split()
                if len(parts) >= 3 and parts[1].upper() == "PLAIN":
                    decoded = base64.b64decode(parts[2]).split(b"\0")
                    sink.logins.append((decoded[1].decode(), decoded[2].decode()))
                    self._send("235 ok" if decoded[2].decode() != sink.reject_password else "535 bad credentials")
                elif len(parts) >= 2 and parts[1].upper() == "LOGIN":
                    self._send("334 VXNlcm5hbWU6")
                    user = base64.b64decode(self.rfile.readline().strip()).decode()
                    self._send("334 UGFzc3dvcmQ6")
                    password = base64.b64decode(self.rfile.readline().strip()).decode()
                    sink.logins.append((user, password))
                    self._send("235 ok" if password != sink.reject_password else "535 bad credentials")
                else:
                    self._send("504 unsupported")
            elif verb == "MAIL":
                mail_from, rcpt = line.split(":", 1)[1].strip().strip("<>").split(">")[0], []
                self._send("250 ok")
            elif verb == "RCPT":
                address = line.split(":", 1)[1].strip().strip("<>").split(">")[0]
                if address in sink.reject_recipients:
                    self._send("550 no such user")
                else:
                    rcpt.append(address)
                    self._send("250 ok")
            elif verb == "DATA":
                self._send("354 end with .")
                chunks = []
                while True:
                    data = self.rfile.readline()
                    if data in (b".\r\n", b".\n", b""):
                        break
                    chunks.append(data[1:] if data.startswith(b"..") else data)
                sink.store(mail_from, rcpt, b"".join(chunks))
                self._send("250 queued")
            elif verb in ("RSET", "NOOP"):
                self._send("250 ok")
            elif verb == "QUIT":
                self._send("221 bye")
                return
            else:
                self._send("502 not implemented")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class SMTPSink:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, directory: Path | None = None):
        self.messages: list[tuple[str, list[str], bytes]] = []
        self.logins: list[tuple[str, str]] = []
        self.reject_password = None
        self.reject_recipients: set[str] = set()
        self.directory = directory
        self._server = _Server((host, port), _Handler)
        self._server.sink = self
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._lock = threading.Lock()

    def store(self, mail_from, rcpt, raw: bytes) -> None:
        with self._lock:
            self.messages.append((mail_from, list(rcpt), raw))
            if self.directory is not None:
                self.directory.mkdir(parents=True, exist_ok=True)
                (self.directory / f"{time.time_ns()}.eml").write_bytes(raw)

    def start(self) -> "SMTPSink":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMTP sink for ReelForge tests and E2E runs")
    parser.add_argument("--port", type=int, default=2525)
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    sink = SMTPSink(port=args.port, directory=Path(args.dir)).start()
    print(f"smtp sink on 127.0.0.1:{sink.port}, writing to {args.dir}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sink.stop()
