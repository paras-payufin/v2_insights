"""
Email Sending Functionality
Handles SMTP connection and email delivery
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from config.settings import SENDER_EMAIL
from utils.utils import SMTP_PASSWORD, SMTP_PORT, SMTP_SERVER, SMTP_USERNAME


def send_email(subject, html_content, text_content, recipients, attachments=None):
    """
    Send email via AWS SES SMTP.

    Args:
        subject:     Email subject line
        html_content: HTML version of email body
        text_content: Plain text version of email body
        recipients:  List of recipient email addresses or single email string
        attachments: Optional list of dicts with keys:
                       - filename (str): name shown in email, e.g. "report.html"
                       - content  (str|bytes): file content
                       - mimetype (str): e.g. "text/html" or "application/octet-stream"

    Returns:
        bool: True if email sent successfully
    """
    recipients_list = [recipients] if isinstance(recipients, str) else list(recipients)

    # Use 'mixed' when there are attachments, otherwise 'alternative'
    if attachments:
        msg = MIMEMultipart('mixed')
        # Body part must be 'alternative' nested inside 'mixed'
        body_part = MIMEMultipart('alternative')
        body_part.attach(MIMEText(text_content, 'plain', 'utf-8'))
        body_part.attach(MIMEText(html_content, 'html', 'utf-8'))
        msg.attach(body_part)
    else:
        msg = MIMEMultipart('alternative')
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = ', '.join(recipients_list)

    # Attach files
    for att in (attachments or []):
        maintype, subtype = att.get('mimetype', 'application/octet-stream').split('/', 1)
        part = MIMEBase(maintype, subtype)
        content = att['content']
        part.set_payload(content.encode('utf-8') if isinstance(content, str) else content)
        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            'attachment',
            filename=att['filename'],
        )
        msg.attach(part)

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients_list, msg.as_string())
        server.quit()

        attachment_note = f" + {len(attachments)} attachment(s)" if attachments else ""
        print(f"✓ Email sent successfully to: {', '.join(recipients_list)}{attachment_note}")
        return True

    except Exception as e:
        print(f"✗ Email sending failed: {str(e)}")
        raise
