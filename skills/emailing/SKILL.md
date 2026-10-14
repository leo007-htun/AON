---
name: emailing
description: Send an email via SMTP with optional attachments
---

# Emailing Skill

The **emailing** skill allows the agent to send plain text or HTML emails to one or more recipients via an SMTP server. It accepts various parameters to configure the email content and delivery.

## Parameters

| Key | Type | Required | Default | Description |
|-----|------|----------|---------|-------------|
| `to` | string | ✓ | N/A | Recipient email address (comma‑separated for multiple). |
| `subject` | string | ✓ | N/A | Subject line of the email. |
| `body` | string | ✓ | N/A | Email body text. Supports `\n` for line breaks. |
| `sender` | string | ✗ | `config.email.sender` | Sender email address. |
| `from_name` | string | ✗ | Same as `sender` | Optional display name for the sender. |
| `attachments` | array | ✗ | [] | Optional list of objects: `{"filename": "file.txt", "content": "<base64>"}` |
| `smtp_host` | string | ✗ | `config.smtp.host` | SMTP server host. |
| `smtp_port` | int | ✗ | `config.smtp.port` | SMTP server port. |
| `smtp_username` | string | ✗ | `config.smtp.username` | SMTP authentication username. |
| `smtp_password` | string | ✗ | `config.smtp.password` | SMTP authentication password. |
| `use_tls` | bool | ✗ | `true` | Whether to use TLS for the connection. |

## Configuration

Provide default values via `config.yml` or environment variables:

```yaml
email:
  sender: "noreply@example.com"

smtp:
  host: "smtp.example.com"
  port: 587
  username: "smtp_user"
  password: "smtp_pass"
  use_tls: true
```

## Workflow

1. **Validate** that required fields (`to`, `subject`, `body`) are present.
2. **Construct MIME message**. If there are attachments, create a `multipart/mixed` message, otherwise a simple `plain` or `html` message.
3. **Connect to SMTP** using `smtp_host` and `smtp_port`. If `use_tls` is true, secure the connection.
4. **Authenticate** if credentials are provided.
5. **Send** the message and **log** for debugging.
6. **Return** JSON result: `{"success": true, "message_id": "<msg-id>"}` or `{"success": false, "error": "<error>"}`.

## Example Call

```python
await skill.run({
  "to": "alice@example.com,bob@example.org",
  "subject": "Project Update",
  "body": "Hi team,\nHere is the latest update...",
  "attachments": [
    {"filename": "report.pdf", "content": "<base64string>"}
  ]
})
```

## Error Handling
- Authentication failures, connection timeouts, or invalid email formats will be caught and an error message returned.
- Attachment encoding issues will be reported.

## Dependencies
Only standard Python libraries: `smtplib`, `email`, `base64`, `socket`.

---