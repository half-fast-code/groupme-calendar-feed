import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import re

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def escape_ics(text):
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace(";", "\\;")
    text = text.replace(",", "\\,")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text

def parse_iso8601_to_ics(dt_str, is_all_day=False):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if is_all_day:
            return f"VALUE=DATE:{dt.strftime('%Y%m%d')}"
        dt_utc = dt.astimezone(timezone.utc)
        return dt_utc.strftime("%Y%m%dT%H%M%SZ")
    except Exception as e:
        clean = re.sub(r"[-:]", "", dt_str).split(".")[0]
        if "T" in clean:
            return clean if clean.endswith("Z") else clean + "Z"
        return clean

def main():
    token = os.environ.get("GROUPME_ACCESS_TOKEN")
    if not token and len(sys.argv) > 1:
        token = sys.argv[1]

    if not token:
        print("Error: GROUPME_ACCESS_TOKEN environment variable not set.")
        sys.exit(1)

    print("Fetching groups from GroupMe...")
    groups_req = urllib.request.Request(
        "https://api.groupme.com/v3/groups?per_page=100&omit=memberships",
        headers={"X-Access-Token": token, "User-Agent": "GroupMeCalendarSync/1.0"}
    )

    try:
        with urllib.request.urlopen(groups_req) as resp:
            groups = json.loads(resp.read().decode()).get("response", [])
    except Exception as e:
        print(f"Failed to fetch groups: {e}")
        sys.exit(1)

    print(f"Found {len(groups)} groups.")
    all_events = []

    for g in groups:
        gid = g.get("id")
        gname = g.get("name")
        try:
            ev_req = urllib.request.Request(
                f"https://api.groupme.com/v3/conversations/{gid}/events/list",
                headers={"X-Access-Token": token, "User-Agent": "GroupMeCalendarSync/1.0"}
            )
            with urllib.request.urlopen(ev_req) as ev_resp:
                ev_data = json.loads(ev_resp.read().decode())
                events = ev_data.get("response", {}).get("events", [])
                for ev in events:
                    if ev.get("deleted_at"):
                        continue
                    ev["_group_name"] = gname
                    ev["_group_id"] = gid
                    all_events.append(ev)
                print(f"  [{gname}] Active events: {len([e for e in events if not e.get('deleted_at')])}")
        except Exception as e:
            print(f"  Error fetching events for group {gid}: {e}")

    print(f"Total active events across all groups: {len(all_events)}")

    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GroupMe Calendar Sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:GroupMe Events",
        "X-WR-TIMEZONE:UTC",
        "X-WR-CALDESC:Synced GroupMe Calendar Events",
    ]

    for ev in all_events:
        event_id = ev.get("event_id") or ev.get("id") or str(hash(ev.get("name", "") + str(ev.get("start_at", ""))))
        uid = f"groupme-{event_id}@groupme.com"
        name = ev.get("name", "Untitled Event")
        group_name = ev.get("_group_name", "GroupMe")
        summary = f"[{group_name}] {name}"
        
        description_parts = []
        if ev.get("description"):
            description_parts.append(ev.get("description"))
        description_parts.append(f"\\nGroup: {group_name}")
        if ev.get("image_url"):
            description_parts.append(f"Image: {ev.get('image_url')}")
        description = "\\n".join(description_parts)

        loc_obj = ev.get("location") or {}
        location_parts = []
        if loc_obj.get("name"):
            location_parts.append(loc_obj.get("name"))
        if loc_obj.get("address"):
            location_parts.append(loc_obj.get("address"))
        location = ", ".join(location_parts)

        is_all_day = ev.get("is_all_day", False)
        start_at = ev.get("start_at")
        end_at = ev.get("end_at")

        dtstart = parse_iso8601_to_ics(start_at, is_all_day)
        if not dtstart:
            continue

        ics_lines.append("BEGIN:VEVENT")
        ics_lines.append(f"UID:{uid}")
        ics_lines.append(f"DTSTAMP:{now_utc}")

        if is_all_day or dtstart.startswith("VALUE=DATE:"):
            ics_lines.append(f"DTSTART;{dtstart}")
            if end_at:
                dtend = parse_iso8601_to_ics(end_at, is_all_day)
                ics_lines.append(f"DTEND;{dtend}")
        else:
            ics_lines.append(f"DTSTART:{dtstart}")
            if end_at:
                dtend = parse_iso8601_to_ics(end_at, False)
                ics_lines.append(f"DTEND:{dtend}")

        ics_lines.append(f"SUMMARY:{escape_ics(summary)}")
        ics_lines.append(f"DESCRIPTION:{escape_ics(description)}")
        if location:
            ics_lines.append(f"LOCATION:{escape_ics(location)}")
        ics_lines.append("STATUS:CONFIRMED")
        ics_lines.append("END:VEVENT")

    ics_lines.append("END:VCALENDAR")

    os.makedirs("docs", exist_ok=True)
    ics_content = "\r\n".join(ics_lines) + "\r\n"

    with open("docs/events.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)
    with open("events.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)

    html_content = f"""<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>GroupMe Calendar Feed</title>\n    <style>\n        body {{\n            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;\n            background: #f7f9fc;\n            color: #333;\n            max-width: 680px;\n            margin: 60px auto;\n            padding: 24px;\n        }}\n        .card {{\n            background: white;\n            padding: 32px;\n            border-radius: 12px;\n            box-shadow: 0 4px 16px rgba(0,0,0,0.08);\n        }}\n        h1 {{ margin-top: 0; color: #0070f3; }}\n        code {{\n            background: #eee;\n            padding: 4px 8px;\n            border-radius: 4px;\n            word-break: break-all;\n            display: block;\n            margin: 12px 0;\n            font-size: 14px;\n        }}\n        a.btn {{\n            display: inline-block;\n            background: #0070f3;\n            color: white;\n            text-decoration: none;\n            padding: 10px 18px;\n            border-radius: 6px;\n            margin-top: 12px;\n            font-weight: 600;\n        }}\n        .status {{\n            color: #666;\n            font-size: 13px;\n            margin-top: 24px;\n            border-top: 1px solid #eee;\n            padding-top: 12px;\n        }}\n    </style>\n</head>\n<body>\n    <div class="card">\n        <h1>GroupMe Calendar Feed</h1>\n        <p>This feed automatically syncs your GroupMe group events.</p>\n        <p><strong>Direct iCal Feed URL:</strong></p>\n        <code>events.ics</code>\n        <a class="btn" href="events.ics">Download / View iCal File</a>\n        <div class="status">\n            Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} &bull; Active events: {len(all_events)}\n        </div>\n    </div>\n</body>\n</html>\n"""
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print("Successfully generated docs/events.ics and docs/index.html!")

if __name__ == "__main__":
    main()
