"""
mailer.py — sends one-time verification / password-reset codes by email.

Providers (first one configured wins):
  1. Resend   (HTTPS API)  KRAY_RESEND_API_KEY + KRAY_MAIL_FROM
  2. Brevo    (HTTPS API)  KRAY_BREVO_API_KEY  + KRAY_MAIL_FROM
  3. SMTP                  KRAY_SMTP_HOST, KRAY_SMTP_PORT, KRAY_SMTP_USER,
                           KRAY_SMTP_PASSWORD, KRAY_SMTP_FROM, KRAY_SMTP_USE_TLS

Render's free web services block outgoing SMTP ports (25/465/587), so SMTP
only works on a paid Render plan. Resend/Brevo use normal HTTPS (port 443)
and work on the free plan.

KRAY_MAIL_FROM looks like:  K-Ray Enterprise <noreply@yourdomain.com>

If nothing is configured — or sending fails — the backend falls back to
"demo mode": the code is returned to the app so nobody is ever locked out,
and the reason for the failure is returned as `mail_error` so it is visible.
"""
import os
import re
import json
import smtplib
import threading
import urllib.request
import urllib.error
import base64
from email.mime.text import MIMEText

RESEND_API_KEY = os.environ.get("KRAY_RESEND_API_KEY", "").strip()
BREVO_API_KEY = os.environ.get("KRAY_BREVO_API_KEY", "").strip()
MAIL_FROM = os.environ.get("KRAY_MAIL_FROM", "").strip()

SMTP_HOST = os.environ.get("KRAY_SMTP_HOST")
SMTP_PORT = int(os.environ.get("KRAY_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("KRAY_SMTP_USER")
SMTP_PASSWORD = os.environ.get("KRAY_SMTP_PASSWORD")
SMTP_FROM = os.environ.get("KRAY_SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = os.environ.get("KRAY_SMTP_USE_TLS", "true").lower() != "false"

HAS_SMTP = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)
DEMO_MODE = not (RESEND_API_KEY or BREVO_API_KEY or HAS_SMTP)

_local = threading.local()


def last_error():
    """Why the most recent send on this thread failed (or None)."""
    return getattr(_local, "error", None)


def _compose(purpose, code):
    if purpose == "password_reset":
        subject = "Reset your K-Ray Enterprise password"
        body = (f"Your password reset code is: {code}\n\n"
                f"Enter it in the app to choose a new password. It expires in 5 minutes. "
                f"If you did not ask for this, you can ignore this email.")
    else:
        subject = "Your K-Ray Enterprise verification code"
        body = (f"Your verification code is: {code}\n\n"
                f"Enter it in the app to confirm your email. It expires in 5 minutes.")
    return subject, body


def _http_json(url, headers, payload, timeout=8):
    headers = dict(headers)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("User-Agent", "kray-enterprise/1.0")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = ""
        raise RuntimeError(f"HTTP {e.code}: {detail[:300]}")


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _send_resend(to_email, subject, body, html=None, attachments=None):
    payload = {"from": MAIL_FROM, "to": [to_email], "subject": subject, "text": body}
    if html:
        payload["html"] = html
    if attachments:
        payload["attachments"] = [{"filename": n, "content": _b64(d)} for n, d in attachments]
    _http_json("https://api.resend.com/emails",
               {"Authorization": "Bearer " + RESEND_API_KEY}, payload, timeout=30 if attachments else 8)


def _send_brevo(to_email, subject, body, html=None, attachments=None):
    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", MAIL_FROM)
    name, email = (m.group(1).strip('" '), m.group(2)) if m else ("K-Ray Enterprise", MAIL_FROM)
    payload = {"sender": {"name": name or "K-Ray Enterprise", "email": email},
               "to": [{"email": to_email}], "subject": subject, "textContent": body}
    if html:
        payload["htmlContent"] = html
    if attachments:
        payload["attachment"] = [{"name": n, "content": _b64(d)} for n, d in attachments]
    _http_json("https://api.brevo.com/v3/smtp/email",
               {"api-key": BREVO_API_KEY, "accept": "application/json"}, payload, timeout=30 if attachments else 8)


def _send_smtp(to_email, subject, body, html=None, attachments=None):
    from email.mime.multipart import MIMEMultipart
    from email.mime.application import MIMEApplication
    if attachments:
        msg = MIMEMultipart("mixed")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain"))
        if html:
            alt.attach(MIMEText(html, "html"))
        msg.attach(alt)
        for n, d in attachments:
            part = MIMEApplication(d, Name=n)
            part["Content-Disposition"] = f'attachment; filename="{n}"'
            msg.attach(part)
    elif html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain"))
        msg.attach(MIMEText(html, "html"))
    else:
        msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30 if attachments else 8) as server:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to_email], msg.as_string())


def send_email(to_email: str, subject: str, body: str, html: str = None, attachments=None) -> bool:
    """Send one email through the configured provider. True on success; on failure see last_error()."""
    _local.error = None
    if DEMO_MODE:
        _local.error = ("No email service is configured on the server "
                        "(set KRAY_RESEND_API_KEY and KRAY_MAIL_FROM).")
        return False
    try:
        if RESEND_API_KEY:
            if not MAIL_FROM:
                raise RuntimeError("KRAY_MAIL_FROM is not set.")
            _send_resend(to_email, subject, body, html, attachments)
        elif BREVO_API_KEY:
            if not MAIL_FROM:
                raise RuntimeError("KRAY_MAIL_FROM is not set.")
            _send_brevo(to_email, subject, body, html, attachments)
        else:
            _send_smtp(to_email, subject, body, html, attachments)
        return True
    except Exception as e:
        _local.error = str(e)[:400]
        print(f"[MAILER ERROR] Could not send email to {to_email}: {e}")
        return False


def send_verification_code(to_email: str, code: str, purpose: str = "signup") -> bool:
    """
    Returns True if a real email was sent. Returns False in demo mode or if
    sending failed (the reason is available from last_error()); the caller
    then shows the code on screen instead of leaving the person stuck.
    """
    if DEMO_MODE:
        print(f"[DEMO MODE] Verification code for {to_email}: {code}")
    subject, body = _compose(purpose, code)
    return send_email(to_email, subject, body)
