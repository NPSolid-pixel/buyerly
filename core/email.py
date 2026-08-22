import logging
import httpx
from typing import Optional
from core.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


async def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: Optional[str] = None
) -> bool:
    """Send transactional email via Resend API or log in local dev mode."""
    if not to_email or not to_email.strip():
        logger.warning("Attempted to send email to empty recipient")
        return False

    recipient = to_email.strip()
    sender = settings.EMAIL_FROM or "Buyerly <team@buyerly.app>"

    # 1. If Resend API key is configured, send via Resend REST API
    if settings.RESEND_API_KEY:
        try:
            payload = {
                "from": sender,
                "to": [recipient],
                "subject": subject,
                "html": html_content,
            }
            if text_content:
                payload["text"] = text_content

            headers = {
                "Authorization": f"Bearer {settings.RESEND_API_KEY.strip()}",
                "Content-Type": "application/json"
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(RESEND_API_URL, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info("Email sent successfully via Resend to %s: %s", recipient, resp.text)
                    return True
                else:
                    logger.error("Failed to send email via Resend to %s: HTTP %d %s", recipient, resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.error("Exception while sending email via Resend to %s: %s", recipient, e)
            return False

    # 2. Local fallback / logging mode
    logger.info("[DEV EMAIL] To: %s | Subject: %s | (No RESEND_API_KEY configured)", recipient, subject)
    return True


async def send_otp_verification_email(to_email: str, otp_code: str) -> bool:
    """Send 6-digit one-time password (OTP) verification email."""
    subject = f"Your Buyerly verification code is {otp_code}"
    
    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Your Buyerly verification code</title>
</head>
<body style="margin:0;padding:0;background-color:#F5F6F8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#101112;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#F5F6F8;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:12px;border:1px solid #E6E7EA;box-shadow:0 2px 8px rgba(0,0,0,0.04);overflow:hidden;padding:36px 32px;">
          <tr>
            <td align="center" style="padding-bottom:20px;">
              <div style="font-size:24px;font-weight:700;letter-spacing:-0.5px;color:#101112;">Buyerly</div>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:12px;">
              <h1 style="margin:0;font-size:20px;font-weight:600;color:#101112;text-align:center;">Your verification code</h1>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:24px;color:#5A5E66;font-size:14px;line-height:1.5;text-align:center;">
              Enter the following 6-digit temporary password to sign in to your Buyerly account. This code is valid for 15 minutes.
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <div style="display:inline-block;background:#F5F6F8;border:1px solid #E6E7EA;border-radius:8px;padding:14px 28px;font-size:32px;font-weight:700;letter-spacing:6px;color:#266DF0;font-family:monospace,sans-serif;">
                {otp_code}
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:28px;color:#898A8D;font-size:12px;line-height:1.5;text-align:center;">
              If you didn't request this verification code, you can safely ignore this email.
            </td>
          </tr>
          <tr>
            <td style="border-top:1px solid #E6E7EA;padding-top:20px;text-align:center;color:#898A8D;font-size:11px;">
              &copy; 2026 Buyerly &middot; Automated Media Buying Platform
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_content = f"Your Buyerly verification code is: {otp_code}\n\nThis code is valid for 15 minutes.\nIf you didn't request this code, please ignore this email."
    return await send_email(to_email, subject, html_content, text_content)


async def send_workspace_invitation_email(
    to_email: str,
    workspace_name: str,
    inviter_name: str,
    role: str,
    invite_token: str
) -> bool:
    """Send workspace invitation email with direct join link."""
    subject = f"{inviter_name or 'A team member'} invited you to join {workspace_name} on Buyerly"
    join_url = f"https://buyerly.app/invite/{invite_token}"

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>You're invited to join {workspace_name} on Buyerly</title>
</head>
<body style="margin:0;padding:0;background-color:#F5F6F8;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#101112;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color:#F5F6F8;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="100%" border="0" cellspacing="0" cellpadding="0" style="max-width:480px;background:#ffffff;border-radius:12px;border:1px solid #E6E7EA;box-shadow:0 2px 8px rgba(0,0,0,0.04);overflow:hidden;padding:36px 32px;">
          <tr>
            <td align="center" style="padding-bottom:20px;">
              <div style="font-size:24px;font-weight:700;letter-spacing:-0.5px;color:#101112;">Buyerly</div>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:12px;">
              <h1 style="margin:0;font-size:20px;font-weight:600;color:#101112;text-align:center;">You've been invited!</h1>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:24px;color:#5A5E66;font-size:14px;line-height:1.5;text-align:center;">
              <strong>{inviter_name or 'A colleague'}</strong> has invited you to collaborate in the <strong>{workspace_name}</strong> workspace as <strong>{role.capitalize()}</strong>.
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <a href="{join_url}" target="_blank" style="display:inline-block;background:#266DF0;color:#ffffff;text-decoration:none;font-size:14px;font-weight:600;padding:12px 28px;border-radius:6px;box-shadow:0 1px 3px rgba(38,109,240,0.3);">
                Accept & Join Workspace
              </a>
            </td>
          </tr>
          <tr>
            <td style="padding-bottom:28px;color:#898A8D;font-size:12px;line-height:1.5;text-align:center;">
              Or copy this link to your browser:<br>
              <a href="{join_url}" style="color:#266DF0;word-break:break-all;font-size:11px;">{join_url}</a>
            </td>
          </tr>
          <tr>
            <td style="border-top:1px solid #E6E7EA;padding-top:20px;text-align:center;color:#898A8D;font-size:11px;">
              &copy; 2026 Buyerly &middot; Automated Media Buying Platform
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    text_content = f"You've been invited to join {workspace_name} on Buyerly by {inviter_name}.\n\nClick the link below to accept your invitation:\n{join_url}"
    return await send_email(to_email, subject, html_content, text_content)
