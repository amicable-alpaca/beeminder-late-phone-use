import json
import os
import sys
import requests
from datetime import datetime, timedelta
import pytz
from pathlib import Path

# Configuration
BEEMINDER_USERNAME = os.environ.get('BEEMINDER_USERNAME', 'zarathustra')
BEEMINDER_AUTH_TOKEN = os.environ.get('BEEMINDER_AUTH_TOKEN', 'Koy57AUAxgSxw1QhfHRz')
BEEMINDER_GOAL_SLUG = os.environ.get('BEEMINDER_GOAL_SLUG', 'usingphonelate')
DB_FILE = 'data/phone_usage_db.json'
LAST_RUN_FILE = 'data/last_run.json'

def ensure_directories():
    """Create necessary directories if they don't exist."""
    Path('data').mkdir(exist_ok=True)

def load_database():
    """Load the source of truth database."""
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {'datapoints': [], 'metadata': {'created': datetime.now().isoformat()}}

def save_database(db):
    """Save the source of truth database."""
    with open(DB_FILE, 'w') as f:
        json.dump(db, f, indent=2)

def check_already_run_today():
    """Check if we've already run for this 11PM-4AM window."""
    if not os.path.exists(LAST_RUN_FILE):
        return False
    
    with open(LAST_RUN_FILE, 'r') as f:
        last_run_data = json.load(f)
    
    last_run = datetime.fromisoformat(last_run_data['last_run'])
    now = datetime.now()
    
    # Define the current window
    if now.hour >= 23:
        window_start = now.replace(hour=23, minute=0, second=0, microsecond=0)
    elif now.hour < 4:
        window_start = (now - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
    else:
        # Not in the target window, allow run
        return False
    
    # Check if last run was in the same window
    return last_run >= window_start

def update_last_run():
    """Update the last run timestamp."""
    with open(LAST_RUN_FILE, 'w') as f:
        json.dump({'last_run': datetime.now().isoformat()}, f)

def get_beeminder_datapoints():
    """Fetch all datapoints from Beeminder."""
    url = f"https://www.beeminder.com/api/v1/users/{BEEMINDER_USERNAME}/goals/{BEEMINDER_GOAL_SLUG}/datapoints.json"
    params = {'auth_token': BEEMINDER_AUTH_TOKEN}
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Beeminder data: {e}")
        return []

def add_beeminder_datapoint(date_str, value=1, comment="Late night phone usage detected"):
    """Add a datapoint to Beeminder."""
    url = f"https://www.beeminder.com/api/v1/users/{BEEMINDER_USERNAME}/goals/{BEEMINDER_GOAL_SLUG}/datapoints.json"
    
    # Convert date string to timestamp
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    timestamp = int(date_obj.timestamp())
    
    data = {
        'auth_token': BEEMINDER_AUTH_TOKEN,
        'timestamp': timestamp,
        'value': value,
        'comment': comment,
        'requestid': f"phone_usage_{date_str}"  # Idempotency key
    }
    
    try:
        response = requests.post(url, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error adding datapoint to Beeminder: {e}")
        return None

def sync_datapoints(db, beeminder_datapoints):
    """Sync missing datapoints from DB to Beeminder."""
    # Create a set of dates that exist in Beeminder
    beeminder_dates = set()
    for dp in beeminder_datapoints:
        # Convert timestamp to date
        date = datetime.fromtimestamp(dp['timestamp']).strftime('%Y-%m-%d')
        beeminder_dates.add(date)
    
    # Find missing datapoints in Beeminder
    synced_count = 0
    for dp in db['datapoints']:
        if dp['date'] not in beeminder_dates:
            print(f"Syncing missing datapoint for {dp['date']}")
            result = add_beeminder_datapoint(
                dp['date'], 
                dp.get('value', 1),
                dp.get('comment', 'Historical late night phone usage (synced)')
            )
            if result:
                synced_count += 1
    
    return synced_count

def main(trigger_date=None):
    """Main function to handle the phone usage tracking."""
    ensure_directories()
    
    # Check if we've already run for this window
    if check_already_run_today():
        print("Already processed for this 11PM-4AM window. Skipping.")
        return
    
    # Load database
    db = load_database()
    
    # Get current date or use provided date
    if trigger_date:
        current_date = trigger_date
    else:
        now = datetime.now()
        # If it's after midnight but before 4 AM, use yesterday's date
        if now.hour < 4:
            current_date = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            current_date = now.strftime('%Y-%m-%d')
    
    print(f"Processing phone usage for date: {current_date}")
    
    # Check if we already have a datapoint for this date
    existing_dates = [dp['date'] for dp in db['datapoints']]
    if current_date in existing_dates:
        print(f"Datapoint for {current_date} already exists in database.")
    else:
        # Add to source of truth database
        new_datapoint = {
            'date': current_date,
            'value': 1,
            'timestamp': datetime.now().isoformat(),
            'comment': 'Late night phone usage detected'
        }
        db['datapoints'].append(new_datapoint)
        save_database(db)
        print(f"Added datapoint for {current_date} to database.")
    
    # Get Beeminder datapoints
    beeminder_datapoints = get_beeminder_datapoints()
    
    # Sync any missing historical datapoints
    synced = sync_datapoints(db, beeminder_datapoints)
    if synced > 0:
        print(f"Synced {synced} historical datapoints to Beeminder.")
    
    # Check if today's datapoint exists in Beeminder
    beeminder_dates = set()
    for dp in beeminder_datapoints:
        date = datetime.fromtimestamp(dp['timestamp']).strftime('%Y-%m-%d')
        beeminder_dates.add(date)
    
    if current_date not in beeminder_dates:
        # Add today's datapoint to Beeminder
        result = add_beeminder_datapoint(current_date)
        if result:
            print(f"Successfully added datapoint for {current_date} to Beeminder.")
        else:
            print(f"Failed to add datapoint for {current_date} to Beeminder.")
    else:
        print(f"Datapoint for {current_date} already exists in Beeminder.")
    
    # Update last run time
    update_last_run()
    print("Workflow completed successfully.")

if __name__ == "__main__":
    # Accept optional date argument
    trigger_date = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    main(trigger_date)