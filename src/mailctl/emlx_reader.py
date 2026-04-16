"""Read Mail.app ``.emlx`` message files from the on-disk store.

The Envelope Index stores headers and metadata but not the full message
body. Bodies live as ``.emlx`` files under each account's mailbox
directory in ``~/Library/Mail/V10``. This module finds and parses them.

``.emlx`` format:
- First line: the total message byte length (ASCII digits, trailing newline).
- Then the full RFC 822 message (headers + body).
- Then optionally a plist suffix with Apple metadata (ignored here).

We delegate MIME/multipart parsing to Python's stdlib ``email`` module.
"""

from __future__ import annotations

import email
import email.policy
import glob
from pathlib import Path
from urllib.parse import unquote, urlparse


def emlx_candidates(message_rowid: int, mailbox_url: str) -> list[Path]:
    """Return candidate paths for the ``.emlx`` file of *message_rowid*.

    Uses glob rather than reconstructing Mail's sharded directory layout
    because the shard depth varies by how many messages the mailbox has
    accumulated. The glob is scoped by account UUID and mailbox path, so
    it stays fast even on stores with hundreds of mailboxes.
    """
    parsed = urlparse(mailbox_url)
    account_uuid = parsed.netloc
    if "@" in account_uuid:
        account_uuid = account_uuid.split("@", 1)[-1]
    mailbox_path = unquote(parsed.path.lstrip("/"))
    # Each slash in the URL path becomes a ".mbox/" segment on disk.
    # "[Gmail]/All Mail" → "[Gmail].mbox/All Mail.mbox"
    mailbox_on_disk = ".mbox/".join(mailbox_path.split("/")) + ".mbox"
    base = Path.home() / "Library" / "Mail" / "V10" / account_uuid / mailbox_on_disk

    def _find(base_path: Path, suffix: str) -> list[Path]:
        # glob.escape handles brackets like "[Gmail]" that would otherwise be
        # interpreted as a glob character class. The "**" pattern is added
        # after escaping so it keeps its wildcard meaning.
        pattern = glob.escape(str(base_path)) + f"/**/Messages/{message_rowid}.{suffix}"
        return sorted(Path(m) for m in glob.glob(pattern, recursive=True))

    if base.exists():
        matches = _find(base, "emlx")
        if matches:
            return matches
        matches = _find(base, "partial.emlx")
        if matches:
            return matches

    # Fall back to searching across the whole account directory, across
    # all V* versions, for both suffixes. Handles cases where the message
    # has been moved or the mailbox layout is nonstandard.
    for version_dir in sorted((Path.home() / "Library" / "Mail").glob("V*")):
        acct_dir = version_dir / account_uuid
        if not acct_dir.exists():
            continue
        for suffix in ("emlx", "partial.emlx"):
            pattern = glob.escape(str(acct_dir)) + f"/**/Messages/{message_rowid}.{suffix}"
            matches = sorted(Path(m) for m in glob.glob(pattern, recursive=True))
            if matches:
                return matches
    return []


def read_emlx(path: Path) -> email.message.Message:
    """Read an ``.emlx`` file and return the parsed email message.

    The leading byte-count line is stripped before RFC 822 parsing.
    Any trailing Apple plist suffix is left in the body; Python's
    email parser will stop at the message boundary and ignore it.
    """
    data = path.read_bytes()
    # Skip the first line (byte count).
    first_newline = data.find(b"\n")
    if first_newline == -1:
        body_bytes = data
    else:
        body_bytes = data[first_newline + 1:]
    return email.message_from_bytes(body_bytes, policy=email.policy.default)


def extract_body(msg: email.message.Message) -> str:
    """Return the plain-text body of *msg*, preferring ``text/plain``.

    Falls back to stripping HTML if only ``text/html`` is present. Returns
    an empty string if no text content is found.
    """
    if msg.is_multipart():
        # Prefer text/plain; fall back to text/html.
        plain = None
        html = None
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and plain is None:
                try:
                    plain = part.get_content()
                except (LookupError, UnicodeDecodeError):
                    plain = part.get_payload(decode=True)
                    if isinstance(plain, bytes):
                        plain = plain.decode("utf-8", errors="replace")
            elif ctype == "text/html" and html is None:
                try:
                    html = part.get_content()
                except (LookupError, UnicodeDecodeError):
                    html = part.get_payload(decode=True)
                    if isinstance(html, bytes):
                        html = html.decode("utf-8", errors="replace")
        if plain is not None:
            return str(plain)
        if html is not None:
            return _strip_html(str(html))
        return ""
    try:
        content = msg.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = msg.get_payload(decode=True)
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""
    if msg.get_content_type() == "text/html":
        return _strip_html(str(content))
    return str(content)


def _strip_html(html: str) -> str:
    """Extremely cheap HTML → text fallback. Enough for CLI display."""
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace.
    return re.sub(r"\s+\n", "\n", text).strip()
