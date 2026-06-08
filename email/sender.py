"""
Email Sending Functionality
Handles SMTP connection and email delivery
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from config.settings import SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SENDER_EMAIL


def send_email(subject, html_content, text_content, recipients):
    """
    Send email via AWS SES SMTP
    
    Args:
        subject: Email subject line
        html_content: HTML version of email body
        text_content: Plain text version of email body
        recipients: List of recipient email addresses or single email string
        
    Returns:
        bool: True if email sent successfully
    """
    # Normalize recipients to list
    recipients_list = [recipients] if isinstance(recipients, str) else recipients
    
    # Create message
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SENDER_EMAIL
    msg['To'] = ', '.join(recipients_list)
    
    # Attach both plain text and HTML versions
    msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_content, 'html', 'utf-8'))
    
    # Send via SMTP
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
