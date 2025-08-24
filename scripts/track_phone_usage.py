import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests

# Configuration
BEEMINDER_USERNAME = os.environ.get('BEEMINDER_USERNAME')
BEEMINDER_AUTH_TOKEN = os.environ.get('BEEMINDER_AUTH_TOKEN')
BEEMINDER_GOAL_SLUG = os.environ.get('BEEMINDER_GOAL_SLUG')
DB_FILE = 'data/phone_usage_db.json'
LAST_RUN_FILE = 'data/last_run.json'
# Timezone used for midnight-4 AM cutoff logic
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(message)s")
logger = logging.getLogger(__name__)


def calculate_beeminder_date(unlock_datetime):
    """Return the Beeminder date applying the 4 AM cutoff."""
    if unlock_datetime.hour < 4:
        unlock_datetime -= timedelta(days=1)
    return unlock_datetime.strftime("%Y-%m-%d")

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

def check_already_processed_date(target_date, beeminder_map, db):
    """Check if we've already processed this date and Beeminder has matching data."""
    if not os.path.exists(LAST_RUN_FILE):
        return False

    with open(LAST_RUN_FILE, 'r') as f:
        last_run_data = json.load(f)

    last_processed_date = last_run_data.get('last_processed_date')
    if last_processed_date != target_date:
        return False

    expected = next((dp for dp in db['datapoints'] if dp['date'] == target_date), None)
    beeminder_dp = beeminder_map.get(target_date)

    if not expected or not beeminder_dp:
        return False

    return (
        beeminder_dp.get('value') == expected.get('value', 1)
        and beeminder_dp.get('comment', '') == expected.get('comment', '')
    )

def update_last_run(processed_date):
    """Update the last run timestamp and processed date."""
    with open(LAST_RUN_FILE, 'w') as f:
        json.dump(
            {"last_run": datetime.now().isoformat(), "last_processed_date": processed_date},
            f,
        )

def get_beeminder_datapoints():
    """Fetch all datapoints from Beeminder."""
    url = f"https://www.beeminder.com/api/v1/users/{BEEMINDER_USERNAME}/goals/{BEEMINDER_GOAL_SLUG}/datapoints.json"
    params = {'auth_token': BEEMINDER_AUTH_TOKEN}
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        status = getattr(e.response, "status_code", "N/A")
        logger.error(f"Error fetching Beeminder data (status {status}): {e}")
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
        status = getattr(e.response, "status_code", "N/A")
        logger.error(f"Error adding datapoint to Beeminder (status {status}): {e}")
        return None

def update_beeminder_datapoint(datapoint_id, value, comment):
    """Update an existing Beeminder datapoint."""
    url = (
        f"https://www.beeminder.com/api/v1/users/{BEEMINDER_USERNAME}/goals/"
        f"{BEEMINDER_GOAL_SLUG}/datapoints/{datapoint_id}.json"
    )
    data = {'auth_token': BEEMINDER_AUTH_TOKEN, 'value': value, 'comment': comment}
    try:
        response = requests.put(url, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        status = getattr(e.response, "status_code", "N/A")
        logger.error(f"Error updating datapoint {datapoint_id} (status {status}): {e}")
        return None

def get_beeminder_date_map(datapoints):
    """Return mapping of date -> datapoint dict."""
    date_map = {}
    for dp in datapoints:
        date = datetime.fromtimestamp(dp['timestamp']).strftime('%Y-%m-%d')
        date_map[date] = dp
    return date_map

def sync_datapoints(db, beeminder_map):
    """Sync missing or mismatched datapoints from DB to Beeminder."""
    synced_count = 0
    failed_syncs = []
    for dp in db['datapoints']:
        existing = beeminder_map.get(dp['date'])
        if not existing:
            logger.info(f"Syncing missing datapoint for {dp['date']}")
            result = add_beeminder_datapoint(
                dp['date'],
                dp.get('value', 1),
                dp.get('comment', 'Historical late night phone usage (synced)'),
            )
            if result:
                synced_count += 1
            else:
                failed_syncs.append(dp['date'])
        elif existing.get('value') != dp.get('value', 1) or existing.get('comment', '') != dp.get('comment', ''):
            logger.info(f"Updating mismatched datapoint for {dp['date']}")
            result = update_beeminder_datapoint(
                existing['id'],
                dp.get('value', 1),
                dp.get('comment', 'Historical late night phone usage (synced)'),
            )
            if result:
                synced_count += 1
            else:
                failed_syncs.append(dp['date'])

    if failed_syncs:
        logger.warning(f"Failed to sync datapoints: {', '.join(failed_syncs)}")
    return synced_count, failed_syncs

def main(trigger_date=None):
    """Main function to handle the phone usage tracking."""
    ensure_directories()
    
    # Load database
    db = load_database()

    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    if trigger_date:
        date_part = datetime.strptime(trigger_date, "%Y-%m-%d")
        unlock_dt = tz.localize(
            date_part.replace(
                hour=now.hour,
                minute=now.minute,
                second=now.second,
                microsecond=now.microsecond,
            )
        )
    else:
        unlock_dt = now

    beeminder_date = calculate_beeminder_date(unlock_dt)

    logger.info(f"Processing phone usage for Beeminder date: {beeminder_date}")

    existing_dates = [dp['date'] for dp in db['datapoints']]
    if beeminder_date not in existing_dates:
        new_datapoint = {
            'date': beeminder_date,
            'value': 1,
            'timestamp': unlock_dt.isoformat(),
            'comment': 'Late night phone usage detected',
        }
        db['datapoints'].append(new_datapoint)
        save_database(db)
        logger.info(f"Added datapoint for {beeminder_date} to database.")
    else:
        logger.info(f"Datapoint for {beeminder_date} already exists in database.")

    beeminder_datapoints = get_beeminder_datapoints()
    beeminder_map = get_beeminder_date_map(beeminder_datapoints)

    synced, failures = sync_datapoints(db, beeminder_map)
    if synced:
        logger.info(f"Synced {synced} historical datapoint(s) to Beeminder.")
    if failures:
        logger.warning(f"Some datapoints failed to sync: {', '.join(failures)}")

    beeminder_datapoints = get_beeminder_datapoints()
    beeminder_map = get_beeminder_date_map(beeminder_datapoints)

    if check_already_processed_date(beeminder_date, beeminder_map, db):
        logger.info(f"Already processed datapoint for {beeminder_date}. Skipping.")
    else:
        if beeminder_date not in beeminder_map:
            result = add_beeminder_datapoint(beeminder_date)
            if result:
                logger.info(f"Successfully added datapoint for {beeminder_date} to Beeminder.")
            else:
                logger.error(f"Failed to add datapoint for {beeminder_date} to Beeminder.")
                return
        else:
            logger.info(f"Datapoint for {beeminder_date} already exists in Beeminder.")

    update_last_run(beeminder_date)
    logger.info("Workflow completed successfully.")

if __name__ == "__main__":
    # Accept optional date argument
    trigger_date = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
    main(trigger_date)

