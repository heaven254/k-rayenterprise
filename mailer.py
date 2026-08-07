"""
mailer.py — sends the admin one-time verification code by email.

If SMTP environment variables are not configured, the backend runs in
"demo mode": the code is printed to the server console and also returned
directly in the API response (mirroring the standalone HTML prototype),
so the whole flow is testable with zero external setup.

To enable real email delivery, set these environment variables:
  KRAY_SMTP_HOST, KRAY_SMTP_PORT, KRAY_SMTP_USER, KRAY_SMTP_PASSWORD,
  KRAY_SMTP_FROM  (defaults to KRAY_SMTP_USER)
  KRAY_SMTP_USE_TLS (default "true")
"""
import os
import smtplib
from email.mime.text import MIMEText

SMTP_HOST = os.environ.get("KRAY_SMTP_HOST")
SMTP_PORT = int(os.environ.get("KRAY_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("KRAY_SMTP_USER")
SMTP_PASSWORD = os.environ.get("KRAY_SMTP_PASSWORD")
SMTP_FROM = os.environ.get("KRAY_SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = os.environ.get("KRAY_SMTP_USE_TLS", "true").lower() != "false"

DEMO_MODE = not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def send_admin_code(to_email: str, code: str) -> bool:
    """
    Sends the verification code. Returns True if a real email was sent,
    False if running in demo mode (caller should surface the code in the
    API response / console instead).
    """
    if DEMO_MODE:
        print(f"[DEMO MODE] Admin verification code for {to_email}: {code}")
        return False

    subject = "Your K-Ray Enterprise Admin verification code"
    body = (
        f"Your one-time Admin verification code is: {code}\n\n"
        f"This code expires in 5 minutes. If you did not request admin "
        f"access, you can safely ignore this email."
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [to_email], msg.as_string())
    return True
