"""
Email Sending Functionality
Handles SMTP connection and email delivery
"""
import smtplib
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from config.settings import SENDER_EMAIL
from utils.utils import SMTP_PASSWORD, SMTP_PORT, SMTP_SERVER, SMTP_USERNAME


def send_email(
    subject,
    html_content,
    text_content,
    recipients,
    attachment_bytes=None,
    attachment_filename=None,
):
    """
    Send email via AWS SES SMTP, with an optional file attachment.

    Args:
        subject:             Email subject line
        html_content:        HTML version of email body
        text_content:        Plain text version of email body
        recipients:          List of recipient email addresses or single email string
        attachment_bytes:    Raw bytes of a file to attach (optional)
        attachment_filename: Filename shown in the email (optional, required if attachment_bytes set)

    Returns:
        bool: True if email sent successfully
    """
    recipients_list = [recipients] if isinstance(recipients, str) else list(recipients)

    if attachment_bytes:
        # mixed: allows both body alternatives and an attachment
        outer = MIMEMultipart('mixed')
        outer['Subject'] = subject
        outer['From'] = SENDER_EMAIL
        outer['To'] = ', '.join(recipients_list)

        body = MIMEMultipart('alternative')
        body.attach(MIMEText(text_content, 'plain', 'utf-8'))
        body.attach(MIMEText(html_content, 'html', 'utf-8'))
        outer.attach(body)

        part = MIMEBase('application', 'octet-stream')
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header(
            'Content-Disposition',
            f'attachment; filename="{attachment_filename}"',
        )
        outer.attach(part)
        msg = outer
    else:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = SENDER_EMAIL
        msg['To'] = ', '.join(recipients_list)
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))

    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipients_list, msg.as_string())
        server.quit()

        print(f"✓ Email sent successfully to: {', '.join(recipients_list)}")
        return True

    except Exception as e:
        print(f"✗ Email sending failed: {str(e)}")
        raise
