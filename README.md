![Alertbot banner](assets/alertbanner.png)

# Alertbot

A [maubot](https://github.com/maubot/maubot) plugin that receives webhooks from monitoring systems and forwards them as formatted messages to [Matrix](https://matrix.org) rooms.

## Supported webhook types

| Type | Detection | Source |
|------|-----------|--------|
| Alertmanager | `alerts[].labels.alertname` | Prometheus, custom rules, any Alertmanager source |
| Grafana | `alerts[].labels.grafana_folder` | Grafana alerting |
| Uptime Kuma | `heartbeat.status` | Uptime Kuma |
| Slack webhook | `text` + `attachments` | Slack-format payloads |

## Getting Started

You can either [invite](#invite-the-official-instance) an official instance of the bot, or [self-host](#self-host-an-instance) it on your own matrix server.

### Invite the Official Instance

* Create a Matrix room and invite @alertbot:hyteck.de
* Send `!url` to the room. The bot will answer with the webhook URL
* Put the Webhook URL into your monitoring solution (see below)

### Self-host an Instance

**Prerequisites:**
* A Matrix server where you have access to a maubot instance
   * Refer to the [docs](https://docs.mau.fi/maubot/usage/setup/index.html) for setting up one
   * or use [spantaleev/matrix-docker-ansible-deploy](https://github.com/spantaleev/matrix-docker-ansible-deploy/blob/master/docs/configuring-playbook-bot-matrix-registration-bot.md) which has built-in maubot support
* An instance of alertmanager or grafana or a similar alerting program that is able to send webhooks

**Build the Bot**

The bot is built using the maubot command line tool `mbc`, you can either build it locally or remotely.

Build it locally, and afterwards navigate to the Maubot Administration Interface and upload the .mpb file.
```shell
pip install maubot
git clone https://github.com/moan0s/alertbot
cd alertbot
mbc build
```

It's possible to upload the build to your maubot instance from the CLI. This is especially helpful when developing.
First login to your instance, then add the `-u` flag to upload after build.
```shell
mbc login
mbc build -u
```

**Configure the Bot**

Next, once the plugin is installed, set up a [client](https://docs.mau.fi/maubot/usage/basic.html#creating-clients) and then create an [instance](https://docs.mau.fi/maubot/usage/basic.html#creating-instances) which connects the client and plugin.

Finally, invite the bot to a Matrix room where alerts should be sent and query the bot for the room id with the command `!roomid`.

## Setup Alertmanager

This configuration will send all your alerts to a Matrix room. Put in your own room-id (`!roomid`) behind the webhook base url (`!url`):

```yaml
receivers:
  - name: alertbot
    webhook_configs:
      - url: 'https://maubot.example.com/_matrix/maubot/plugin/alertbot/webhook/!roomid:example.com'
        send_resolved: true

route:
  receiver: alertbot
  group_by: ['alertname']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 3h
```

**Example messages:**

Firing:
```
🔥 **InstanceDown** · critical

webserver.example.com of job node_exporter has been down for more than 5 minutes.

• **Instance:** webserver.example.com
• **Job:** node_exporter
• **Since:** 2024-03-30 08:00 UTC

Source · Silence
```

Resolved:
```
✅ **InstanceDown** · resolved

webserver.example.com of job node_exporter has been down for more than 5 minutes.

• **Instance:** webserver.example.com
• **Job:** node_exporter
• **Duration:** 2h 15m · **Resolved:** 2024-03-30 10:15 UTC

Source
```

## Setup Grafana

The grafana setup is fairly simple and can be used to forward grafana alerts to matrix.

![Screenshot of the Grafana Setup](assets/grafana.png)

## Send Test Alerts

Use an example from `alert_examples/` to test your setup:

```shell
curl --header "Content-Type: application/json" \
  --request POST \
  --data "@alert_examples/prometheus_alert.json" \
  https://maubot.example.com/_matrix/maubot/plugin/alertbot/webhook/!roomid:example.com
```

## Dependencies

- `python-dateutil>=2.8.2` (for timestamp parsing and duration calculation)

## Matrix Room

This project has a dedicated [chat room](https://matrix.to/#/#alertbot:hyteck.de)
