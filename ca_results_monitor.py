import os
import requests
from bs4 import BeautifulSoup
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import hashlib
import json
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration (use environment variables)
RESULTS_URL = os.getenv('RESULTS_URL', '')
EMAIL_FROM = os.getenv('EMAIL_FROM', '')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD', '')
EMAIL_TO = os.getenv('EMAIL_TO', '')
SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
CHECK_INTERVAL_MINUTES = int(os.getenv('CHECK_INTERVAL_MINUTES', 1))

# Store previous page hash to detect changes
SNAPSHOT_FILE = 'page_snapshot.json'

def get_page_content(url):
    """Fetch the webpage content"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
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
    return hashlib.md5(content.encode()).hexdigest()

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

def send_email_notification(subject, body, previous_hash=None, current_hash=None):
    """Send email notification"""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_FROM
        msg['To'] = EMAIL_TO
        msg['Subject'] = subject
        
        email_body = f"""
{body}

---
Check Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Results URL: {RESULTS_URL}

This is an automated notification from your CA Results Monitor.
        """
        
        msg.attach(MIMEText(email_body, 'plain'))
        
        # Connect to SMTP server
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.send_message(msg)
        
        logger.info("Email notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
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
            f"Failed to connect to the results page. Status code: {status_code}\n\nPlease check:\n- The URL is correct\n- Your internet connection\n- The website is accessible"
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
            "Monitor has started successfully.\nI'll check your results page every 15 minutes and notify you if anything changes."
        )
    elif current_hash != previous_hash:
        # Content has changed!
        logger.warning("RESULTS PAGE HAS CHANGED!")
        save_snapshot(current_hash)
        send_email_notification(
            "🎉 CA RESULTS - PAGE CHANGED!",
            "The results page has changed!\n\nThis could mean your CA results have been released.\n\nPlease visit immediately:\n" + RESULTS_URL + "\n\nCheck your results right away!"
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
    logger.info(f"Scheduler started - checking every {CHECK_INTERVAL_MINUTES} minutes")

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return {'status': 'ok', 'timestamp': datetime.now().isoformat()}, 200

@app.route('/check-now', methods=['POST'])
def check_now():
    """Manual trigger for checking results"""
    check_results()
    return {'status': 'check triggered', 'timestamp': datetime.now().isoformat()}, 200

if __name__ == '__main__':
    # Run initial check on startup
    check_results()
    
    # Start scheduler
    start_scheduler()
    
    # Run Flask app
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
