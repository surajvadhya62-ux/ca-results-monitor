import os
import requests
from bs4 import BeautifulSoup
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
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
EMAIL_TO = os.getenv('EMAIL_TO', '')
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
    """Send email notification via Resend HTTP API (works on Render free tier)"""
    if not RESEND_API_KEY or not EMAIL_TO:
        logger.error(
            "Email not configured. Set RESEND_API_KEY and EMAIL_TO env vars. "
            f"Currently: RESEND_API_KEY={'SET' if RESEND_API_KEY else 'EMPTY'}, "
            f"EMAIL_TO={'SET' if EMAIL_TO else 'EMPTY'}"
        )
        return False

    try:
        email_body = (
            f"{body}\n\n"
            f"---\n"
            f"Check Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Results URL: {RESULTS_URL}\n\n"
            f"This is an automated notification from your CA Results Monitor."
        )

        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json'
            },
            json={
                'from': 'CA Results Monitor <onboarding@resend.dev>',
                'to': [EMAIL_TO],
                'subject': subject,
                'text': email_body
            },
            timeout=10
        )

        if response.status_code == 200:
            logger.info(f"✅ Email sent successfully to {EMAIL_TO}")
            return True
        else:
            logger.error(f"❌ Resend API error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        logger.error(f"❌ Email error ({type(e).__name__}): {str(e)}")
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
            'resend_api_key': 'SET' if RESEND_API_KEY else 'NOT SET',
            'email_to': 'SET' if EMAIL_TO else 'NOT SET',
            'check_interval': CHECK_INTERVAL_MINUTES,
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
    logger.info(f"  RESULTS_URL:     {RESULTS_URL or 'NOT SET'}")
    logger.info(f"  RESEND_API_KEY:  {'SET' if RESEND_API_KEY else 'NOT SET'}")
    logger.info(f"  EMAIL_TO:        {'SET' if EMAIL_TO else 'NOT SET'}")
    logger.info(f"  INTERVAL:        {CHECK_INTERVAL_MINUTES} minute(s)")
    logger.info("=" * 50)
    
    # Run initial check on startup
    check_results()
    
    # Start scheduler
    start_scheduler()
    
    # Run Flask app
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)