import os
import datetime
import resend

resend.api_key = os.environ.get("RESEND_API_KEY")

FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "The Graduate <hello@thegraduate.io>")
APP_URL = os.environ.get("APP_URL", "https://thegraduate.io/app")

BRAND_COLOR = "#4f46e5"


def _wrap(inner_html: str) -> str:
    return f"""
    <div style="font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; background:#f8fafc; padding:32px 16px;">
      <div style="max-width:560px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f1f5f9;">
        <div style="background:{BRAND_COLOR}; padding:24px 32px;">
          <span style="color:#ffffff; font-size:20px; font-weight:800; font-family:Georgia, serif;">Graduate</span>
        </div>
        <div style="padding:32px;">
          {inner_html}
        </div>
        <div style="padding:20px 32px; border-top:1px solid #f1f5f9;">
          <p style="color:#94a3b8; font-size:11px; margin:0;">The Graduate &middot; thegraduate.io</p>
        </div>
      </div>
    </div>
    """


def _button(label: str, url: str) -> str:
    return f"""
    <a href="{url}" style="display:inline-block; background:{BRAND_COLOR}; color:#ffffff; text-decoration:none;
       padding:12px 24px; border-radius:10px; font-weight:700; font-size:14px; margin-top:16px;">
      {label}
    </a>
    """


def send_welcome_email(name: str, email: str):
    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Welcome to Graduate, {name}!</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 12px;">
        Graduate is your career and education hub, the place where students, recent grads,
        employers, and professors connect. Find job openings, discover scholarships, get endorsed
        for your skills, and build the professional network that gets you hired.
      </p>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 4px;">
        The best next step is finishing your profile so we can start matching you with jobs and
        scholarships that fit your major and skills.
      </p>
      {_button("Complete Your Profile", f"{APP_URL}?view=profile")}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": "Welcome to Graduate 🎓",
        "html": _wrap(inner),
    })


def send_job_matches_email(name: str, email: str, matches: list):
    rows = ""
    for job in matches:
        location = job.get("location") or "Remote"
        salary = job.get("salary_range")
        salary_html = f'<span style="color:#94a3b8;">&middot; {salary}</span>' if salary else ""
        rows += f"""
        <div style="border:1px solid #f1f5f9; border-radius:12px; padding:16px; margin-bottom:12px;">
          <p style="margin:0 0 4px; font-weight:800; color:#0f172a; font-size:15px;">{job.get('title')}</p>
          <p style="margin:0 0 8px; color:#64748b; font-size:13px;">{job.get('company')} &middot; {location} {salary_html}</p>
          {_button("View Job", job.get('url') or f"{APP_URL}?view=jobs")}
        </div>
        """

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Your top job matches, {name}</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 20px;">
        Based on your major and skills, here are your top {len(matches)} matches on Graduate right now.
      </p>
      {rows}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": f"Your top {len(matches)} job matches on Graduate",
        "html": _wrap(inner),
    })


def send_password_reset_email(name: str, email: str, reset_url: str):
    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Reset your password</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 4px;">
        Hi {name}, we received a request to reset your Graduate password. This link expires in
        1 hour. If you didn't request this, you can safely ignore this email.
      </p>
      {_button("Reset Password", reset_url)}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": "Reset your Graduate password",
        "html": _wrap(inner),
    })


def send_daily_analytics_email(to_email: str, stats: dict):
    today = datetime.date.today().strftime("%B %d, %Y")
    rows = "".join(f"""
      <tr>
        <td style="padding:10px 0; color:#64748b; font-size:13px; border-bottom:1px solid #f1f5f9;">{label}</td>
        <td style="padding:10px 0; color:#0f172a; font-size:15px; font-weight:800; text-align:right; border-bottom:1px solid #f1f5f9;">{value}</td>
      </tr>
    """ for label, value in [
        ("Total Users", stats["total_users"]),
        ("New Signups (24h)", stats["new_signups_24h"]),
        ("Total Jobs", stats["total_jobs"]),
        ("Total Scholarships", stats["total_scholarships"]),
        ("Total Applications", stats["total_applications"]),
    ])

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 4px;">Daily Report</h1>
      <p style="color:#94a3b8; font-size:13px; margin:0 0 20px;">{today}</p>
      <table style="width:100%; border-collapse:collapse;">{rows}</table>
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": to_email,
        "subject": f"Graduate Daily Report | {today}",
        "html": _wrap(inner),
    })
