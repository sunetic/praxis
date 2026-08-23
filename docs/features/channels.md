# Channels

Channels deliver Praxis messages to external collaboration platforms. The currently configurable channels are:

- DingTalk custom robots.
- Slack Incoming Webhooks.
- Telegram bots.

Lark and WeCom appear as unavailable and should not be treated as supported capabilities.

## Configuration

Each channel includes setup guidance and a test message. DingTalk supports signature, keyword, or IP allowlist security modes, Markdown templates, and mentions. Slack uses an Incoming Webhook and can set a username, icon, and channel. Telegram uses a Bot Token and Chat ID, with optional parse mode and silent notification.

## Security guidance

- Treat webhooks, signing secrets, and bot tokens as credentials.
- Grant minimal send permissions and restrict the target group or channel.
- Send a test message after saving to verify formatting, recipients, and rate-limit behavior.
- Do not include database passwords, full SQL parameters, or sensitive result sets in default message templates.

Channel providers may rate-limit or reject messages. Automation should check task execution and notification delivery as separate outcomes.
