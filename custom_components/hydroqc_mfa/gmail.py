"""Opérations IMAP Gmail synchrones et bornées ; à appeler depuis l'executor de HA."""

from __future__ import annotations

import imaplib
import re
import ssl
import unicodedata
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser

from .const import NETWORK_TIMEOUT

SENDER = "hydro-quebec@communications.hydro.qc.ca"
SUBJECT = "Votre code de vérification pour votre identifiant Hydro-Québec"
MAX_MESSAGE_BYTES = 262144
MAX_CANDIDATES = 50


class GmailError(Exception):
    """Porte une raison fixe (clé de REASON_MESSAGES), jamais de contenu serveur.

    `detail` dit à quelle étape et avec quel type d'exception l'échec a eu lieu, pour le
    journal : jamais le message de l'exception ni une réponse du serveur, qui pourraient
    contenir l'adresse, le mot de passe ou un code.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _kind(err: BaseException) -> str:
    """Nom qualifié du type d'exception (ex. `socket.gaierror`), sans son message."""
    kind = type(err)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


@dataclass(frozen=True)
class GmailCode:
    """Reçu en mémoire ; le code ne doit pas apparaître dans la représentation."""

    uid: int
    validity: bytes
    code: str = field(repr=False)


@dataclass(frozen=True)
class HqMailSurvey:
    """Courriels de code Hydro-Québec présents dans INBOX (métadonnées seulement)."""

    hq_messages: int
    hq_last_received: datetime | None


_MONTHS = {
    name: index
    for index, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
_INTERNALDATE = re.compile(
    rb'INTERNALDATE "\s?(\d{1,2})-([A-Za-z]{3})-(\d{4}) '
    rb'(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})"'
)


def _internaldate(meta: bytes) -> datetime | None:
    """Lit INTERNALDATE sans dépendre de la locale (contrairement à strptime %b)."""
    found = _INTERNALDATE.search(meta)
    if found is None:
        return None
    day, month, year, hour, minute, second, sign, off_h, off_m = found.groups()
    if month.decode().title() not in _MONTHS:
        return None
    offset = timedelta(hours=int(off_h), minutes=int(off_m))
    return datetime(
        int(year),
        _MONTHS[month.decode().title()],
        int(day),
        int(hour),
        int(minute),
        int(second),
        tzinfo=timezone(offset if sign == b"+" else -offset),
    )


class _VisibleText(HTMLParser):
    """Extrait le texte visible, sans attributs, scripts ni styles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._hidden += 1
        elif tag in ("br", "p", "div", "td"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head"):
            self._hidden = max(0, self._hidden - 1)
        self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).replace("’", "'").casefold().split())


def extract_code(raw: bytes) -> str | None:
    """Accepte uniquement l'expéditeur/sujet attendus et un seul code à six chiffres."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
        assert isinstance(message, EmailMessage)
        if len(message.get_all("From", [])) != 1 or len(message.get_all("Subject", [])) != 1:
            return None
        addresses = getaddresses([str(message["From"])])
        if len(addresses) != 1 or addresses[0][1].casefold() != SENDER:
            return None
        if _normalize(str(message["Subject"])) != _normalize(SUBJECT):
            return None
        codes: set[str] = set()
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() not in ("text/plain", "text/html"):
                continue
            text = part.get_content()
            if not isinstance(text, str):
                continue
            if part.get_content_type() == "text/html":
                parser = _VisibleText()
                parser.feed(text)
                text = "".join(parser.parts)
            codes.update(re.findall(r"(?<!\w)[0-9]{6}(?!\w)", text))
    except (ValueError, LookupError, UnicodeError, AssertionError) as err:
        raise GmailError("gmail_unavailable", f"lecture du message : {_kind(err)}") from None
    if len(codes) > 1:
        raise GmailError("ambiguous")
    return next(iter(codes), None)


class GmailInbox:
    """Une connexion IMAP courte, utilisée en série pendant un défi MFA."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._client: imaplib.IMAP4_SSL | None = None
        self._validity = b""
        self._minimum_uid = 0
        self._examined: set[int] = set()

    def connect(self) -> None:
        """S'authentifie avec un mot de passe d'application via TLS vérifié."""
        try:
            self._client = imaplib.IMAP4_SSL(
                "imap.gmail.com",
                993,
                ssl_context=ssl.create_default_context(),
                timeout=NETWORK_TIMEOUT,
            )
            status, _ = self._client.login(self._username, self._password)
            if status != "OK":
                raise GmailError("gmail_auth_failed", "connexion : identifiants refusés")
        except GmailError:
            self.close()
            raise
        except (imaplib.IMAP4.error, ValueError) as err:
            # ValueError couvre UnicodeEncodeError : imaplib encode en ASCII, un identifiant
            # accentué est refusé localement et le message cite le caractère fautif.
            self.close()
            raise GmailError("gmail_auth_failed", f"connexion : {_kind(err)}") from None
        except (OSError, TimeoutError) as err:
            self.close()
            raise GmailError("gmail_unavailable", f"connexion : {_kind(err)}") from None

    def checkpoint(self) -> None:
        """Mémorise UIDVALIDITY/UIDNEXT avant de demander un nouveau code."""
        client = self._connection()
        try:
            self._check(client.select("INBOX", readonly=True)[0])
            validity = client.response("UIDVALIDITY")[1]
            next_uid = client.response("UIDNEXT")[1]
            if not validity or not validity[0] or not next_uid or not next_uid[0]:
                raise GmailError(
                    "gmail_unavailable", "point de départ : UIDVALIDITY ou UIDNEXT absent"
                )
            self._validity = validity[0]
            self._minimum_uid = int(next_uid[0])
            self._examined.clear()
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError) as err:
            raise GmailError("gmail_unavailable", f"point de départ : {_kind(err)}") from None

    def poll(self) -> GmailCode | None:
        """Lit uniquement les nouveaux messages candidats, même déjà marqués lus."""
        client = self._connection()
        try:
            self._check(client.noop()[0])
            status, data = client.uid(
                "SEARCH", "UID", f"{self._minimum_uid}:*", "FROM", f'"{SENDER}"'
            )
            self._check(status)
            # `n:*` renvoie toujours le dernier UID, même inférieur à n.
            uids = [
                int(value)
                for value in (data[0] or b"").split()
                if int(value) >= self._minimum_uid and int(value) not in self._examined
            ]
            if len(uids) > MAX_CANDIDATES:
                raise GmailError("gmail_unavailable", "lecture : trop de messages candidats")
            matches: list[GmailCode] = []
            for uid in uids:
                self._examined.add(uid)
                status, fetched = client.uid(
                    "FETCH", str(uid), f"(BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)"
                )
                self._check(status)
                for item in fetched:
                    if not isinstance(item, tuple):
                        continue
                    raw = item[1]
                    if len(raw) > MAX_MESSAGE_BYTES:
                        raise GmailError("gmail_unavailable", "lecture : message trop volumineux")
                    code = extract_code(raw)
                    if code:
                        matches.append(GmailCode(uid, self._validity, code))
            if len({match.code for match in matches}) > 1:
                raise GmailError("ambiguous")
            return matches[-1] if matches else None
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError, IndexError) as err:
            raise GmailError("gmail_unavailable", f"lecture : {_kind(err)}") from None

    def trash(self, receipt: GmailCode) -> None:
        """Met à la corbeille exactement le message accepté ; jamais d'EXPUNGE."""
        client = self._connection()
        try:
            self._check(client.select("INBOX", readonly=False)[0])
            validity = client.response("UIDVALIDITY")[1]
            if not validity or validity[0] != receipt.validity:
                raise GmailError("gmail_unavailable", "corbeille : UIDVALIDITY changé")
            # Libellé système Gmail, indépendant de la langue : ni DELETE ni EXPUNGE.
            status, _ = client.uid("STORE", str(receipt.uid), "+X-GM-LABELS", r"(\Trash)")
            self._check(status)
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError) as err:
            raise GmailError("gmail_unavailable", f"corbeille : {_kind(err)}") from None

    def survey(self) -> HqMailSurvey:
        """Compte les courriels de code dans INBOX ; ne lit que sujet et date de réception.

        Limité aux `MAX_CANDIDATES` plus récents de l'expéditeur. `BODY.PEEK` : rien n'est
        marqué comme lu ; aucun corps n'est téléchargé, aucun code n'est extrait.
        """
        client = self._connection()
        try:
            status, data = client.uid("SEARCH", "UID", "1:*", "FROM", f'"{SENDER}"')
            self._check(status)
            uids = sorted({int(value) for value in (data[0] or b"").split()})[-MAX_CANDIDATES:]
            count = 0
            latest: datetime | None = None
            for uid in uids:
                status, fetched = client.uid(
                    "FETCH", str(uid), "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (SUBJECT)])"
                )
                self._check(status)
                for item in fetched:
                    if not isinstance(item, tuple):
                        continue
                    header = BytesParser(policy=policy.default).parsebytes(
                        item[1], headersonly=True
                    )
                    if _normalize(str(header["Subject"] or "")) != _normalize(SUBJECT):
                        continue
                    count += 1
                    received = _internaldate(item[0])
                    if received is not None and (latest is None or received > latest):
                        latest = received
            return HqMailSurvey(count, latest)
        except GmailError:
            raise
        except (imaplib.IMAP4.error, OSError, ValueError, TypeError, IndexError) as err:
            raise GmailError("gmail_unavailable", f"recherche : {_kind(err)}") from None

    def close(self) -> None:
        """Ferme sans EXPUNGE ; n'inclut jamais de réponse serveur dans les erreurs."""
        client, self._client = self._client, None
        if client is not None:
            with suppress(imaplib.IMAP4.error, OSError):
                client.logout()

    def _connection(self) -> imaplib.IMAP4_SSL:
        if self._client is None:
            raise GmailError("gmail_unavailable", "connexion IMAP absente")
        return self._client

    @staticmethod
    def _check(status: str) -> None:
        if status != "OK":
            raise GmailError("gmail_unavailable", "connexion IMAP absente")


def validate_gmail(username: str, password: str) -> None:
    """Teste les identifiants et INBOX sans lire de message ni demander de code."""
    inbox = GmailInbox(username, password)
    try:
        inbox.connect()
        inbox.checkpoint()
    finally:
        inbox.close()


def survey_gmail(username: str, password: str) -> HqMailSurvey:
    """Comme `validate_gmail`, puis compte les courriels de code Hydro-Québec dans INBOX."""
    inbox = GmailInbox(username, password)
    try:
        inbox.connect()
        inbox.checkpoint()
        return inbox.survey()
    finally:
        inbox.close()
