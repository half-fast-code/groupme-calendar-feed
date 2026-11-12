import html
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone
import re

HIKING_GROUP_ID = "114352942"
FEED_BASE_URL = "https://half-fast-code.github.io/groupme-calendar-feed"

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

def first_name_only(full_name):
    """Extract the first name from a member's display name."""
    parts = (full_name or "").strip().split()
    return parts[0].strip(",") if parts else "Member"


def format_event_datetime_display(dt_str, is_all_day=False):
    """Format an ISO8601 timestamp into a readable date/time string for HTML."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str)
        if is_all_day:
            return dt.strftime("%a, %b %d, %Y (All Day)").replace(" 0", " ")
        return dt.strftime("%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    except Exception:
        return dt_str


def build_ics_event(ev, now_utc, summary, description):
    """Build RFC 5545 VEVENT lines for a single GroupMe event."""
    event_id = ev.get("event_id") or ev.get("id") or str(hash(ev.get("name", "") + str(ev.get("start_at", ""))))
    uid = f"groupme-{event_id}@groupme.com"

    loc_obj = ev.get("location") or {}
    location_parts = [p for p in (loc_obj.get("name"), loc_obj.get("address")) if p]
    location = ", ".join(location_parts)

    is_all_day = ev.get("is_all_day", False)
    start_at = ev.get("start_at")
    end_at = ev.get("end_at")

    dtstart = parse_iso8601_to_ics(start_at, is_all_day)
    if not dtstart:
        return []

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_utc}",
    ]

    if is_all_day or dtstart.startswith("VALUE=DATE:"):
        lines.append(f"DTSTART;{dtstart}")
        if end_at:
            dtend = parse_iso8601_to_ics(end_at, is_all_day)
            if dtend:
                lines.append(f"DTEND;{dtend}")
    else:
        lines.append(f"DTSTART:{dtstart}")
        if end_at:
            dtend = parse_iso8601_to_ics(end_at, False)
            if dtend:
                lines.append(f"DTEND:{dtend}")

    lines.append(f"SUMMARY:{escape_ics(summary)}")
    lines.append(f"DESCRIPTION:{escape_ics(description)}")
    if location:
        lines.append(f"LOCATION:{escape_ics(location)}")
    if ev.get("share_url"):
        lines.append(f"URL:{ev['share_url']}")
    if ev.get("image_url"):
        lines.append(f"ATTACH;FMTTYPE=image/jpeg:{ev['image_url']}")

    lines.append("STATUS:CONFIRMED")
    lines.append("END:VEVENT")
    return lines


def build_hiking_group_description(ev, channel_label, going_first_names, maybe_first_names):
    """Build a clean, shareable event description for the whole hiking group."""
    sections = [f"📁 Channel: {channel_label}"]

    share_url = ev.get("share_url")
    if share_url:
        sections.append(f"🔗 Open in GroupMe: {share_url}")

    image_url = ev.get("image_url")
    if image_url:
        sections.append(f"🖼️ Event Photo: {image_url}")

    raw_desc = (ev.get("description") or "").strip()
    if raw_desc:
        sections.append(
            "\n📝 Details\n"
            "──────────────────────────────\n"
            f"{raw_desc}"
        )

    attendee_lines = [
        f"\n👥 Attendees ({len(going_first_names)} Going, {len(maybe_first_names)} Maybe)",
        "──────────────────────────────",
    ]
    if going_first_names:
        attendee_lines.append(f"Going ({len(going_first_names)}):")
        attendee_lines.extend(f"• {name}" for name in going_first_names)
    if maybe_first_names:
        if going_first_names:
            attendee_lines.append("")
        attendee_lines.append(f"Maybe ({len(maybe_first_names)}):")
        attendee_lines.extend(f"• {name}" for name in maybe_first_names)
    if not going_first_names and not maybe_first_names:
        attendee_lines.append("No RSVPs yet — be the first to join in GroupMe!")

    sections.append("\n".join(attendee_lines))
    return "\n".join(sections)


def generate_hiking_feed(all_events, member_map, now_utc):
    """Generate the group-wide hiking calendar feed (docs/hiking.ics) and share page (docs/hiking.html)."""
    hiking_events = [
        ev for ev in all_events
        if str(ev.get("_channel_info", {}).get("group_id")) == HIKING_GROUP_ID
    ]

    ics_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SLC Hiking & Adventure Group Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:SLC Hiking & Adventure Group",
        "X-WR-TIMEZONE:UTC",
        "X-WR-CALDESC:All events from every channel in the SLC Hiking & Adventure Group",
    ]

    cards_html = []
    sorted_events = sorted(hiking_events, key=lambda e: e.get("start_at") or "")
    now_iso = datetime.now(timezone.utc).isoformat()

    for ev in sorted_events:
        name = ev.get("name", "Untitled Event")
        ch_info = ev.get("_channel_info", {})
        is_subgroup = ch_info.get("is_subgroup", False)
        channel_label = ch_info.get("name", "Hiking") if is_subgroup else "Hiking"

        summary = f"[{channel_label}] {name}"

        going_uids = [str(x) for x in ev.get("going", [])]
        maybe_uids = [str(x) for x in ev.get("maybe_going", [])]
        going_first = [first_name_only(member_map.get(u, "Member")) for u in going_uids]
        maybe_first = [first_name_only(member_map.get(u, "Member")) for u in maybe_uids]

        description = build_hiking_group_description(ev, channel_label, going_first, maybe_first)
        ics_lines.extend(build_ics_event(ev, now_utc, summary, description))

        # Only show upcoming (or undated) events as preview cards on the share page
        start_at = ev.get("start_at") or ""
        if start_at:
            try:
                if datetime.fromisoformat(start_at).astimezone(timezone.utc) < datetime.now(timezone.utc):
                    continue
            except Exception:
                pass

        loc_obj = ev.get("location") or {}
        location = ", ".join(p for p in (loc_obj.get("name"), loc_obj.get("address")) if p)
        when_str = format_event_datetime_display(start_at, ev.get("is_all_day", False))
        image_url = ev.get("image_url") or ""
        share_url = ev.get("share_url") or ""
        raw_desc = (ev.get("description") or "").strip()

        img_block = (
            f'<img class="event-img" src="{html.escape(image_url)}" alt="{html.escape(name)}" loading="lazy">'
            if image_url else ""
        )
        loc_block = (
            f'<div class="meta-row">📍 {html.escape(location)}</div>'
            if location else ""
        )
        desc_block = (
            f'<p class="event-desc">{html.escape(raw_desc)}</p>'
            if raw_desc else ""
        )
        going_pills = "".join(f"<li>{html.escape(n)}</li>" for n in going_first)
        maybe_pills = "".join(f"<li>{html.escape(n)}</li>" for n in maybe_first)
        attendees_block = ""
        if going_first or maybe_first:
            parts = []
            if going_first:
                parts.append(f'<div class="rsvp-group"><strong>Going ({len(going_first)}):</strong><ul class="attendee-list">{going_pills}</ul></div>')
            if maybe_first:
                parts.append(f'<div class="rsvp-group"><strong>Maybe ({len(maybe_first)}):</strong><ul class="attendee-list">{maybe_pills}</ul></div>')
            attendees_block = f'<div class="attendees">{"".join(parts)}</div>'

        btn_block = (
            f'<a class="btn btn-outline" href="{html.escape(share_url)}" target="_blank" rel="noopener">Open in GroupMe &rarr;</a>'
            if share_url else ""
        )

        cards_html.append(f"""
        <article class="event-card">
            {img_block}
            <div class="event-body">
                <span class="badge">{html.escape(channel_label)}</span>
                <h3>{html.escape(name)}</h3>
                <div class="meta-row">🗓️ {html.escape(when_str)}</div>
                {loc_block}
                {desc_block}
                {attendees_block}
                {btn_block}
            </div>
        </article>""")

    ics_lines.append("END:VCALENDAR")
    ics_content = "\r\n".join(ics_lines) + "\r\n"

    with open("docs/hiking.ics", "w", encoding="utf-8") as f:
        f.write(ics_content)

    feed_url = f"{FEED_BASE_URL}/hiking.ics"
    gcal_url = f"https://calendar.google.com/calendar/render?cid={feed_url}"
    webcal_url = feed_url.replace("https://", "webcal://")
    cards_section = "\n".join(cards_html) if cards_html else '<p class="empty">No upcoming events scheduled right now — check back soon!</p>'

    hiking_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SLC Hiking &amp; Adventure Group — Event Calendar</title>
    <style>
        :root {{
            --bg: #f3f6f4;
            --surface: #ffffff;
            --primary: #1b5e3a;
            --primary-dark: #13442a;
            --accent: #e6f2ec;
            --text: #1f2933;
            --muted: #52606d;
            --border: #dce5df;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 0;
            line-height: 1.55;
        }}
        .hero {{
            background: linear-gradient(135deg, #164a2e 0%, #247349 100%);
            color: white;
            padding: 48px 20px 40px;
            text-align: center;
        }}
        .hero-inner {{ max-width: 820px; margin: 0 auto; }}
        .hero h1 {{ margin: 0 0 10px; font-size: 2rem; letter-spacing: -0.02em; }}
        .hero p {{ margin: 0 auto 24px; max-width: 600px; opacity: 0.92; font-size: 1.05rem; }}
        .cta-row {{ display: flex; flex-wrap: wrap; gap: 12px; justify-content: center; margin-bottom: 20px; }}
        .btn {{
            display: inline-block;
            padding: 11px 20px;
            border-radius: 8px;
            font-weight: 600;
            text-decoration: none;
            font-size: 0.95rem;
            transition: transform 0.12s ease, background 0.15s ease;
            cursor: pointer;
            border: none;
        }}
        .btn:hover {{ transform: translateY(-1px); }}
        .btn-white {{ background: #ffffff; color: var(--primary-dark); }}
        .btn-ghost {{ background: rgba(255,255,255,0.16); color: #ffffff; border: 1px solid rgba(255,255,255,0.35); }}
        .btn-outline {{ background: var(--accent); color: var(--primary-dark); margin-top: 12px; }}
        .url-box {{
            background: rgba(0,0,0,0.22);
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 8px;
            padding: 10px 14px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            max-width: 100%;
            font-size: 0.85rem;
        }}
        .url-box code {{ word-break: break-all; color: #e8f5ee; }}
        .copy-btn {{
            background: rgba(255,255,255,0.2);
            color: white;
            border: none;
            border-radius: 5px;
            padding: 5px 10px;
            cursor: pointer;
            font-size: 0.8rem;
            font-weight: 600;
            flex-shrink: 0;
        }}
        .container {{ max-width: 960px; margin: 36px auto; padding: 0 20px; }}
        .section-title {{ font-size: 1.35rem; margin: 0 0 18px; color: var(--primary-dark); }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 22px;
        }}
        .event-card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 4px 14px rgba(16, 42, 28, 0.06);
            display: flex;
            flex-direction: column;
        }}
        .event-img {{
            width: 100%;
            height: 190px;
            object-fit: cover;
            display: block;
            background: #e5ece8;
        }}
        .event-body {{ padding: 18px 20px 20px; display: flex; flex-direction: column; flex: 1; }}
        .badge {{
            display: inline-block;
            align-self: flex-start;
            background: var(--accent);
            color: var(--primary-dark);
            font-size: 0.76rem;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 999px;
            margin-bottom: 8px;
        }}
        .event-body h3 {{ margin: 0 0 8px; font-size: 1.15rem; line-height: 1.3; }}
        .meta-row {{ font-size: 0.88rem; color: var(--muted); margin-bottom: 5px; }}
        .event-desc {{
            font-size: 0.9rem;
            color: #334155;
            white-space: pre-line;
            margin: 12px 0;
            max-height: 160px;
            overflow-y: auto;
            padding-right: 4px;
        }}
        .attendees {{
            margin-top: auto;
            padding-top: 12px;
            border-top: 1px solid var(--border);
            font-size: 0.85rem;
        }}
        .rsvp-group {{ margin-bottom: 8px; }}
        .attendee-list {{
            list-style: none;
            padding: 0;
            margin: 4px 0 0;
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
        }}
        .attendee-list li {{
            background: #f1f5f3;
            border-radius: 6px;
            padding: 2px 8px;
            font-size: 0.8rem;
        }}
        footer {{
            text-align: center;
            color: var(--muted);
            font-size: 0.82rem;
            padding: 28px 20px 40px;
        }}
    </style>
</head>
<body>
    <header class="hero">
        <div class="hero-inner">
            <h1>🏔️ SLC Hiking &amp; Adventure Group</h1>
            <p>Live event calendar covering every channel — hikes, winter sports, pickleball, hangouts, and more. Subscribe once and stay synced automatically.</p>
            <div class="cta-row">
                <a class="btn btn-white" href="{gcal_url}" target="_blank" rel="noopener">+ Add to Google Calendar</a>
                <a class="btn btn-ghost" href="{webcal_url}">Apple / Outlook Calendar</a>
                <a class="btn btn-ghost" href="hiking.ics">Download .ics</a>
            </div>
            <div class="url-box">
                <code id="feedUrl">{feed_url}</code>
                <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('feedUrl').textContent).then(() => this.textContent = 'Copied!')">Copy URL</button>
            </div>
        </div>
    </header>
    <main class="container">
        <h2 class="section-title">Upcoming Group Events</h2>
        <div class="grid">
            {cards_section}
        </div>
    </main>
    <footer>
        Updated hourly from GroupMe &bull; Last synced {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &bull; {len(hiking_events)} total active events
    </footer>
</body>
</html>
"""
    with open("docs/hiking.html", "w", encoding="utf-8") as f:
        f.write(hiking_html)

    print(f"Successfully generated docs/hiking.ics and docs/hiking.html with {len(hiking_events)} hiking group events!")


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
            "group_id": gid,
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
                        "group_id": gid,
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
            # Some channels may not have events enabled
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

        # Calculate user RSVP
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

        # Event summary with channel prefix
        prefix = f"[{channel_name}] " if is_subgroup else f"[{group_name}] "
        summary = f"{rsvp_badge}{prefix}{name}"

        # Resolve attendee names
        going_names = [member_map.get(u, f"User {u}") for u in going_uids]
        maybe_names = [member_map.get(u, f"User {u}") for u in maybe_uids]

        # Construct rich description
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

        # Location
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

    # HTML Landing page
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GroupMe Calendar Feed</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #f7f9fc;
            color: #333;
            max-width: 720px;
            margin: 60px auto;
            padding: 24px;
        }}
        .card {{
            background: white;
            padding: 32px;
            border-radius: 12px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.08);
        }}
        h1 {{ margin-top: 0; color: #0070f3; }}
        code {{
            background: #eee;
            padding: 6px 10px;
            border-radius: 4px;
            word-break: break-all;
            display: block;
            margin: 12px 0;
            font-size: 14px;
        }}
        a.btn {{
            display: inline-block;
            background: #0070f3;
            color: white;
            text-decoration: none;
            padding: 10px 18px;
            border-radius: 6px;
            margin-top: 12px;
            font-weight: 600;
        }}
        .status {{
            color: #666;
            font-size: 13px;
            margin-top: 24px;
            border-top: 1px solid #eee;
            padding-top: 12px;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>GroupMe Calendar Feed</h1>
        <p>This feed automatically syncs your GroupMe group and channel events to Google Calendar.</p>
        <p><strong>Calendar Subscription URL:</strong></p>
        <code>https://half-fast-code.github.io/groupme-calendar-feed/events.ics</code>
        <a class="btn" href="events.ics">Download / View iCal Feed</a>
        <div class="status">
            Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} &bull; Active events synced: {len(all_events)} across {len(all_channels)} groups and channels
        </div>
    </div>
</body>
</html>
"""
    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\nSuccessfully generated docs/events.ics and docs/index.html with {len(all_events)} events!")

    generate_hiking_feed(all_events, member_map, now_utc)

if __name__ == "__main__":
    main()
