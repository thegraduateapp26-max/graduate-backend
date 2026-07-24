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


def _checklist(items: list) -> str:
    rows = "".join(f"""
      <li style="display:flex; gap:12px; align-items:flex-start; margin-bottom:14px;">
        <span style="flex-shrink:0; width:22px; height:22px; border-radius:999px; background:#eef2ff; color:{BRAND_COLOR};
          font-size:12px; font-weight:800; display:inline-flex; align-items:center; justify-content:center; margin-top:1px;">&#10003;</span>
        <span style="color:#334155; font-size:14px; line-height:1.5;">{item}</span>
      </li>
    """ for item in items)
    return f'<ul style="list-style:none; margin:0 0 20px; padding:0;">{rows}</ul>'


# Role-specific "get started" checklist and primary CTA, in the same spirit as a LinkedIn or
# Indeed welcome email - a short, concrete list of next actions rather than generic marketing copy.
_ROLE_CONTENT = {
    "student": {
        "tagline": "Let's get your academic and career profile in front of the right people.",
        "items": [
            "Add your school and major so we can match you to scholarships built for you",
            "Get endorsed by a professor who knows your work - it takes them two minutes",
            "Browse internships and entry-level roles picked for your major",
        ],
        "cta": "Complete Your Profile",
        "cta_view": "profile",
    },
    "high_school_graduate": {
        "tagline": "Let's help you find scholarships and your first opportunities out of high school.",
        "items": [
            "Complete your profile so we can start matching you to scholarships",
            "Upload a Spotlight - a 60-second video pitch that gets you noticed",
            "Browse entry-level jobs and internships that don't require a degree yet",
        ],
        "cta": "Complete Your Profile",
        "cta_view": "profile",
    },
    "graduate": {
        "tagline": "Let's get you in front of recruiters who are actually hiring.",
        "items": [
            "Complete your profile so recruiters can find you by school, major, and skills",
            "Upload a Spotlight - a 60-second video pitch recruiters actually watch",
            "Browse jobs matched to your major and skills",
        ],
        "cta": "Complete Your Profile",
        "cta_view": "profile",
    },
    "employer": {
        "tagline": "Let's get your first role in front of verified students and graduates.",
        "items": [
            "Post your first job - it goes straight in front of students and graduates by major",
            "Watch Spotlights, 60-second video pitches from candidates, before you even open a resume",
            "Search the member directory by school, major, and skills",
        ],
        "cta": "Post a Job",
        "cta_view": "jobs",
    },
    "recruiter": {
        "tagline": "Let's get your first role in front of verified students and graduates.",
        "items": [
            "Post your first job - it goes straight in front of students and graduates by major",
            "Watch Spotlights, 60-second video pitches from candidates, before you even open a resume",
            "Search the member directory by school, major, and skills",
        ],
        "cta": "Post a Job",
        "cta_view": "jobs",
    },
    "professor": {
        "tagline": "Let's help your students get noticed.",
        "items": [
            "Endorse a student you've mentored - it takes two minutes and helps them get hired",
            "See which of your students are already on Graduate",
            "Keep an eye out for endorsement requests from your students",
        ],
        "cta": "Explore Graduate",
        "cta_view": "members",
    },
}


def send_welcome_email(name: str, email: str, role: str = "graduate"):
    content = _ROLE_CONTENT.get(role, _ROLE_CONTENT["graduate"])
    first_name = (name or "").split(" ")[0] or name

    inner = f"""
      <h1 style="font-size:24px; color:#0f172a; margin:0 0 8px;">Welcome to Graduate, {first_name} 🎓</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 24px;">
        {content['tagline']}
      </p>
      <p style="color:#0f172a; font-size:12px; font-weight:800; text-transform:uppercase; letter-spacing:0.04em; margin:0 0 14px;">
        Here's how to get started
      </p>
      {_checklist(content['items'])}
      {_button(content['cta'], f"{APP_URL}?view={content['cta_view']}")}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": f"Welcome to Graduate, {first_name} 🎓",
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


def send_graduation_reminder_email(name: str, email: str, grad_date_label: str):
    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Your graduation is coming up, {name}!</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 12px;">
        You told us you're set to graduate around <strong>{grad_date_label}</strong> - that's about a
        month away. We're reaching out now because your school email may stop working once you
        graduate, and we don't want you to lose access to your Graduate account.
      </p>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 12px;">
        Whenever you're ready, head to your account Settings to:
      </p>
      <ul style="color:#475569; font-size:14px; line-height:1.8; margin:0 0 16px; padding-left:20px;">
        <li>Update your email to a personal address you'll keep using</li>
        <li>Switch your account from Student to Graduate</li>
      </ul>
      <p style="color:#94a3b8; font-size:12px; line-height:1.6; margin:0 0 4px;">
        This is just a heads-up based on the graduation date you gave us when you signed up -
        no action is required right away.
      </p>
      {_button("Go to Settings", f"{APP_URL}?view=settings")}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": email,
        "subject": "Graduating soon? A quick heads-up from Graduate",
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
