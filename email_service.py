"""
email_service.py — Resend-based transactional email for SurveyQC.

All public functions are fire-and-forget: they log on failure but never raise,
so a broken API key or network blip never blocks the main request flow.

Public API:
    send_welcome_email(to_email, name, plan)
    send_report_ready_email(to_email, name, doc_filename, issues_count, job_id)
    send_password_reset_email(to_email, reset_link)
"""

import logging
import resend

log = logging.getLogger(__name__)

RESEND_API_KEY = "re_5iNwvdGo_Br4csdiDCu9u7VtRtH8AjtpL"
FROM_EMAIL     = "onboarding@resend.dev"
APP_URL        = "https://surveyqc.com"

resend.api_key = RESEND_API_KEY

# ── Internal helper ───────────────────────────────────────────────────────────

def _send(to: str, subject: str, html: str) -> bool:
    """Send one email. Returns True on success, False on any error."""
    try:
        response = resend.Emails.send({
            "from":    FROM_EMAIL,
            "to":      [to],
            "subject": subject,
            "html":    html,
        })
        log.info("email sent  to=%s  subject=%r  response=%s", to, subject, response)
        return True
    except Exception as exc:
        log.error("email failed  to=%s  subject=%r  err=%s", to, subject, exc)
        return False


# ── Shared HTML chrome ────────────────────────────────────────────────────────

def _wrap(body_html: str) -> str:
    """Wrap body content in the standard SurveyQC email shell."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F7F4EE;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif">
  <div style="max-width:560px;margin:40px auto 60px;background:#ffffff;
              border-radius:16px;overflow:hidden;
              box-shadow:0 4px 24px rgba(0,0,0,.08)">

    <!-- header -->
    <div style="background:#1B140F;padding:28px 40px">
      <div style="display:inline-flex;align-items:center;gap:10px">
        <div style="width:32px;height:32px;background:#C46A2B;border-radius:8px;
                    display:inline-flex;align-items:center;justify-content:center">
          <span style="color:#fff;font-size:16px;font-weight:700">S</span>
        </div>
        <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:-.3px">SurveyQC</span>
      </div>
    </div>

    <!-- body -->
    <div style="padding:40px">
      {body_html}
    </div>

    <!-- footer -->
    <div style="padding:20px 40px 28px;border-top:1px solid #F0EDE8">
      <p style="margin:0;font-size:12px;color:#8A847A;line-height:1.6">
        SurveyQC &nbsp;·&nbsp; Built for QC professionals worldwide<br>
        Questions? Reply to this email — we read every one.
      </p>
    </div>
  </div>
</body></html>"""


# ── Public functions ──────────────────────────────────────────────────────────

def send_welcome_email(to_email: str, name: str, plan: str = "Free") -> bool:
    """
    Sent immediately after a successful signup.
    Welcomes the user, states their plan limits, and links to /new-qc.
    """
    plan_detail = {
        "Free":     "3 QC reports included",
        "Pro":      "25 QC reports per month",
        "Business": "Unlimited QC reports",
    }.get(plan, "Free plan — 3 reports")

    first = name.split()[0] if name else "there"

    body = f"""
      <h1 style="margin:0 0 8px;font-size:24px;font-weight:700;color:#171717;
                 letter-spacing:-.5px">Welcome, {first}! 🎉</h1>
      <p style="margin:0 0 24px;color:#5F5B53;font-size:15px;line-height:1.65">
        Your SurveyQC account is live. You're on the
        <strong style="color:#171717">{plan}</strong> plan — {plan_detail}.
      </p>

      <div style="background:#F7F4EE;border-radius:12px;padding:20px 24px;margin-bottom:28px">
        <p style="margin:0 0 12px;font-weight:600;color:#171717;font-size:14px">
          Getting started in 3 steps:
        </p>
        <ol style="margin:0;padding-left:20px;color:#5F5B53;font-size:14px;line-height:2.1">
          <li>Upload your survey spec document <em>(.docx)</em></li>
          <li>Paste your live survey URL</li>
          <li>Click <strong>Run QC</strong> — report ready in 3–10 minutes</li>
        </ol>
      </div>

      <a href="{APP_URL}/new-qc"
         style="display:inline-block;background:#1B140F;color:#F7F4EE;
                text-decoration:none;padding:13px 28px;border-radius:10px;
                font-size:15px;font-weight:600">
        Run your first QC &rarr;
      </a>
    """
    return _send(to_email, "Welcome to SurveyQC!", _wrap(body))


def send_report_ready_email(to_email: str, name: str, doc_filename: str,
                             issues_count: int, job_id: str = "") -> bool:
    """
    Sent when run_qc_engine completes (status → 'done').
    Shows the verdict and links directly to the report page.
    """
    first       = name.split()[0] if name else "there"
    report_url  = f"{APP_URL}/report/{job_id}" if job_id else f"{APP_URL}/reports"
    clean_name  = doc_filename[:60] + ("…" if len(doc_filename) > 60 else "")

    if issues_count == 0:
        verdict_color = "#16A34A"
        verdict_text  = "All checks passed ✓"
        verdict_sub   = "Your survey looks good — no structural issues found."
    else:
        verdict_color = "#DC2626"
        label         = f"{issues_count} issue{'s' if issues_count != 1 else ''}"
        verdict_text  = f"{label} found"
        verdict_sub   = "Review the full report for details and fix recommendations."

    body = f"""
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#171717;
                 letter-spacing:-.4px">Your QC Report is Ready</h1>
      <p style="margin:0 0 24px;color:#5F5B53;font-size:15px">
        Hi {first}, your quality check on
        <strong style="color:#171717">{clean_name}</strong> has finished.
      </p>

      <div style="background:#F7F4EE;border-radius:12px;padding:22px 24px;
                  margin-bottom:28px;text-align:center">
        <p style="margin:0 0 4px;font-size:11px;font-weight:600;
                  color:#8A847A;text-transform:uppercase;letter-spacing:.07em">Result</p>
        <p style="margin:0 0 6px;font-size:28px;font-weight:700;
                  color:{verdict_color};letter-spacing:-.5px">{verdict_text}</p>
        <p style="margin:0;font-size:13px;color:#5F5B53">{verdict_sub}</p>
      </div>

      <a href="{report_url}"
         style="display:inline-block;background:#1B140F;color:#F7F4EE;
                text-decoration:none;padding:13px 28px;border-radius:10px;
                font-size:15px;font-weight:600">
        View full report &rarr;
      </a>
      <p style="margin:20px 0 0;font-size:13px;color:#8A847A">
        A downloadable Word report is available on the report page for client sharing.
      </p>
    """
    return _send(to_email, "Your QC Report is Ready — SurveyQC", _wrap(body))


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """
    Sent after a user submits /forgot-password.
    The link contains a one-time token; expires in 1 hour.
    """
    log.info("Sending reset email to: %s", to_email)
    log.info("Reset link generated: %s", reset_link)
    body = f"""
      <h1 style="margin:0 0 8px;font-size:22px;font-weight:700;color:#171717;
                 letter-spacing:-.4px">Reset your password</h1>
      <p style="margin:0 0 24px;color:#5F5B53;font-size:15px;line-height:1.65">
        We received a password-reset request for your SurveyQC account.<br>
        Click the button below — this link is valid for <strong>1 hour</strong>.
      </p>

      <a href="{reset_link}"
         style="display:inline-block;background:#C46A2B;color:#ffffff;
                text-decoration:none;padding:13px 28px;border-radius:10px;
                font-size:15px;font-weight:600">
        Reset password &rarr;
      </a>

      <p style="margin:24px 0 0;font-size:13px;color:#8A847A;line-height:1.65">
        If you didn't request this, ignore this email — your password won't change.<br>
        For security, this link works only once and expires after 1 hour.
      </p>
    """
    return _send(to_email, "Reset your SurveyQC password", _wrap(body))
