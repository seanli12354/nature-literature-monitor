
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent

TO_EMAIL = "xiaoli8@nus.edu.sg"

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]

from topic_config import output_paths

TOPICS = os.environ.get("TOPICS", "hydro battery").split()
FILES = []
for topic in TOPICS:
    data, reports = output_paths(topic)
    FILES.extend((topic, p) for p in (
        reports / "latest.md", reports / "external_latest.md",
        data / "literature.xlsx", data / "external_sources.xlsx"))

date_str = datetime.now(
    ZoneInfo("Asia/Singapore")
).strftime("%Y-%m-%d")

msg = EmailMessage()
msg["Subject"] = f"Weekly Literature Monitor ({' + '.join(TOPICS)}) | {date_str}"
msg["From"] = SMTP_USER
msg["To"] = TO_EMAIL

body = [
    f"Weekly Literature Monitor ({' + '.join(TOPICS)})",
    "",
    f"Report date: {date_str}",
    "",
    "Attached are the latest literature and external information reports.",
    "",
    "GitHub repository:",
    "https://github.com/seanli12354/nature-literature-monitor",
    "",
]

for topic, path in FILES:
    if path.suffix != ".md":
        continue
    if path.exists():
        body.extend([
            "=" * 50,
            f"{topic}: {path.name}",
            "=" * 50,
            path.read_text(encoding="utf-8")[:30000],
            "",
        ])

msg.set_content("\n".join(body))

for topic, path in FILES:
    if not path.exists():
        print(f"WARNING: Missing attachment: {path}")
        continue

    if path.suffix == ".xlsx":
        maintype = "application"
        subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        maintype = "text"
        subtype = "markdown"

    msg.add_attachment(
        path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=f"{topic}_{path.name}",
    )

if SMTP_PORT == 465:
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
else:
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

print(f"Email sent to {TO_EMAIL}")

