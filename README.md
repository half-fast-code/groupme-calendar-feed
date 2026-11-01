# GroupMe Calendar Feed & Google Calendar Sync

An automated, serverless sync service that turns GroupMe group calendars into a live iCal (`.ics`) subscription feed for Google Calendar, Apple Calendar, and Outlook.

## 🚀 Live Calendar Subscription

Once deployed to GitHub Pages, subscribe in **Google Calendar**:
1. Open [Google Calendar](https://calendar.google.com).
2. On the left sidebar next to **Other calendars**, click **`+`** → **From URL**.
3. Paste your feed URL:
   ```
   https://half-fast-code.github.io/groupme-calendar-feed/events.ics
   ```
4. Click **Add calendar**.

Google Calendar will automatically refresh and pull in any upcoming events, locations, and updates.

## ⚙️ How It Works
- Runs on **GitHub Actions** hourly (or manually via Workflow Dispatch).
- Fetches active group events via the GroupMe API.
- Generates a standard RFC 5545 iCalendar (`.ics`) feed in `docs/events.ics`.
- Publishes to GitHub Pages with 0 hosting costs and 24/7 uptime.
