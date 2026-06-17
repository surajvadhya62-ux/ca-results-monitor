import os
import ssl
import socket

# Force IPv4 — Render free tier doesn't support IPv6 outbound,
# and smtp.gmail.com resolves to IPv6 by default causing
# "[Errno 101] Network is unreachable"
_original_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(*args, **kwargs):
    responses = _original_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET] or responses
socket.getaddrinfo = _getaddrinfo_ipv4
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import hashlib
import json
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration (use environment variables)
RESULTS_URL = os.getenv('RESULTS_URL', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_TO = os.getenv('EMAIL_TO', '')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '465'))
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', '1'))

# Store previous page hash to detect changes
SNAPSHOT_FILE = 'page_snapshot.json'

def get_page_content(url):
    """Fetch the webpage content"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Extract text content for comparison (ignores formatting changes)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        text = soup.get_text()
        # Clean up whitespace
        text = ' '.join(text.split())
        
        return text, response.status_code
    except Exception as e:
        logger.error(f"Error fetching page: {str(e)}")
        return None, None

def calculate_hash(content):
    """Calculate hash of content for comparison"""
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def load_snapshot():
    """Load previous snapshot from file"""
    try:
        if os.path.exists(SNAPSHOT_FILE):
            with open(SNAPSHOT_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading snapshot: {str(e)}")
    return {'hash': None, 'timestamp': None}

def save_snapshot(content_hash):
    """Save current snapshot to file"""
    try:
        with open(SNAPSHOT_FILE, 'w') as f:
            json.dump({
                'hash': content_hash,
                'timestamp': datetime.now().isoformat()
            }, f)
    except Exception as e:
        logger.error(f"Error saving snapshot: {str(e)}")

def send_email_notification(subject, body):
    """Send email notification via Gmail SMTP_SSL (port 465)"""
    if not EMAIL_FROM or not EMAIL_PASSWORD or not EMAIL_TO:
        logger.error(
            "Email not configured. Set EMAIL_FROM, EMAIL_PASSWORD, EMAIL_TO env vars. "
            f"Currently: FROM={'SET' if EMAIL_FROM else 'EMPTY'}, "
            f"PASS={'SET' if EMAIL_PASSWORD else 'EMPTY'}, "
            f"TO={'SET' if EMAIL_TO else 'EMPTY'}"
        )
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        
        email_body = (
            f"{body}\n\n"
            f"---\n"
            f"Check Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Results URL: {RESULTS_URL}\n\n"
            f"This is an automated notification from your CA Results Monitor."
        )
        
        msg.attach(MIMEText(email_body, 'plain', 'utf-8'))
        
        # Use SMTP_SSL (port 465) — more reliable on cloud platforms like Render
        context = ssl.create_default_context()
        
        if SMTP_PORT == 465:
            # Direct SSL connection (recommended)
            logger.info(f"Connecting to {SMTP_SERVER}:{SMTP_PORT} via SMTP_SSL...")
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, context=context) as server:
                server.login(EMAIL_FROM, EMAIL_PASSWORD)
                server.send_message(msg)
        else:
            # Fallback: STARTTLS on port 587
            logger.info(f"Connecting to {SMTP_SERVER}:{SMTP_PORT} via STARTTLS...")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(EMAIL_FROM, EMAIL_PASSWORD)
                server.send_message(msg)
        
        logger.info(f"✅ Email sent successfully to {EMAIL_TO}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            f"❌ Gmail authentication failed: {str(e)}. "
            "Make sure you're using a Gmail App Password (not your regular password). "
            "Generate one at: https://myaccount.google.com/apppasswords"
        )
        return False
    except smtplib.SMTPException as e:
        logger.error(f"❌ SMTP error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected email error ({type(e).__name__}): {str(e)}")
        return False

def check_results():
    """Main function to check for results changes"""
    logger.info(f"Checking results at {datetime.now()}")
    
    if not RESULTS_URL:
        logger.error("RESULTS_URL not configured")
        return
    
    # Fetch current page content
    content, status_code = get_page_content(RESULTS_URL)
    
    if content is None:
        send_email_notification(
            "⚠️ CA Results Monitor - Connection Error",
            f"Failed to connect to the results page. Status code: {status_code}\n\n"
            "Please check:\n- The URL is correct\n- Your internet connection\n- The website is accessible"
        )
        return
    
    # Calculate hash of current content
    current_hash = calculate_hash(content)
    
    # Load previous snapshot
    snapshot = load_snapshot()
    previous_hash = snapshot.get('hash')
    
    # Check for changes
    if previous_hash is None:
        # First run - just save the snapshot
        save_snapshot(current_hash)
        logger.info("Initial snapshot saved")
        send_email_notification(
            "✅ CA Results Monitor - Started",
            "Monitor has started successfully.\n"
            f"I'll check your results page every {CHECK_INTERVAL_MINUTES} minute(s) and notify you if anything changes."
        )
    elif current_hash != previous_hash:
        # Content has changed!
        logger.warning("🎉 RESULTS PAGE HAS CHANGED!")
        save_snapshot(current_hash)
        send_email_notification(
            "🎉 CA RESULTS - PAGE CHANGED!",
            "The results page has changed!\n\n"
            "This could mean your CA results have been released.\n\n"
            f"Please visit immediately:\n{RESULTS_URL}\n\n"
            "Check your results right away!"
        )
    else:
        logger.info("No changes detected")

def start_scheduler():
    """Start the background scheduler"""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        check_results,
        'interval',
        minutes=CHECK_INTERVAL_MINUTES,
        id='ca_results_check'
    )
    scheduler.start()
    logger.info(f"Scheduler started - checking every {CHECK_INTERVAL_MINUTES} minute(s)")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.now().isoformat(),
        'config': {
            'results_url': RESULTS_URL or 'NOT SET',
            'email_from': 'SET' if EMAIL_FROM else 'NOT SET',
            'email_to': 'SET' if EMAIL_TO else 'NOT SET',
            'email_password': 'SET' if EMAIL_PASSWORD else 'NOT SET',
            'check_interval': CHECK_INTERVAL_MINUTES,
            'smtp_server': SMTP_SERVER,
            'smtp_port': SMTP_PORT,
        }
    }), 200

@app.route('/check-now', methods=['POST'])
def check_now():
    """Manual trigger for checking results"""
    check_results()
    return jsonify({'status': 'check triggered', 'timestamp': datetime.now().isoformat()}), 200

@app.route('/test-email', methods=['POST'])
def test_email():
    """Send a test email to verify email configuration"""
    success = send_email_notification(
        "🧪 CA Results Monitor - Test Email",
        "This is a test email from your CA Results Monitor.\n"
        "If you received this, your email configuration is working correctly!"
    )
    return jsonify({
        'status': 'email sent' if success else 'email failed',
        'timestamp': datetime.now().isoformat()
    }), 200 if success else 500

if __name__ == '__main__':
    # Log configuration on startup
    logger.info("=" * 50)
    logger.info("CA Results Monitor Starting...")
    logger.info(f"  RESULTS_URL: {RESULTS_URL or 'NOT SET'}")
    logger.info(f"  EMAIL_FROM:  {'SET' if EMAIL_FROM else 'NOT SET'}")
    logger.info(f"  EMAIL_TO:    {'SET' if EMAIL_TO else 'NOT SET'}")
    logger.info(f"  EMAIL_PASS:  {'SET' if EMAIL_PASSWORD else 'NOT SET'}")
    logger.info(f"  SMTP:        {SMTP_SERVER}:{SMTP_PORT}")
    logger.info(f"  INTERVAL:    {CHECK_INTERVAL_MINUTES} minute(s)")
    logger.info("=" * 50)
    
    # Run initial check on startup
    check_results()
    
    # Start scheduler
    start_scheduler()
    
    # Run Flask app
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)