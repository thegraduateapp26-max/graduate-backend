import os
import datetime
import html
import resend

resend.api_key = os.environ.get("RESEND_API_KEY")

FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "The Graduate <hello@thegraduate.io>")
APP_URL = os.environ.get("APP_URL", "https://thegraduate.io/app")

BRAND_COLOR = "#4f46e5"


def _esc(value) -> str:
    """Escapes user/employer-controlled text before it's interpolated into an HTML email body -
    e.g. a job title or company name (set by an employer account) reaching another user's inbox
    via the job-match email, unescaped, would let that employer inject arbitrary HTML/links."""
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_url(url, fallback: str) -> str:
    """Only allows http(s) links in an href - blocks javascript: URIs and (via html.escape)
    attribute-breakout from employer-controlled job URLs."""
    if url and str(url).strip().lower().startswith(("http://", "https://")):
        return html.escape(str(url), quote=True)
    return fallback


def _wrap(inner_html: str) -> str:
    return f"""
    <div style="font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif; background:#f8fafc; padding:32px 16px;">
      <div style="max-width:560px; margin:0 auto; background:#ffffff; border-radius:16px; overflow:hidden; border:1px solid #f1f5f9;">
        <div style="background:{BRAND_COLOR}; padding:24px 32px;">
          <span style="color:#ffffff; font-size:20px; font-weight:800; font-family:Georgia, serif;">Grad<span style="color:#a5b4fc;">uate</span></span>
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
    first_name = _esc((name or "").split(" ")[0] or name)

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
        location = _esc(job.get("location") or "Remote")
        salary = job.get("salary_range")
        salary_html = f'<span style="color:#94a3b8;">&middot; {_esc(salary)}</span>' if salary else ""
        job_url = _safe_url(job.get('url'), f"{APP_URL}?view=jobs")
        rows += f"""
        <div style="border:1px solid #f1f5f9; border-radius:12px; padding:16px; margin-bottom:12px;">
          <p style="margin:0 0 4px; font-weight:800; color:#0f172a; font-size:15px;">{_esc(job.get('title'))}</p>
          <p style="margin:0 0 8px; color:#64748b; font-size:13px;">{_esc(job.get('company'))} &middot; {location} {salary_html}</p>
          {_button("View Job", job_url)}
        </div>
        """

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Your top job matches, {_esc(name)}</h1>
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
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Your graduation is coming up, {_esc(name)}!</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 12px;">
        You told us you're set to graduate around <strong>{_esc(grad_date_label)}</strong> - that's about a
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
        Hi {_esc(name)}, we received a request to reset your Graduate password. This link expires in
        1 hour. If you didn't request this, you can safely ignore this email.
      </p>
      {_button("Reset Password", _safe_url(reset_url, f"{APP_URL}?view=forgot-password"))}
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


def send_prune_report(admin_email: str, checked: int, removed: list):
    today = datetime.date.today().strftime("%B %d, %Y")
    if removed:
        rows = "".join(f"""
          <tr>
            <td style="padding:8px 0; color:#0f172a; font-size:13px; font-weight:700; border-bottom:1px solid #f1f5f9;">{_esc(r['title'])}</td>
            <td style="padding:8px 0; color:#64748b; font-size:13px; text-align:right; border-bottom:1px solid #f1f5f9;">{_esc(r['company'])}</td>
          </tr>
        """ for r in removed)
        body = f"""<table style="width:100%; border-collapse:collapse; margin-top:12px;">{rows}</table>"""
    else:
        body = """<p style="color:#64748b; font-size:14px; margin-top:12px;">Nothing to remove - every checked listing is still live.</p>"""

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 4px;">Weekly Dead Job Cleanup</h1>
      <p style="color:#94a3b8; font-size:13px; margin:0 0 4px;">{today}</p>
      <p style="color:#475569; font-size:14px; margin:12px 0 0;">Checked {checked} job listing{'s' if checked != 1 else ''}, removed {len(removed)} that {'were' if len(removed) != 1 else 'was'} no longer live on the company's site.</p>
      {body}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": admin_email,
        "subject": f"Graduate: {len(removed)} dead job listing{'s' if len(removed) != 1 else ''} removed | {today}",
        "html": _wrap(inner),
    })


def send_ingest_report(admin_email: str, new_count: int, new_jobs: list, full_refresh: bool = False):
    today = datetime.date.today().strftime("%B %d, %Y")
    mode_label = "Full Board Refresh" if full_refresh else "Weekly New Jobs"
    if new_jobs:
        # Capped so a full-refresh run (hundreds of rows) doesn't blow up the email body.
        shown = new_jobs[:40]
        rows = "".join(f"""
          <tr>
            <td style="padding:8px 0; color:#0f172a; font-size:13px; font-weight:700; border-bottom:1px solid #f1f5f9;">{_esc(r['title'])}</td>
            <td style="padding:8px 0; color:#64748b; font-size:13px; text-align:right; border-bottom:1px solid #f1f5f9;">{_esc(r['company'])}</td>
          </tr>
        """ for r in shown)
        more_note = f"""<p style="color:#94a3b8; font-size:12px; margin-top:12px;">...and {len(new_jobs) - len(shown)} more.</p>""" if len(new_jobs) > len(shown) else ""
        body = f"""<table style="width:100%; border-collapse:collapse; margin-top:12px;">{rows}</table>{more_note}"""
    else:
        body = """<p style="color:#64748b; font-size:14px; margin-top:12px;">No new organized listings found this run - try again next week.</p>"""

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 4px;">{mode_label}</h1>
      <p style="color:#94a3b8; font-size:13px; margin:0 0 4px;">{today}</p>
      <p style="color:#475569; font-size:14px; margin:12px 0 0;">Added {new_count} new job listing{'s' if new_count != 1 else ''} to the board.</p>
      {body}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": admin_email,
        "subject": f"Graduate: {new_count} new job listing{'s' if new_count != 1 else ''} added | {today}",
        "html": _wrap(inner),
    })


def send_scholarship_report(admin_email: str, published: list, pending: list):
    today = datetime.date.today().strftime("%B %d, %Y")

    def _rows(items):
        return "".join(f"""
          <tr>
            <td style="padding:8px 0; color:#0f172a; font-size:13px; font-weight:700; border-bottom:1px solid #f1f5f9;">{_esc(r['title'])}</td>
            <td style="padding:8px 0; color:#64748b; font-size:13px; text-align:right; border-bottom:1px solid #f1f5f9;">{_esc(r['provider'])}</td>
          </tr>
        """ for r in items)

    if published:
        pub_body = f"""<h2 style="font-size:15px; color:#0f172a; margin:20px 0 4px;">Published ({len(published)})</h2>
          <table style="width:100%; border-collapse:collapse;">{_rows(published)}</table>"""
    else:
        pub_body = """<p style="color:#64748b; font-size:14px; margin-top:12px;">No new scholarships found this run.</p>"""

    if pending:
        pending_body = f"""<h2 style="font-size:15px; color:#b45309; margin:20px 0 4px;">Needs review - application link didn't verify ({len(pending)})</h2>
          <table style="width:100%; border-collapse:collapse;">{_rows(pending)}</table>
          <p style="color:#94a3b8; font-size:12px; margin-top:8px;">Hidden from the app for now. Check each link and approve via PATCH /api/scholarships/&lt;id&gt;/approve, or leave it hidden if it's not real.</p>"""
    else:
        pending_body = ""

    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 4px;">Weekly Scholarship Discovery</h1>
      <p style="color:#94a3b8; font-size:13px; margin:0 0 4px;">{today}</p>
      <p style="color:#475569; font-size:14px; margin:12px 0 0;">Found {len(published)} new scholarship{'s' if len(published) != 1 else ''} with a verified application link, {len(pending)} more that need a manual look.</p>
      {pub_body}
      {pending_body}
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": admin_email,
        "subject": f"Graduate: {len(published)} new scholarship{'s' if len(published) != 1 else ''} added, {len(pending)} pending review | {today}",
        "html": _wrap(inner),
    })


def send_contact_notification(admin_email: str, name: str, sender_email: str, message: str):
    # Escaped like everything else here - this is fully public, unauthenticated user input.
    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">New contact form message</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 4px;"><strong>From:</strong> {_esc(name)} ({_esc(sender_email)})</p>
      <div style="margin-top:16px; padding:16px; background:#f8fafc; border-radius:12px; color:#334155; font-size:14px; line-height:1.6; white-space:pre-wrap;">{_esc(message)}</div>
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": admin_email,
        "reply_to": sender_email,
        "subject": f"Graduate contact form: {name}",
        "html": _wrap(inner),
    })


def send_report_notification(admin_email: str, reporter_name: str, reporter_email: str, author_name: str, reason_label: str, details: str, post_content: str):
    # post_content/details are reported (i.e. flagged as objectionable) user input - escaped
    # the same as contact form input, and truncated so one huge post can't blow up the email.
    details_html = f"""<p style="color:#475569; font-size:14px; line-height:1.6; margin:16px 0 4px;"><strong>Reporter's notes:</strong></p><div style="padding:16px; background:#f8fafc; border-radius:12px; color:#334155; font-size:14px; line-height:1.6; white-space:pre-wrap;">{_esc(details)}</div>""" if details else ""
    inner = f"""
      <h1 style="font-size:22px; color:#0f172a; margin:0 0 12px;">Post reported: {_esc(reason_label)}</h1>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 4px;"><strong>Reported by:</strong> {_esc(reporter_name)} ({_esc(reporter_email)})</p>
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:0 0 4px;"><strong>Post author:</strong> {_esc(author_name)}</p>
      {details_html}
      <p style="color:#475569; font-size:14px; line-height:1.6; margin:16px 0 4px;"><strong>Reported post:</strong></p>
      <div style="padding:16px; background:#f8fafc; border-radius:12px; color:#334155; font-size:14px; line-height:1.6; white-space:pre-wrap;">{_esc(post_content[:2000])}</div>
    """
    return resend.Emails.send({
        "from": FROM_EMAIL,
        "to": admin_email,
        "reply_to": reporter_email,
        "subject": f"Graduate report: {reason_label} (from {reporter_name})",
        "html": _wrap(inner),
    })
