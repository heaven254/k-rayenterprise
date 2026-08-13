"""
mailer.py — sends the one-time email verification code used at signup.

If SMTP environment variables are not configured, the backend runs in
"demo mode": the code is printed to the server console and also returned
directly in the API response, so the whole flow is testable with zero
external setup.

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


def send_verification_code(to_email: str, code: str, purpose: str = "signup") -> bool:
    """
    Sends the email verification code. Returns True if a real email was
    sent, False if running in demo mode or if sending failed for any
    reason (caller should surface the code directly in that case instead
    of leaving the person stuck).
    """
    if DEMO_MODE:
        print(f"[DEMO MODE] Verification code for {to_email}: {code}")
        return False

    subject = "Verify your email for K-Ray Enterprise"
    body = (
        f"Welcome to K-Ray Enterprise! Your verification code is: {code}\n\n"
        f"Enter this code to confirm your email and finish setting up "
        f"your account. This code expires in 5 minutes."
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=8) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        return True
    except Exception as e:
        # Never let a slow/unreachable mail server break signup/login.
        # Fall back to surfacing the code directly instead of hanging
        # the request until Render's proxy times it out.
        print(f"[MAILER ERROR] Could not send verification code to {to_email}: {e}")
        return False
