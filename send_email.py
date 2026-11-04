
import json
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

# Every enabled topic under topics/ is included; set TOPICS="solar_cost" to restrict.
def _enabled(d):
    try:
        return json.loads((d / "config.json").read_text(encoding="utf-8")).get("enabled", True)
    except Exception:
        return False


TOPICS = [t.strip() for t in os.environ.get("TOPICS", "").split(",") if t.strip()] or sorted(
    d.name for d in (ROOT / "topics").iterdir() if d.is_dir() and _enabled(d))

REPORT_FILES, DATA_FILES = [], []
for t in TOPICS:
    REPORT_FILES += [(t, ROOT / "reports" / t / "latest.md"), (t, ROOT / "reports" / t / "external_latest.md")]
    DATA_FILES += [(t, ROOT / "data" / t / "literature.xlsx"), (t, ROOT / "data" / t / "external_sources.xlsx")]
FILES = REPORT_FILES + DATA_FILES

date_str = datetime.now(
    ZoneInfo("Asia/Singapore")
).strftime("%Y-%m-%d")

msg = EmailMessage()
msg["Subject"] = f"Weekly Solar Cost Literature Monitor ({' + '.join(TOPICS)}) | {date_str}"
msg["From"] = SMTP_USER
msg["To"] = TO_EMAIL

body = [
    f"Weekly Literature Monitor: {', '.join(TOPICS)}",
    "",
    f"Report date: {date_str}",
    "",
    "Attached are the latest literature and external information reports.",
    "",
    "GitHub repository:",
    "https://github.com/seanli12354/nature-literature-monitor",
    "",
]

PER_REPORT = 30000 // max(1, len(REPORT_FILES) // 2)
for topic, path in REPORT_FILES:
    if path.exists():
        body.extend([
            "=" * 50,
            f"[{topic}] {path.name}",
            "=" * 50,
            path.read_text(encoding="utf-8")[:PER_REPORT],
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
