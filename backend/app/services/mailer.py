from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import aiosmtplib

from app.core.config import get_settings


def build_message(
    *,
    from_name: str,
    reply_to: str | None,
    to: str,
    subject: str,
    body: str,
    attachment: Path | None,
    attachment_name: str | None = None,
) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, s.smtp_user))
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to and reply_to.lower() != s.smtp_user.lower():
        msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain=s.smtp_user.split("@")[-1] or None)
    msg.set_content(body)
    if attachment:
        msg.add_attachment(
            attachment.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=attachment_name or attachment.name,
        )
    return msg


async def send(msg: EmailMessage) -> str:
    """Kirim via SMTP (STARTTLS). Kembalikan Message-ID."""
    s = get_settings()
    await aiosmtplib.send(
        msg,
        hostname=s.smtp_host,
        port=s.smtp_port,
        username=s.smtp_user or None,
        password=s.smtp_password.replace(" ", "") or None,  # App Password Google boleh ditulis berspasi
        start_tls=s.smtp_port == 587,
        use_tls=s.smtp_port == 465,
        timeout=60,
    )
    return msg["Message-ID"]
