# Phone Usage Tracking Automation

This repository contains the automation system for tracking late-night phone usage (11 PM - 4 AM) and reporting it to Beeminder.

## Setup Instructions

### 1. Repository Setup
1. Fork or create this repository on GitHub
2. Add the following repository secrets (Settings → Secrets and variables → Actions):
   - `BEEMINDER_USERNAME`: Your Beeminder username
   - `BEEMINDER_AUTH_TOKEN`: Your Beeminder auth token
   - `BEEMINDER_GOAL_SLUG`: Your Beeminder goal slug

### 2. MacroDroid Configuration
Configure MacroDroid on your Android device to trigger the GitHub Action when you unlock your phone between 11 PM and 4 AM.

### 3. File Structure
```
├── .github/
│   └── workflows/
│       └── phone-usage-tracker.yml  # GitHub Action workflow
├── scripts/
│   └── track_phone_usage.py         # Main tracking script
├── data/                             # Database directory (auto-created)
│   ├── phone_usage_db.json          # Source of truth database
│   └── last_run.json                # Tracks last run to prevent duplicates
└── README.md                         # This file
```

## How It Works

1. **MacroDroid Detection**: Detects phone unlocks between 11 PM - 4 AM
2. **GitHub Action Trigger**: MacroDroid sends a webhook to trigger the GitHub Action
3. **Database Management**: Creates/maintains a source of truth database in the repository
4. **Beeminder Sync**: Syncs all datapoints with Beeminder, ensuring consistency
5. **Duplicate Prevention**: Ensures only one datapoint per night is recorded

## Manual Testing

You can manually trigger the workflow from the Actions tab in GitHub to test the system.

## Troubleshooting

- Check the Actions tab in GitHub for workflow run logs
- Ensure your GitHub PAT has the correct permissions
- Verify MacroDroid has internet permissions and can send HTTP requests
- Check that repository secrets are correctly set

## Privacy Note

Your Beeminder credentials are stored as GitHub secrets and are never exposed in logs or commits.
