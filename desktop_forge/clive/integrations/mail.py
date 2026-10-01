"""Email over IMAP and SMTP, one App Access entry per connected account.

Google accounts sign in through GNOME Online Accounts (OAuth; CLIVE stores no
password). Other accounts use an app password kept in GNOME Keyring. Messages
are read with BODY.PEEK, so reading never marks them read. Deleting moves to
Trash; sending always shows the whole message and asks first.

A message is named by an opaque ID -- folder, UIDVALIDITY and UID -- that the
search results hand back.
"""
from __future__ import annotations

import base64
import datetime
import email
import email.policy
import email.utils
import html
import imaplib
import re
import smtplib
import ssl
import threading
import uuid
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path

from ... import config
from .base import Capability, Guards, Integration, Tool
from .schemas import STRING, TEXT

ACCOUNTS_PATH = Path(config.CONFIG_DIR) / "clive-mail.json"
PREFIX = "mail:"
TIMEOUT = 25
MAX_RESULTS = 25
MAX_BODY = 20_000
SNIPPET = 240
HEADER_FIELDS = "FROM TO CC SUBJECT DATE MESSAGE-ID IN-REPLY-TO REFERENCES LIST-UNSUBSCRIBE"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_lock = threading.RLock()

CAPABILITIES = (
    Capability("read", "Read and summarize emails", "read", "low"),
    Capability("search", "Search emails", "search", "low"),
    Capability("followup", "Track replies and follow-ups", "monitor", "low",
               "Find messages waiting on you, and yours still waiting on others."),
    Capability("organize", "Organize and label emails", "modify", "normal",
               "Mark read or unread, flag, label and move."),
    Capability("archive", "Archive emails", "modify", "normal"),
    Capability("draft", "Draft replies", "create", "normal",
               "Saved to your Drafts folder; nothing is sent."),
    Capability("send", "Send emails", "execute", "high",
               "Always shows the recipients, subject and text and asks first."),
    Capability("delete", "Delete emails", "delete", "high",
               "Moves them to Trash; always asks first."),
)
PROVIDER_GUARDS = {
    "gmail": (("mail.google.com",), ("Gmail",)),
    "outlook": (("outlook.live.com", "outlook.office.com", "outlook.office365.com"), ("Outlook",)),
    "yahoo": (("mail.yahoo.com",), ("Yahoo Mail",)),
}


# -- the accounts file ---------------------------------------------------

def load_accounts() -> list[dict]:
    raw = config.read_json(str(ACCOUNTS_PATH)) or {}
    accounts = raw.get("accounts") if isinstance(raw, dict) else None
    return [a for a in accounts or [] if isinstance(a, dict) and isinstance(a.get("id"), str)]


def _save_accounts(accounts: list[dict]) -> None:
    global _integrations_cache
    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.write_json(str(ACCOUNTS_PATH), {"version": 1, "accounts": accounts})
    ACCOUNTS_PATH.chmod(0o600)
    # Timestamps are coarse: never trust the cache across our own write.
    _integrations_cache = None


def provider_of(account: dict) -> str:
    host = (account.get("imap_host") or "").casefold()
    address = (account.get("address") or "").casefold()
    if "gmail" in host or address.endswith(("@gmail.com", "@googlemail.com")):
        return "gmail"
    if "outlook" in host or "office365" in host or address.endswith(("@outlook.com", "@hotmail.com", "@live.com")):
        return "outlook"
    if "yahoo" in host:
        return "yahoo"
    return "imap"


def _secret_schema():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    return Secret, Secret.Schema.new("org.jrf.DesktopForge.Clive", Secret.SchemaFlags.NONE,
                                     {"provider": Secret.SchemaAttributeType.STRING})


def _store_password(account_id: str, password: str) -> None:
    Secret, schema = _secret_schema()
    if not Secret.password_store_sync(schema, {"provider": PREFIX + account_id}, Secret.COLLECTION_DEFAULT,
                                      "CLIVE — email account", password, None):
        raise RuntimeError("GNOME Keyring could not save the password")


def _password(account_id: str) -> str:
    Secret, schema = _secret_schema()
    return Secret.password_lookup_sync(schema, {"provider": PREFIX + account_id}, None) or ""


def _clear_password(account_id: str) -> None:
    try:
        Secret, schema = _secret_schema()
        Secret.password_clear_sync(schema, {"provider": PREFIX + account_id}, None)
    except Exception:  # noqa: BLE001 - nothing stored is nothing to clear
        pass


def goa_mail_accounts() -> list[dict]:
    """Mail-enabled accounts in GNOME Online Accounts, for the Connect list."""
    try:
        import gi
        gi.require_version("Goa", "1.0")
        from gi.repository import Goa
        client = Goa.Client.new_sync(None)
    except Exception:  # noqa: BLE001 - no GOA, nothing to offer
        return []
    found = []
    for item in client.get_accounts():
        account, mail = item.get_account(), item.get_mail()
        if mail is None or account is None or account.props.mail_disabled:
            continue
        found.append({"goa_id": account.props.id, "provider": account.props.provider_name,
                      "address": mail.props.email_address or account.props.presentation_identity,
                      "oauth": item.get_oauth2_based() is not None})
    return found


def _goa_object(goa_id: str):
    import gi
    gi.require_version("Goa", "1.0")
    from gi.repository import Goa
    client = Goa.Client.new_sync(None)
    for item in client.get_accounts():
        if item.get_account() and item.get_account().props.id == goa_id:
            return item
    raise ValueError("That Online Accounts account is no longer set up")


def add_account(request: dict) -> dict:
    """Connect an account: from Online Accounts, or with an app password."""
    with _lock:
        accounts = load_accounts()
        identifier = uuid.uuid4().hex[:10]
        if request.get("goa_id"):
            item = _goa_object(request["goa_id"])
            mail = item.get_mail()
            account = {
                "id": identifier, "auth": "goa", "goa_id": request["goa_id"],
                "address": mail.props.email_address, "username": mail.props.imap_user_name,
                "imap_host": mail.props.imap_host, "imap_port": 993 if mail.props.imap_use_ssl else 143,
                "smtp_host": mail.props.smtp_host,
                "smtp_port": 465 if mail.props.smtp_use_ssl else 587,
                "smtp_security": "ssl" if mail.props.smtp_use_ssl else "starttls",
            }
        else:
            for key in ("address", "imap_host", "smtp_host", "password"):
                if not isinstance(request.get(key), str) or not request[key].strip():
                    raise ValueError("Enter the address, the IMAP and SMTP servers, and an app password")
            security = request.get("smtp_security", "ssl")
            if security not in ("ssl", "starttls"):
                raise ValueError("Choose SSL or STARTTLS for sending")
            account = {
                "id": identifier, "auth": "password", "address": request["address"].strip(),
                "username": (request.get("username") or request["address"]).strip(),
                "imap_host": request["imap_host"].strip(), "imap_port": int(request.get("imap_port") or 993),
                "smtp_host": request["smtp_host"].strip(),
                "smtp_port": int(request.get("smtp_port") or (465 if security == "ssl" else 587)),
                "smtp_security": security,
            }
            _store_password(identifier, request["password"])
        account["name"] = (request.get("name") or "").strip() or {
            "gmail": "Gmail", "outlook": "Outlook", "yahoo": "Yahoo Mail"}.get(provider_of(account), "Email")
        accounts.append(account)
        _save_accounts(accounts)
        return account


def remove_account(account_id: str) -> None:
    with _lock:
        accounts = [a for a in load_accounts() if a["id"] != account_id]
        _save_accounts(accounts)
        _clear_password(account_id)


def account_for(integration_id: str) -> dict:
    wanted = integration_id.removeprefix(PREFIX)
    for account in load_accounts():
        if account["id"] == wanted:
            return account
    raise ValueError("That email account is not connected")


_integrations_cache: tuple | None = None


def account_integrations() -> list[Integration]:
    """One integration per connected account; rebuilt only when the file changes.

    The registry asks for these on every check, several times per tool call.
    """
    global _integrations_cache
    try:
        status = ACCOUNTS_PATH.stat()
        key = (str(ACCOUNTS_PATH), status.st_mtime_ns, status.st_size)
    except OSError:
        key = (str(ACCOUNTS_PATH), 0, 0)
    if _integrations_cache is not None and _integrations_cache[0] == key:
        return _integrations_cache[1]
    result = _build_integrations()
    _integrations_cache = (key, result)
    return result


def _build_integrations() -> list[Integration]:
    result = []
    for account in load_accounts():
        hosts, markers = PROVIDER_GUARDS.get(provider_of(account), ((), ()))
        result.append(Integration(
            PREFIX + account["id"], account.get("name") or "Email", "communication", "mail-unread-symbolic",
            account.get("address", ""), CAPABILITIES, default_enabled=False, instance_of="mail",
            status=lambda account=account: _status(account),
            # A mail app or the provider's website shows this mailbox too.
            guards=Guards(desktop_ids=("thunderbird", "evolution", "geary"), categories=("Email",),
                          hosts=hosts, title_markers=markers)))
    return result


def _status(account: dict) -> dict:
    if account.get("auth") == "goa":
        try:
            _goa_object(account["goa_id"])
        except Exception:  # noqa: BLE001
            return {"state": "needs_setup", "detail": "Its Online Accounts account was removed"}
        return {"state": "ready", "detail": f"{account.get('address', '')} through Online Accounts"}
    return {"state": "ready", "detail": f"{account.get('address', '')} on {account.get('imap_host', '')}"}


# -- talking to the server ------------------------------------------------

def _oauth_string(user: str, token: str) -> str:
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def _credentials(account: dict) -> tuple[str, str]:
    """("password", secret) or ("oauth", token) for this account, right now."""
    if account.get("auth") == "goa":
        item = _goa_object(account["goa_id"])
        oauth = item.get_oauth2_based()
        if oauth is not None:
            token, _expires = oauth.call_get_access_token_sync(None)
            return "oauth", token
        password = item.get_password_based()
        return "password", password.call_get_password_sync("imap-password", None)
    secret = _password(account["id"])
    if not secret:
        raise ValueError("The password for this account is missing from GNOME Keyring; connect it again")
    return "password", secret


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def encode_id(folder: str, validity: str, uid: str) -> str:
    return base64.urlsafe_b64encode(folder.encode()).decode().rstrip("=") + f".{validity}.{uid}"


def decode_id(identifier: str) -> tuple[str, str, str]:
    try:
        folder, validity, uid = identifier.rsplit(".", 2)
        folder = base64.urlsafe_b64decode(folder + "=" * (-len(folder) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        raise ValueError("That is not a message ID from a mail search") from None
    if not uid.isdigit():
        raise ValueError("That is not a message ID from a mail search")
    return folder, validity, uid


LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def parse_list(lines) -> dict[str, str]:
    """Special-use folders (\\Sent, \\Drafts, \\Trash, \\Archive, \\All) by role."""
    roles = {}
    for line in lines or []:
        if not isinstance(line, bytes):
            continue
        match = LIST_LINE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        name = name[1:-1].replace(b'\\"', b'"') if name.startswith(b'"') else name
        for flag in match.group("flags").split():
            role = flag.decode(errors="ignore").lstrip("\\").casefold()
            if role in ("sent", "drafts", "trash", "archive", "all", "junk") and role not in roles:
                roles[role] = name.decode("utf-8", errors="replace")
    return roles


def parse_fetch(data) -> list[dict]:
    """imaplib's FETCH reply as one dict per message: meta text and body sections."""
    messages, current = [], None
    for item in data or []:
        if isinstance(item, tuple):
            prefix = item[0].decode(errors="replace")
            if re.match(r"\d+ \(", prefix):
                current = {"meta": prefix, "parts": {}}
                messages.append(current)
            elif current is None:
                continue
            else:
                current["meta"] += " " + prefix
            section = re.findall(r"(BODY\[[^\]]*\](?:<\d+>)?) \{\d+\}$", prefix)
            if section:
                current["parts"][section[-1].split("<")[0]] = item[1]
        elif isinstance(item, bytes) and current is not None:
            current["meta"] += " " + item.decode(errors="replace")
    for message in messages:
        meta = message["meta"]
        uid = re.search(r"UID (\d+)", meta)
        flags = re.search(r"FLAGS \(([^)]*)\)", meta)
        labels = re.search(r"X-GM-LABELS \(([^)]*)\)", meta)
        thread = re.search(r"X-GM-THRID (\d+)", meta)
        message.update(uid=uid.group(1) if uid else "", flags=flags.group(1).split() if flags else [],
                       labels=re.findall(r'"[^"]*"|\S+', labels.group(1)) if labels else [],
                       thread=thread.group(1) if thread else "")
    return messages


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks, self.skip = [], 0

    def handle_starttag(self, tag, _attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def html_to_text(markup: str) -> str:
    parser = _Text()
    parser.feed(markup)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape("".join(parser.chunks))).strip()


def message_text(message: email.message.EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    content = part.get_content()
    return html_to_text(content) if part.get_content_type() == "text/html" else content


def summarize_headers(raw: bytes) -> dict:
    parsed = email.message_from_bytes(raw or b"", policy=email.policy.default)
    return {key: str(parsed.get(header, "")) for key, header in (
        ("from", "From"), ("to", "To"), ("cc", "Cc"), ("subject", "Subject"), ("date", "Date"),
        ("message_id", "Message-ID"), ("in_reply_to", "In-Reply-To"), ("references", "References"),
        ("list", "List-Unsubscribe"))}


class Mailbox:
    """One IMAP session, opened for a single tool call and closed after it."""

    def __init__(self, account: dict):
        self.account = account
        kind, secret = _credentials(account)
        self.imap = imaplib.IMAP4_SSL(account["imap_host"], int(account.get("imap_port") or 993),
                                      ssl_context=ssl.create_default_context(), timeout=TIMEOUT)
        user = account.get("username") or account["address"]
        if kind == "oauth":
            self.imap.authenticate("XOAUTH2", lambda _challenge: _oauth_string(user, secret).encode())
        else:
            self.imap.login(user, secret)
        self.gmail = "X-GM-EXT-1" in self.imap.capabilities
        status, lines = self.imap.list()
        self.roles = parse_list(lines) if status == "OK" else {}
        self.selected = None
        self.validity = ""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            self.imap.logout()
        except Exception:  # noqa: BLE001 - the session is over either way
            pass

    def folder(self, role: str, fallback: str | None = None) -> str:
        found = self.roles.get(role) or fallback
        if not found:
            raise ValueError(f"This account has no {role.capitalize()} folder")
        return found

    def default_folder(self) -> str:
        return self.roles.get("all", "INBOX") if self.gmail else "INBOX"

    def select(self, folder: str, write: bool = False) -> None:
        if self.selected == (folder, write):
            return
        status, _data = self.imap.select(quote(folder), readonly=not write)
        if status != "OK":
            raise ValueError(f"The folder {folder} could not be opened")
        validity = self.imap.response("UIDVALIDITY")[1]
        self.validity = validity[0].decode() if validity and validity[0] else ""
        self.selected = (folder, write)

    def locate(self, identifier: str, write: bool = False) -> str:
        folder, validity, uid = decode_id(identifier)
        self.select(folder, write)
        if validity and self.validity and validity != self.validity:
            raise ValueError("That message has changed on the server; search again")
        return uid

    def search(self, criteria: list[str]) -> list[str]:
        status, data = self.imap.uid("SEARCH", *criteria)
        if status != "OK":
            raise ValueError("The mail server refused the search")
        return data[0].decode().split() if data and data[0] else []

    def headers(self, uids: list[str], snippet: bool = True) -> list[dict]:
        if not uids:
            return []
        items = f"(UID FLAGS BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})]"
        if snippet:
            items += f" BODY.PEEK[TEXT]<0.{SNIPPET * 4}>"
        if self.gmail:
            items += " X-GM-LABELS X-GM-THRID"
        status, data = self.imap.uid("FETCH", ",".join(uids), items + ")")
        if status != "OK":
            raise ValueError("The mail server refused to list those messages")
        folder = self.selected[0]
        result = []
        for message in parse_fetch(data):
            header = next((v for k, v in message["parts"].items() if "HEADER" in k), b"")
            fields = summarize_headers(header)
            text = next((v for k, v in message["parts"].items() if k.endswith("[TEXT]")), b"")
            row = {"id": encode_id(folder, self.validity, message["uid"]), **fields,
                   "unread": "\\Seen" not in message["flags"], "flagged": "\\Flagged" in message["flags"],
                   "answered": "\\Answered" in message["flags"]}
            if message["labels"]:
                # IMAP quotes labels and escapes their backslashes: "\\Important".
                row["labels"] = [label.strip('"').replace("\\\\", "\\") for label in message["labels"]]
                row["important"] = "\\Important" in row["labels"]
            if message["thread"]:
                row["thread"] = message["thread"]
            if snippet:
                row["snippet"] = re.sub(r"\s+", " ", text.decode("utf-8", errors="replace"))[:SNIPPET]
            result.append(row)
        return result


def imap_since(days: int) -> str:
    day = datetime.date.today() - datetime.timedelta(days=days)
    return f"{day.day:02d}-{MONTHS[day.month - 1]}-{day.year}"


def search_criteria(gmail: bool, query: str, unread: bool, days: int | None) -> list[str]:
    if gmail:
        raw = query or ""
        if unread:
            raw += " is:unread"
        if days:
            raw += f" newer_than:{days}d"
        return ["X-GM-RAW", quote(raw.strip() or "in:inbox")]
    criteria = []
    if query:
        criteria += ["TEXT", quote(query)]
    if unread:
        criteria.append("UNSEEN")
    if days:
        criteria += ["SINCE", imap_since(days)]
    return criteria or ["ALL"]


def _message_ids(value: str) -> set[str]:
    return set(re.findall(r"<[^>]+>", value or ""))


def followups(inbox: list[dict], sent: list[dict], me: str) -> dict:
    """Who is waiting on whom, from headers alone.

    Waiting on you: messages to you, not from you, that you have not answered
    -- by the \\Answered flag or a reply of yours that references them -- and
    that are not bulk mail. Waiting on them: messages you sent that nothing
    received since references.
    """
    me = me.casefold()
    replied_to = set()
    for message in sent:
        replied_to |= _message_ids(message.get("in_reply_to")) | _message_ids(message.get("references"))
    referenced = set()
    for message in inbox:
        referenced |= _message_ids(message.get("in_reply_to")) | _message_ids(message.get("references"))
    waiting_on_you = [m for m in inbox
                      if me not in (m.get("from") or "").casefold() and not m.get("answered")
                      and not m.get("list") and not (_message_ids(m.get("message_id")) & replied_to)
                      and not re.search(r"no-?reply|do-?not-?reply|notifications?@", m.get("from") or "", re.I)]
    waiting_on_them = [m for m in sent
                       if not (_message_ids(m.get("message_id")) & referenced)
                       and me not in (m.get("to") or "").casefold()]
    rank = lambda m: (not m.get("flagged") and not m.get("important"), m.get("date", ""))  # noqa: E731
    return {"waiting_on_you": sorted(waiting_on_you, key=rank)[:MAX_RESULTS],
            "waiting_on_them": waiting_on_them[:MAX_RESULTS]}


# -- tool handlers --------------------------------------------------------

def _open(a) -> Mailbox:
    return Mailbox(account_for(a["account"]))


def _search(_ctx, a):
    with _open(a) as box:
        box.select(a.get("folder") or box.default_folder())
        uids = box.search(search_criteria(box.gmail, a.get("query", ""), a.get("unread", False),
                                          a.get("since_days")))
        limit = min(int(a.get("limit") or MAX_RESULTS), MAX_RESULTS)
        messages = box.headers(uids[-limit:])
    messages.reverse()
    return {"messages": messages, "total": len(uids), "truncated": len(uids) > limit}


def _read(_ctx, a):
    with _open(a) as box:
        uid = box.locate(a["id"])
        status, data = box.imap.uid("FETCH", uid, "(UID FLAGS BODY.PEEK[])")
        parsed = parse_fetch(data) if status == "OK" else []
        if not parsed or not parsed[0]["parts"]:
            raise ValueError("That message no longer exists")
    raw = next(iter(parsed[0]["parts"].values()))
    message = email.message_from_bytes(raw, policy=email.policy.default)
    text = message_text(message)
    attachments = [{"name": part.get_filename(), "type": part.get_content_type()}
                   for part in message.iter_attachments()]
    return {"id": a["id"], **summarize_headers(raw), "text": text[:MAX_BODY],
            "truncated": len(text) > MAX_BODY, "attachments": attachments}


def _followups(_ctx, a):
    days = min(int(a.get("days") or 14), 90)
    account = account_for(a["account"])
    with Mailbox(account) as box:
        box.select("INBOX")
        inbox = box.headers(box.search(["SINCE", imap_since(days)])[-200:], snippet=True)
        box.select(box.folder("sent", "Sent"))
        sent = box.headers(box.search(["SINCE", imap_since(days)])[-200:], snippet=False)
    return {"days": days, **followups(inbox, sent, account["address"])}


def _organize(_ctx, a):
    action = a["action"]
    with _open(a) as box:
        done = 0
        for identifier in a["ids"]:
            uid = box.locate(identifier, write=True)
            if action in ("mark_read", "mark_unread", "flag", "unflag"):
                flag = "\\Seen" if action.startswith("mark") else "\\Flagged"
                sign = "+" if action in ("mark_read", "flag") else "-"
                box.imap.uid("STORE", uid, f"{sign}FLAGS", f"({flag})")
            elif action in ("label", "unlabel"):
                label = a.get("label")
                if not label:
                    raise ValueError("Name the label")
                sign = "+" if action == "label" else "-"
                if box.gmail:
                    box.imap.uid("STORE", uid, f"{sign}X-GM-LABELS", f"({quote(label)})")
                else:
                    box.imap.uid("STORE", uid, f"{sign}FLAGS", f"({re.sub(r'[^A-Za-z0-9_-]', '_', label)})")
            elif action == "archive":
                if box.gmail:
                    box.imap.uid("STORE", uid, "-X-GM-LABELS", "(\\Inbox)")
                else:
                    _move(box, uid, box.folder("archive", "Archive"))
            elif action == "move":
                if not a.get("folder"):
                    raise ValueError("Name the folder to move to")
                _move(box, uid, a["folder"])
            else:
                raise ValueError("Unknown mail action")
            done += 1
    return {"done": action, "messages": done}


def _move(box: Mailbox, uid: str, folder: str) -> None:
    if "MOVE" in box.imap.capabilities:
        status, _data = box.imap.uid("MOVE", uid, quote(folder))
    else:
        status, _data = box.imap.uid("COPY", uid, quote(folder))
        if status == "OK":
            box.imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
            box.imap.expunge()
    if status != "OK":
        raise ValueError(f"The server would not move the message to {folder}")


def _delete(_ctx, a):
    with _open(a) as box:
        trash = box.folder("trash", "Trash")
        for identifier in a["ids"]:
            _move(box, box.locate(identifier, write=True), trash)
    return {"moved_to_trash": len(a["ids"])}


def _compose(box: Mailbox | None, account: dict, a) -> EmailMessage:
    message = EmailMessage()
    message["From"] = account["address"]
    message["To"] = a["to"]
    if a.get("cc"):
        message["Cc"] = a["cc"]
    subject = a.get("subject") or ""
    if a.get("reply_to_id") and box is not None:
        uid = box.locate(a["reply_to_id"])
        status, data = box.imap.uid("FETCH", uid, f"(BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
        parsed = parse_fetch(data) if status == "OK" else []
        if parsed and parsed[0]["parts"]:
            original = summarize_headers(next(iter(parsed[0]["parts"].values())))
            if original["message_id"]:
                message["In-Reply-To"] = original["message_id"]
                message["References"] = (original["references"] + " " + original["message_id"]).strip()
            if not subject:
                subject = original["subject"]
            if not subject.lower().startswith("re:"):
                subject = "Re: " + subject
    message["Subject"] = subject
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = email.utils.make_msgid(domain=account["address"].rsplit("@", 1)[-1])
    message.set_content(a["body"])
    return message


def _draft(_ctx, a):
    account = account_for(a["account"])
    with Mailbox(account) as box:
        message = _compose(box, account, a)
        drafts = box.folder("drafts", "Drafts")
        status, _data = box.imap.append(quote(drafts), "(\\Draft \\Seen)", imaplib.Time2Internaldate(
            datetime.datetime.now().astimezone()), message.as_bytes())
        if status != "OK":
            raise ValueError("The server would not save the draft")
    return {"drafted": True, "folder": drafts, "to": a["to"], "subject": message["Subject"]}


def _send(_ctx, a):
    account = account_for(a["account"])
    kind, secret = _credentials(account)
    with Mailbox(account) as box:
        message = _compose(box, account, a)
        user = account.get("username") or account["address"]
        host, port = account["smtp_host"], int(account.get("smtp_port") or 465)
        context = ssl.create_default_context()
        if account.get("smtp_security", "ssl") == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, context=context, timeout=TIMEOUT)
        else:
            smtp = smtplib.SMTP(host, port, timeout=TIMEOUT)
            smtp.starttls(context=context)
        with smtp:
            if kind == "oauth":
                smtp.auth("XOAUTH2", lambda _challenge=None: _oauth_string(user, secret))
            else:
                smtp.login(user, secret)
            smtp.send_message(message)
        # Gmail files what it sends by itself; other servers need a copy.
        if not box.gmail and box.roles.get("sent"):
            box.imap.append(quote(box.roles["sent"]), "(\\Seen)", imaplib.Time2Internaldate(
                datetime.datetime.now().astimezone()), message.as_bytes())
    return {"sent": True, "to": a["to"], "subject": message["Subject"]}


def _send_preview(a) -> str:
    lines = [f"From: {account_for(a['account']).get('address', '')}", f"To: {a.get('to', '')}"]
    if a.get("cc"):
        lines.append(f"Cc: {a['cc']}")
    lines.append(f"Subject: {a.get('subject') or '(reply subject)'}")
    lines += ["", a.get("body", "")[:1500]]
    return "\n".join(lines)


def _delete_preview(a) -> str:
    with _open(a) as box:
        subjects = []
        for identifier in a["ids"][:10]:
            box.locate(identifier)
            _folder, _validity, uid = decode_id(identifier)
            for row in box.headers([uid], snippet=False):
                subjects.append(f"“{row['subject']}” from {row['from']}")
    return f"{len(a['ids'])} message(s) to Trash: " + "; ".join(subjects)


def _account(a):
    return a.get("account") or ""


ACCOUNT = {"account": STRING}
IDS = {"type": "array", "items": STRING, "minItems": 1, "maxItems": 50}
ORGANIZE = ("mark_read", "mark_unread", "flag", "unflag", "label", "unlabel", "move", "archive")

TOOLS = (
    Tool("mail_search", "Search an email account. query uses the server's search (Gmail syntax on "
         "Gmail, such as from:john subject:friday). Results carry ids for reading and organizing.",
         {**ACCOUNT, "query": STRING, "folder": STRING, "unread": {"type": "boolean"},
          "since_days": {"type": "integer", "minimum": 1, "maximum": 3650},
          "limit": {"type": "integer", "minimum": 1, "maximum": MAX_RESULTS}},
         "search", _search, _account, "Searching {app}…", required=["account"], family="mail",
         describe=lambda a: a.get("query") or "recent messages"),
    Tool("mail_read", "Read one email by id: headers, text and attachment names.", {**ACCOUNT, "id": STRING},
         "read", _read, _account, "Reading {app}…", family="mail", describe=lambda a: "one message"),
    Tool("mail_followups", "Messages waiting on your reply, and yours still waiting on a reply, "
         "over the last `days` days.", {**ACCOUNT, "days": {"type": "integer", "minimum": 1, "maximum": 90}},
         "followup", _followups, _account, "Checking {app} for follow-ups…", required=["account"],
         family="mail", describe=lambda a: f"the last {a.get('days', 14)} days"),
    Tool("mail_organize", "Mark read/unread, flag, label, move or archive messages by id.",
         {**ACCOUNT, "ids": IDS, "action": {"type": "string", "enum": list(ORGANIZE)}, "label": STRING,
          "folder": STRING}, lambda a: "archive" if a.get("action") == "archive" else "organize",
         _organize, _account, "Organizing {app}…", required=["account", "ids", "action"], family="mail",
         possible=("organize", "archive"),
         describe=lambda a: f"{a['action']} {len(a['ids'])} message(s)"),
    Tool("mail_draft", "Save a draft (a reply when reply_to_id is given). Nothing is sent.",
         {**ACCOUNT, "to": STRING, "cc": STRING, "subject": STRING, "body": TEXT, "reply_to_id": STRING},
         "draft", _draft, _account, "Drafting in {app}…", required=["account", "to", "body"],
         family="mail", describe=lambda a: f"to {a['to']}: {a.get('subject', '')}"),
    Tool("mail_send", "Send an email (a reply when reply_to_id is given).",
         {**ACCOUNT, "to": STRING, "cc": STRING, "subject": STRING, "body": TEXT, "reply_to_id": STRING},
         "send", _send, _account, "Sending from {app}…", required=["account", "to", "body"],
         family="mail", describe=lambda a: f"to {a['to']}: {a.get('subject', '')}", preview=_send_preview),
    Tool("mail_delete", "Move messages to Trash by id.", {**ACCOUNT, "ids": IDS}, "delete", _delete, _account,
         "Deleting in {app}…", family="mail", describe=lambda a: f"{len(a['ids'])} message(s)",
         preview=_delete_preview),
)

__all__ = ["ACCOUNTS_PATH", "CAPABILITIES", "TOOLS", "account_integrations", "add_account",
           "followups", "goa_mail_accounts", "parse_fetch", "parse_list", "remove_account",
           "search_criteria"]
