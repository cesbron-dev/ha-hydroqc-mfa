"""Faux serveur IMAP Gmail pour les tests (remplace `imaplib.IMAP4_SSL`)."""

from __future__ import annotations

import imaplib
from email.message import EmailMessage

SENDER = "hydro-quebec@communications.hydro.qc.ca"
SUBJECT = "Votre code de vérification pour votre identifiant Hydro-Québec"
PASSWORD = "app-password-secret"


def hq_message(
    code: str,
    *,
    sender: str = SENDER,
    subject: str = SUBJECT,
    html: bool = False,
    body: str | None = None,
) -> bytes:
    """Construit un courriel de code Hydro-Québec."""
    message = EmailMessage()
    message["From"] = f"Hydro-Québec <{sender}>"
    message["To"] = "olivier@example.com"
    message["Subject"] = subject
    if body is None:
        body = (
            f"<html><body><p>Votre code est <b>{code}</b></p></body></html>"
            if html
            else f"Votre code de vérification est {code}."
        )
    message.set_content(body, subtype="html" if html else "plain")
    return message.as_bytes()


class FakeImap:
    """Imite la partie d'IMAP4_SSL utilisée par `GmailInbox`."""

    def __init__(
        self,
        messages: list[tuple[int, bytes]] | None = None,
        *,
        uidvalidity: int = 1,
        uidnext: int | None = None,
        password: str = PASSWORD,
    ) -> None:
        self.messages: list[tuple[int, bytes]] = list(messages or [])
        self.uidvalidity = uidvalidity
        self.uidnext = (
            uidnext if uidnext is not None else max((u for u, _ in self.messages), default=0) + 1
        )
        self.password = password
        self.commands: list[str] = []
        self.logged_out = False
        self.connect_args: tuple = ()
        self.connect_kwargs: dict = {}
        # Date de réception IMAP (INTERNALDATE) par UID ; valeur par défaut sinon.
        self.dates: dict[int, str] = {}

    def __call__(self, *args, **kwargs) -> FakeImap:
        """Sert de fabrique : `patch(..., new=fake)` appelle l'instance."""
        self.connect_args = args
        self.connect_kwargs = kwargs
        return self

    def add(self, raw: bytes) -> int:
        uid = self.uidnext
        self.messages.append((uid, raw))
        self.uidnext += 1
        return uid

    def login(self, user: str, password: str):
        self.commands.append("LOGIN")
        # Comme imaplib : les arguments sont encodés en ASCII avant l'envoi.
        user.encode("ascii")
        password.encode("ascii")
        if password != self.password:
            raise imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] {password} refused")
        return "OK", [b"logged in"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False):
        self.commands.append(f"SELECT {mailbox} readonly={readonly}")
        return "OK", [str(len(self.messages)).encode()]

    def response(self, code: str):
        values = {"UIDVALIDITY": self.uidvalidity, "UIDNEXT": self.uidnext}
        return code, [str(values[code]).encode()]

    def noop(self):
        self.commands.append("NOOP")
        return "OK", [b"done"]

    def uid(self, command: str, *args: str):
        self.commands.append(f"{command} {' '.join(args)}")
        if command == "SEARCH":
            # `n:*` inclut toujours le dernier UID, même inférieur à n (comportement IMAP).
            low = int(args[1].split(":")[0])
            uids = [u for u, _ in self.messages]
            hits = [u for u in uids if u >= low]
            if not hits and uids:
                hits = [max(uids)]
            return "OK", [b" ".join(str(u).encode() for u in hits)]
        if command == "FETCH":
            if "HEADER.FIELDS" in args[1]:
                wanted = {int(value) for value in args[0].split(",")}
                items: list = []
                for candidate, raw in self.messages:
                    if candidate in wanted:
                        items.extend(self._header_item(candidate, raw))
                return "OK", items or [None]
            uid = int(args[0])
            for candidate, raw in self.messages:
                if candidate == uid:
                    meta = f"{uid} (UID {uid} BODY[]<0> {{{len(raw)}}}".encode()
                    return "OK", [(meta, raw), b")"]
            return "OK", [None]
        if command == "STORE":
            return "OK", [b"stored"]
        return "BAD", [b"unsupported"]

    def _header_item(self, uid: int, raw: bytes) -> list:
        """Réponse FETCH (INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT)]) d'un message."""
        header = raw.split(b"\n\n", 1)[0].split(b"\r\n\r\n", 1)[0]
        # Garde l'en-tête Subject brut, lignes de continuation comprises (RFC 5322).
        subject: list[bytes] = []
        for line in header.splitlines():
            if line.lower().startswith(b"subject:") or (subject and line[:1] in (b" ", b"\t")):
                subject.append(line)
            elif subject:
                break
        data = b"\r\n".join(subject) + b"\r\n\r\n"
        date = self.dates.get(uid, "08-Nov-2026 14:00:54 -0500")
        meta = (
            f'{uid} (UID {uid} INTERNALDATE "{date}" '
            f"BODY[HEADER.FIELDS (SUBJECT)] {{{len(data)}}}"
        ).encode()
        return [(meta, data), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b"bye"]
