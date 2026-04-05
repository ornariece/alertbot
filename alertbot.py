import json
from urllib.parse import quote

import dateutil.parser
from aiohttp.web import Request, Response, json_response
from maubot import Plugin, MessageEvent
from maubot.handlers import web, command
from mautrix.errors.request import MForbidden

helpstring = f"""# Alertbot

To control the alertbot you can use the following commands:
* `!help`: To show this help
* `!ping`: To check if the bot is alive
* `!raw`: To toggle raw mode (where webhook data is not parsed but simply forwarded as copyable text)
* `!roomid`: To let the bot show you the current matrix room id
* `!url`: To let the bot show you the webhook url

More information is on [Github](https://github.com/moan0s/alertbot)
"""


def convert_slack_webhook_to_markdown(data):
    # Error Handling: Check if data is a dictionary
    if not isinstance(data, dict):
        return ["Input data must be a dictionary"]

    markdown_parts = []
    attachment_titles = []

    if "text" in data:
        markdown_parts.append(f"{data['text']}")

    if "attachments" in data:
        for attach in data["attachments"]:
            if "title" in attach:
                attachment_titles.append(attach['title'])
                title_md = f"## {attach['title']}" if "title_link" not in attach else f"[{attach['title']}]({attach['title_link']})"
                markdown_parts.append(f"> {title_md}")

            for key in ["text", "image_url"]:
                if key in attach and attach[key] is not None:
                    extra_md = attach[key] if key == "text" else f"![Image]({attach[key]})"
                    markdown_parts.append(f"> {extra_md}")

            if 'fields' in attach:
                field_parts = [f"- **{field['title']}** : {field['value']}" for field in attach['fields']]
                markdown_parts.extend([f"> {part}" for part in field_parts])

    if "sections" in data:
        markdown_parts.append("")
        for section in data["sections"]:
            if "activityTitle" in section and section['activityTitle'] not in attachment_titles:
                markdown_parts.append(f"## {section['activityTitle']}")
            if "activitySubtitle" in section:
                markdown_parts.append(section['activitySubtitle'])

    return ['\n'.join(markdown_parts)]


def get_alert_type(data):
    """Detect the type of incoming webhook payload.

    Supported types:
      - slack-webhook: Slack-format payload with text + attachments
      - uptime-kuma-alert / uptime-kuma-resolved: Uptime Kuma heartbeat
      - grafana-alert / grafana-resolved: Grafana alert with grafana_folder label
      - alertmanager-alert / alertmanager-resolved: Any Alertmanager webhook
        (includes Prometheus-originated alerts — the "job" label is NOT required)
      - not-found: Unrecognized format
    """

    if ("text" in data) and ("attachments" in data):
        return "slack-webhook"

    # Uptime-kuma has heartbeat
    try:
        if data["heartbeat"]["status"] == 0:
            return "uptime-kuma-alert"
        elif data["heartbeat"]["status"] == 1:
            return "uptime-kuma-resolved"
    except (KeyError, TypeError):
        pass

    # Grafana
    try:
        if data["alerts"][0]["labels"]["grafana_folder"]:
            if data['status'] == "firing":
                return "grafana-alert"
            else:
                return "grafana-resolved"
    except (KeyError, IndexError, TypeError):
        pass

    # Generic Alertmanager webhook: has alerts[] with labels.alertname.
    # This covers Prometheus-originated alerts, custom alerts, and any other
    # source that sends through Alertmanager. No "job" label required.
    try:
        if data["alerts"][0]["labels"]["alertname"]:
            if data['status'] == "firing":
                return "alertmanager-alert"
            else:
                return "alertmanager-resolved"
    except (KeyError, IndexError, TypeError):
        pass

    return "not-found"


def _format_duration(start_str, end_str):
    """Calculate human-readable duration between two ISO 8601 timestamps."""
    try:
        start = dateutil.parser.isoparse(start_str)
        end = dateutil.parser.isoparse(end_str)
        delta = end - start
        total_seconds = int(delta.total_seconds())
        if total_seconds < 0:
            return None

        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)
    except (ValueError, TypeError):
        return None


def _format_timestamp(iso_str):
    """Format an ISO 8601 timestamp as 'YYYY-MM-DD HH:MM UTC'."""
    try:
        dt = dateutil.parser.isoparse(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        return iso_str


def _build_silence_url(external_url, alertname):
    """Build a pre-filled Alertmanager silence URL for a given alertname."""
    if not external_url:
        return None
    base = external_url.rstrip("/")
    filter_param = quote('{alertname="' + alertname + '"}')
    return f"{base}/#/silences/new?filter={filter_param}"


# Labels already shown in the title line — no need to repeat in metadata
_SKIP_LABELS = {"alertname", "severity"}


def alertmanager_to_markdown(alert_data: dict) -> list:
    """Convert an Alertmanager webhook payload to formatted markdown messages.

    Produces one message per alert in the payload. Each message follows
    a structured format optimized for mobile notification clients:

      1. Status emoji + alert name + severity  (= push notification text)
      2. Description
      3. Label metadata (one per line)
      4. Timestamps
      5. Source + silence links

    Args:
        alert_data: The full Alertmanager webhook payload dict.

    Returns:
        List of markdown-formatted message strings.
    """
    messages = []
    external_url = alert_data.get("externalURL", "")

    for alert in alert_data.get("alerts", []):
        status = alert.get("status", "unknown")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        alertname = labels.get("alertname", "Unknown")
        severity = labels.get("severity", "")

        # --- Title line ---
        emoji = "\u2705" if status == "resolved" else "\U0001f525"
        severity_text = f" \u00b7 {severity}" if severity and status != "resolved" else ""
        status_text = " \u00b7 resolved" if status == "resolved" else ""
        title = f"{emoji} **{alertname}**{severity_text}{status_text}"

        # --- Description ---
        description = annotations.get("description") or annotations.get("summary", "")

        # --- Label metadata (as list items for proper line breaks) ---
        meta_lines = []
        for key, value in labels.items():
            if key not in _SKIP_LABELS:
                display_key = key.replace("_", " ").title()
                meta_lines.append(f"- **{display_key}:** {value}")

        # --- Timestamps ---
        starts_at = alert.get("startsAt", "")
        ends_at = alert.get("endsAt", "")

        if status == "resolved":
            time_parts = []
            duration = _format_duration(starts_at, ends_at)
            if duration:
                time_parts.append(f"**Duration:** {duration}")
            time_parts.append(f"**Resolved:** {_format_timestamp(ends_at)}")
            meta_lines.append("- " + " \u00b7 ".join(time_parts))
        elif starts_at:
            meta_lines.append(f"- **Since:** {_format_timestamp(starts_at)}")

        # --- Links ---
        links = []
        generator_url = alert.get("generatorURL", "")
        if generator_url:
            links.append(f"[Source]({generator_url})")
        silence_url = _build_silence_url(external_url, alertname)
        if silence_url and status != "resolved":
            links.append(f"[Silence]({silence_url})")
        link_line = " \u00b7 ".join(links)

        # --- Assemble message ---
        parts = [title, ""]
        if description:
            parts.append(description)
            parts.append("")
        for ml in meta_lines:
            parts.append(ml)
        if link_line:
            parts.append("")
            parts.append(link_line)

        messages.append("\n".join(parts).strip())

    return messages


def get_alert_messages(alert_data: dict, raw_mode=False) -> list:
    """
    Returns a list of messages in markdown format

    :param alert_data: The data send to the bot as dict
    :param raw_mode: Toggles a mode where the data is not parsed but simply returned as code block in a message
    :return: List of alert messages in markdown format
    """

    alert_type = get_alert_type(alert_data)

    if raw_mode:
        return ["**Data received**\n```\n" + str(alert_data).strip("\n").strip() + "\n```"]
    elif alert_type == "not-found":
        return ["**Data received**\n " + dict_to_markdown(alert_data)]
    else:
        try:
            if alert_type == "slack-webhook":
                messages = convert_slack_webhook_to_markdown(alert_data)
            elif alert_type in ("grafana-alert", "grafana-resolved"):
                messages = grafana_alert_to_markdown(alert_data)
            elif alert_type in ("alertmanager-alert", "alertmanager-resolved"):
                messages = alertmanager_to_markdown(alert_data)
            elif alert_type == "uptime-kuma-alert":
                messages = uptime_kuma_alert_to_markdown(alert_data)
            elif alert_type == "uptime-kuma-resolved":
                messages = uptime_kuma_resolved_to_markdown(alert_data)
        except KeyError as e:
            messages = ["**Data received**\n```\n" + str(alert_data).strip(
                "\n").strip() + f"\n```\nThe data was detected as {alert_type} but was not in an expected format. If you want to help the development of this bot, file a bug report [here](https://github.com/moan0s/alertbot/issues)\n{e}"]
    return messages


def uptime_kuma_alert_to_markdown(alert_data: dict):
    tags_readable = ", ".join([tag["name"] for tag in alert_data["monitor"]["tags"]])
    message = (
        f"""**Firing 🔥**: Monitor down: {alert_data["monitor"]["url"]}

* **Error:** {alert_data["heartbeat"]["msg"]}
* **Started at:** {alert_data["heartbeat"]["time"]}
* **Tags:** {tags_readable}
* **Source:** "Uptime Kuma"
                    """
    )
    return [message]


def dict_to_markdown(alert_data: dict):
    md = ""
    for key_or_dict in alert_data:
        try:
            alert_data[key_or_dict]
        except TypeError:
            md += "  " + dict_to_markdown(key_or_dict)
            continue
        if not (isinstance(alert_data[key_or_dict], str) or isinstance(alert_data[key_or_dict], int)):
            md += "  " + dict_to_markdown(alert_data[key_or_dict])
        else:
            md += f"* {key_or_dict}: {alert_data[key_or_dict]}\n"
    return md


def uptime_kuma_resolved_to_markdown(alert_data: dict):
    tags_readable = ", ".join([tag["name"] for tag in alert_data["monitor"]["tags"]])
    message = (
        f"""**Resolved 💚**: {alert_data["monitor"]["url"]}

* **Status:** {alert_data["heartbeat"]["msg"]}
* **Started at:** {alert_data["heartbeat"]["time"]}
* Duration until resolved {alert_data["heartbeat"]["duration"]}s
* **Tags:** {tags_readable}
* **Source:** "Uptime Kuma"
    """
    )
    return [message]


def grafana_alert_to_markdown(alert_data: dict) -> list:
    """
    Converts a grafana alert json to markdown

    :param alert_data:
    :return: Alerts as formatted markdown string list
    """
    messages = []
    for alert in alert_data["alerts"]:
        if alert['status'] == "firing":
            message = (
                f"""**Firing 🔥**: {alert['labels']['alertname']}  
    
* **Instance:** {alert["valueString"]}
* **Silence:** {alert["silenceURL"]}
* **Started at:** {alert['startsAt']}
* **Fingerprint:** {alert['fingerprint']}
                """
            )
        if alert['status'] == "resolved":
            end_at = dateutil.parser.isoparse(alert['endsAt'])
            start_at = dateutil.parser.isoparse(alert['startsAt'])
            message = (
                f"""**Resolved 🥳**: {alert['labels']['alertname']}
    
* **Duration until resolved:** {end_at - start_at}
* **Fingerprint:** {alert['fingerprint']}
                """
            )
        messages.append(message)
    return messages


class AlertBot(Plugin):
    raw_mode = False

    async def send_alert(self, req, room):
        text = await req.text()
        self.log.info(text)
        content = json.loads(text)
        for message in get_alert_messages(content, self.raw_mode):
            self.log.debug(f"Sending alert to {room}")
            await self.client.send_markdown(room, message)

    @web.post("/webhook/{room_id}")
    async def webhook_room(self, req: Request) -> Response:
        room_id = req.match_info["room_id"].strip()
        try:
            await self.send_alert(req, room=room_id)
        except MForbidden:
            self.log.error(f"Could not send to {room_id}: Forbidden. Most likely the bot is not invited in the room.")
            return json_response('{"status": "forbidden", "error": "forbidden"}', status=403)
        except json.JSONDecodeError:
            self.log.error(f"Decoding the data failed for {room_id}")
            return json_response({"status": "failed", "error": "JSON decoding failed"}, status=400)
        return json_response({"status": "ok"})

    @command.new()
    async def ping(self, evt: MessageEvent) -> None:
        """Answers pong to check if the bot is running"""
        await evt.reply("pong")

    @command.new()
    async def roomid(self, evt: MessageEvent) -> None:
        """Answers with the current room id"""
        await evt.reply(f"`{evt.room_id}`")

    @command.new()
    async def url(self, evt: MessageEvent) -> None:
        """Answers with the url of the webhook"""
        await evt.reply(f"`{self.webapp_url}/webhook/{evt.room_id}`")

    @command.new()
    async def raw(self, evt: MessageEvent) -> None:
        self.raw_mode = not self.raw_mode
        """Switches the bot to raw mode or disables raw mode (mode where data is not formatted but simply forwarded)"""
        await evt.reply(f"Mode is now: `{'raw' if self.raw_mode else 'normal'} mode`")

    @command.new()
    async def help(self, evt: MessageEvent) -> None:
        await self.client.send_markdown(evt.room_id, helpstring)
