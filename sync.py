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
        print(f"Error parsing date {dt_str}: {e}")
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

    headers = {"X-Access-Token": token, "User-Agent": "GroupMeCalendarSync/2.0"}

    # 1. Fetch current user info
    my_user_id = None
    try:
        user_req = urllib.request.Request("https://api.groupme.com/v3/users/me", headers=headers)
        with urllib.request.urlopen(user_req) as resp:
            my_user_id = str(json.loads(resp.read().decode()).get("response", {}).get("id"))
            print(f"Authenticated as User ID: {my_user_id}")
    except Exception as e:
        print(f"Warning: Could not fetch user profile: {e}")

    # 2. Fetch all groups
    print("Fetching groups from GroupMe...")
    groups_req = urllib.request.Request(
        "https://api.groupme.com/v3/groups?per_page=100",
        headers=headers
    )

    try:
        with urllib.request.urlopen(groups_req) as resp:
            groups = json.loads(resp.read().decode()).get("response", [])
    except Exception as e:
        print(f"Failed to fetch groups: {e}")
        sys.exit(1)

    print(f"Found {len(groups)} top-level groups.")

    # 3. Discover all channels and subgroups
    all_channels = []
    member_map = {}

    for g in groups:
        gid = str(g.get("id"))
        gname = g.get("name")
        
        # Cache members for name resolution
        for m in g.get("members", []):
            uid = str(m.get("user_id"))
            name = m.get("nickname") or m.get("name")
            if uid and name:
                member_map[uid] = name

        all_channels.append({
            "id": gid,
            "name": gname,
            "group_name": gname,
            "channel_title": gname,
            "is_subgroup": False
        })

        # Fetch subgroups / channels
        try:
            sg_req = urllib.request.Request(f"https://api.groupme.com/v3/groups/{gid}/subgroups", headers=headers)
            with urllib.request.urlopen(sg_req) as sg_resp:
                subgroups = json.loads(sg_resp.read().decode()).get("response", [])
                for sg in subgroups:
                    sid = str(sg.get("id"))
                    topic = sg.get("topic") or sg.get("name") or f"Channel {sid}"
                    all_channels.append({
                        "id": sid,
                        "name": topic,
                        "group_name": gname,
                        "channel_title": f"{gname} > {topic}",
                        "is_subgroup": True
                    })
        except Exception as e:
            print(f"  Note: No subgroups or error fetching subgroups for {gname}: {e}")

    print(f"Total targets (groups + channels) to scan: {len(all_channels)}")

    # 4. Fetch events for every group and channel
    all_events = []
    seen_event_ids = set()

    for ch in all_channels:
        cid = ch["id"]
        channel_title = ch["channel_title"]
        try:
            ev_req = urllib.request.Request(f"https://api.groupme.com/v3/conversations/{cid}/events/list", headers=headers)
            with urllib.request.urlopen(ev_req) as ev_resp:
                ev_data = json.loads(ev_resp.read().decode())
                events = ev_data.get("response", {}).get("events", [])
                
                active_events = [e for e in events if not e.get("deleted_at")]
                for ev in active_events:
                    eid = ev.get("event_id") or ev.get("id")
                    if eid in seen_event_ids:
                        continue
                    seen_event_ids.add(eid)
                    ev["_channel_info"] = ch
                    all_events.append(ev)

                if active_events:
                    print(f"  [{channel_title}] Found {len(active_events)} active events.")
        except Exception as e:
            pass

    print(f"\nTotal unique active events across all channels: {len(all_events)}")

    # 5. Build iCalendar feed (RFC 5545)
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//GroupMe Calendar Sync//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:GroupMe Events",
        "X-WR-TIMEZONE:UTC",
        "X-WR-CALDESC:All GroupMe Events across all groups and channels",
    ]

    for ev in all_events:
        event_id = ev.get("event_id") or ev.get("id") or str(hash(ev.get("name", "") + str(ev.get("start_at", ""))))
        uid = f"groupme-{event_id}@groupme.com"
        name = ev.get("name", "Untitled Event")
        ch_info = ev.get("_channel_info", {})
        channel_name = ch_info.get("name", "General")
        group_name = ch_info.get("group_name", "GroupMe")
        is_subgroup = ch_info.get("is_subgroup", False)

        going_uids = [str(x) for x in ev.get("going", [])]
        maybe_uids = [str(x) for x in ev.get("maybe_going", [])]
        not_going_uids = [str(x) for x in ev.get("not_going", [])]

        if my_user_id and my_user_id in going_uids:
            my_rsvp = "GOING"
            rsvp_badge = "[GOING] "
        elif my_user_id and my_user_id in maybe_uids:
            my_rsvp = "MAYBE"
            rsvp_badge = "[MAYBE] "
        elif my_user_id and my_user_id in not_going_uids:
            my_rsvp = "NOT GOING"
            rsvp_badge = ""
        else:
            my_rsvp = "No RSVP yet"
            rsvp_badge = ""

        prefix = f"[{channel_name}] " if is_subgroup else f"[{group_name}] "
        summary = f"{rsvp_badge}{prefix}{name}"

        going_names = [member_map.get(u, f"User {u}") for u in going_uids]
        maybe_names = [member_map.get(u, f"User {u}") for u in maybe_uids]

        desc_lines = []
        desc_lines.append(f"🎟️ YOUR RSVP: {my_rsvp.upper()}")
        desc_lines.append(f"📁 GROUP: {group_name}")
        if is_subgroup:
            desc_lines.append(f"💬 CHANNEL: {channel_name}")
        desc_lines.append(f"👥 ATTENDEES ({len(going_uids)} Going, {len(maybe_uids)} Maybe):")
        
        if going_names:
            preview_names = ", ".join(going_names[:25])
            if len(going_names) > 25:
                preview_names += f" and {len(going_names) - 25} more"
            desc_lines.append(f"   Going: {preview_names}")
        if maybe_names:
            desc_lines.append(f"   Maybe: {', '.join(maybe_names[:15])}")

        if ev.get("description"):
            desc_lines.append("\n" + "="*30 + "\n" + ev.get("description").strip())

        share_url = ev.get("share_url")
        if share_url:
            desc_lines.append(f"\n🔗 Open in GroupMe: {share_url}")

        image_url = ev.get("image_url")
        if image_url:
            desc_lines.append(f"🖼️ Event Photo: {image_url}")

        description = "\n".join(desc_lines)

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
        if share_url:
            ics_lines.append(f"URL:{share_url}")
        if image_url:
            ics_lines.append(f"ATTACH;FMTTYPE=image/jpeg:{image_url}")

        ics_lines.append("STATUS:CONFIRMED")
        ics_lines.append("END:VEVENT")

    ics_lines.append("END:VCALENDAR")

    os.makedirs("docs", exist_ok=True)
    ics_content = "\r\n".join(ics_lines) + "\r\n"

    with open("docs/events.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)
    with open("events.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)

    html_content = f"""<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="UTF-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0">\n    <title>GroupMe Calendar Feed</title>\n    <style>\n        body {{\n            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;\n            background: #f7f9fc;\n            color: #333;\n            max-width: 720px;\n            margin: 60px auto;\n            padding: 24px;\n        }}\n        .card {{\n            background: white;\n            padding: 32px;\n            border-radius: 12px;\n            box-shadow: 0 4px 16px rgba(0,0,0,0.08);\n        }}\n        h1 {{ margin-top: 0; color: #0070f3; }}\n        code {{\n            background: #eee;\n            padding: 6px 10px;\n            border-radius: 4px;\n            word-break: break-all;\n            display: block;\n            margin: 12px 0;\n            font-size: 14px;\n        }}\n        a.btn {{\n            display: inline-block;\n            background: #0070f3;\n            color: white;\n            text-decoration: none;\n            padding: 10px 18px;\n            border-radius: 6px;\n            margin-top: 12px;\n            font-weight: 600;\n        }}\n        .status {{\n            color: #666;\n            font-size: 13px;\n            margin-top: 24px;\n            border-top: 1px solid #eee;\n            padding-top: 12px;\n        }}\n    </style>\n</head>\n<body>\n    <div class="card">\n        <h1>GroupMe Calendar Feed</h1>\n        <p>This feed automatically syncs your GroupMe group and channel events to Google Calendar.</p>\n        <p><strong>Calendar Subscription URL:</strong></p>\n        <code>https://half-fast-code.github.io/groupme-calendar-feed/events.ics</code>\n        <a class="btn" href="events.ics">Download / View iCal Feed</a>\n        <div class="status">\n            Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} &bull; Active events synced: {len(all_events)} across {len(all_channels)} groups and channels\n        </div>\n    </div>\n</body>\n</html>\n"""
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\nSuccessfully generated docs/events.ics and docs/index.html with {len(all_events)} events!")

if __name__ == "__main__":
    main()
