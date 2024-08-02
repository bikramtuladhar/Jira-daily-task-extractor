import os

import pandas as pd
from jira import JIRA
from jira.exceptions import JIRAError
from dotenv import load_dotenv

load_dotenv()

# JIRA authentication details from environment variables
jira_server = os.getenv('JIRA_SERVER')
username = os.getenv('JIRA_USERNAME')
api_token = os.getenv('JIRA_API_TOKEN')

try:
    # Initialize JIRA client
    jira = JIRA(server=jira_server, basic_auth=(username, api_token))
    print("Authenticated successfully")
except JIRAError as e:
    if e.status_code == 401:
        print("Authentication failed: Incorrect username or API token")
    elif e.status_code == 403:
        print("Authentication failed: Forbidden, check your permissions")
    elif e.status_code == 404:
        print("Authentication failed: JIRA server URL not found")
    else:
        print(f"Authentication failed: {e.text}")

# Load the CSV file
file_path = 'data.csv'  # replace with your actual file path
jira_data = pd.read_csv(file_path)


# Function to update time spent for a specific JIRA issue
def update_time_spent(issue_key, new_time_spent):
    try:
        issue = jira.issue(issue_key)
        if issue:
            # Convert time spent from seconds to JIRA time tracking format (e.g., "1h 30m")
            hours = new_time_spent // 3600
            minutes = (new_time_spent % 3600) // 60
            time_spent_str = f"{hours}h {minutes}m"

            # Update the time tracking field
            jira.add_worklog(issue, timeSpent=time_spent_str)
            print(f"Updated {issue_key} to {time_spent_str}.")
        else:
            print(f"Issue {issue_key} not found.")
    except JIRAError as e:
        if e.status_code == 404:
            print(f"Issue {issue_key} not found or you don't have permission to view it.")
        else:
            print(f"Failed to update {issue_key}: {e.text}")


# Iterate over the dataframe and update each issue
for index, row in jira_data.iterrows():
    update_time_spent(row['Key'], row['Time Spent'])

print("All issues updated successfully.")
