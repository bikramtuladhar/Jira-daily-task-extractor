import os
from jira import JIRA
from jira.exceptions import JIRAError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from tzlocal import get_localzone

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# JIRA authentication details from environment variables
jira_server = os.getenv('JIRA_SERVER')
username = os.getenv('JIRA_USERNAME')
api_token = os.getenv('JIRA_API_TOKEN')

logger.info(f"Connecting to JIRA at {jira_server}")

jira = JIRA(server=jira_server, basic_auth=(username, api_token))

# Define the start date custom field ID (update this to match your Jira configuration)
START_DATE_FIELD_ID = "customfield_10014"  # Replace with your actual custom field ID


def fetch_previous_working_day():
    tz = get_localzone()
    now = datetime.now(tz)
    if now.weekday() == 0:  # Monday
        previous_working_day = now - timedelta(days=3)
    else:
        previous_working_day = now - timedelta(days=1)

    start_of_day = previous_working_day.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = (start_of_day + timedelta(days=1)) - timedelta(seconds=1)
    return start_of_day, end_of_day


def fetch_daily_activities():
    try:
        start_of_day, end_of_day = fetch_previous_working_day()

        # Format dates to 'yyyy-MM-dd HH:mm'
        start_of_day_str = start_of_day.strftime('%Y-%m-%d %H:%M')
        end_of_day_str = end_of_day.strftime('%Y-%m-%d %H:%M')
        print(start_of_day_str, end_of_day_str)
        # JQL query to fetch issues updated within the previous working day by the current user, excluding the DEV project
        jql_query = f'updated >= "{start_of_day_str}" AND updated < "{end_of_day_str}" AND assignee = currentUser() AND project != DEV ORDER BY updated DESC'
        issues = jira.search_issues(jql_query, expand='changelog', maxResults=100)

        activity_list = []

        for issue in issues:
            # Fetch issue details
            issue_key = issue.key
            issue_summary = issue.fields.summary
            issue_link = f"{jira_server}/browse/{issue_key}"
            original_estimate = issue.fields.timeoriginalestimate
            time_spent = issue.fields.timespent
            created = issue.fields.created.split("T")[0]
            updated = issue.fields.updated.split("T")[0]

            # Fetch issue comments
            comments = jira.comments(issue)
            comment_texts = [
                {
                    "body": comment.body,
                    "created": comment.created.split("T")[0]
                }
                for comment in comments
                if comment.created > start_of_day_str and comment.body
            ]

            # Add issue details to activity list
            activity_list.append({
                'issue_key': issue_key,
                'issue_summary': issue_summary,
                'issue_link': issue_link,
                'original_estimate': original_estimate,
                'time_spent': time_spent,
                'comments': comment_texts,
                'created': created,
                'updated': updated,
            })

        return activity_list

    except JIRAError as e:
        if e.status_code == 401:
            logger.error("Authentication failed: Incorrect username or API token")
        elif e.status_code == 403:
            logger.error("Authentication failed: Forbidden, check your permissions")
        elif e.status_code == 404:
            logger.error("Authentication failed: JIRA server URL not found")
        else:
            logger.error(f"JIRA Error: {e.text}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")
    return None


def create_daily_work_log(activity_string):
    try:
        # Get current user's name
        current_user = jira.current_user()
        current_user_display_name = jira.user(current_user).displayName
        logger.info(f"Current user: {current_user_display_name}")

        # Get current date details
        now = datetime.now()
        day = now.strftime('%d')
        month = now.strftime('%b')
        year = now.strftime('%Y')
        today_str = now.strftime('%Y-%m-%d')
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)

        month_start_str = month_start.strftime('%Y-%m-%d')
        month_end_str = month_end.strftime('%Y-%m-%d')

        # Search for the epic with the current user's name
        epic_jql = f'project = DEV AND issuetype = Epic AND summary ~ "{current_user_display_name}"'
        epics = jira.search_issues(epic_jql)

        if not epics:
            logger.error(f"No epic found with the summary containing '{current_user_display_name}'")
            return

        epic_key = epics[0].key
        logger.info(f"Found epic: {epic_key}")

        # Search for the monthly issue created in the current month
        monthly_issue_summary = f"{current_user_display_name}"
        monthly_jql = f'project = DEV AND type = Task AND summary ~ "{monthly_issue_summary}" AND created >= "{month_start_str}" AND created <= "{month_end_str}"'
        monthly_issues = jira.search_issues(monthly_jql)

        if not monthly_issues:
            logger.error(f"No issue found with the summary '{monthly_issue_summary}' created in the current month")
            return

        monthly_issue_key = monthly_issues[0].key
        logger.info(f"Found monthly issue: {monthly_issue_key}")

        # Search for existing sub-tasks with the same start date
        sub_task_jql = f'parent = {monthly_issue_key} AND "Start date" = "{today_str}"'
        existing_sub_tasks = jira.search_issues(sub_task_jql)

        if existing_sub_tasks:
            sub_task_key = existing_sub_tasks[0].key
            logger.info(f"Existing sub-task found with the start date today: {sub_task_key}")

            # Add activity string as a comment to the existing sub-task
            jira.add_comment(sub_task_key, activity_string)
            logger.info(f"Added comment to existing sub-task: {sub_task_key}")
        else:
            sub_task_summary = f"{day}, {month}"
            # Create a new sub-task under the monthly issue
            sub_task_data = {
                "project": {"key": "DEV"},
                "parent": {"key": monthly_issue_key},
                "summary": sub_task_summary,
                "description": activity_string,
                "issuetype": {"name": "Sub-task"},
                START_DATE_FIELD_ID: today_str
            }

            sub_task = jira.create_issue(fields=sub_task_data)
            logger.info(f"Created sub-task with key: {sub_task.key} under issue: {monthly_issue_key}")

    except JIRAError as e:
        logger.error(f"JIRA Error: {e.text}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")


def format_time(seconds):
    if seconds is None:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


if __name__ == "__main__":
    # Fetch daily activities
    daily_activities = fetch_daily_activities()
    if daily_activities:
        activity_string = ""
        total_original_estimate = 0
        total_time_spent = 0
        for activity in daily_activities:

            original_estimate = activity['original_estimate'] or 0
            time_spent = activity['time_spent'] or 0
            total_original_estimate += original_estimate
            total_time_spent += time_spent

            formatted_original_estimate = format_time(original_estimate)
            formatted_time_spent = format_time(time_spent)
            activity_string += (
                f"{{panel:title={activity['issue_key']} - {activity['issue_summary']}|borderStyle=dashed|borderColor=#A9A9A9|titleBGColor=#E6F7E6|bgColor=#deebff}}\n"
                f"*Link*: [{activity['issue_link']}]\n"
                f"*Original Estimate*: {formatted_original_estimate}\n"
                f"*Created*: {activity['created']}\n"
                f"*Updated*: {activity['updated']}\n"
                f"*Time Spent*: {formatted_time_spent}\n\n"
                f"{'*Comments:*' if len(activity['comments']) > 0 else ''}\n"
                f"{'-' * len(activity['comments'][0]['body']) if len(activity['comments']) > 0 else ''}\n"
            )
            for comment in activity['comments']:
                activity_string += f"* {comment['body']}\n"
                activity_string += f"date: {comment['created']}\n"
                activity_string += f"{'-' * len(comment['body'])}\n"
            activity_string += "{panel}\n\n----\n\n"

        # Add total time spent and total original estimated time
        total_formatted_original_estimate = format_time(total_original_estimate)
        total_formatted_time_spent = format_time(total_time_spent)
        activity_string += (
            f"{{panel:title=Total Time Summary|borderStyle=dashed|borderColor=#A9A9A9|titleBGColor=#E6F7E6|bgColor=#deebff}}\n"
            f"*Total Original Estimate*: {total_formatted_original_estimate}\n"
            f"*Total Time Spent*: {total_formatted_time_spent}\n"
            "{panel}\n"
        )

        # Create daily work log
        create_daily_work_log(activity_string)
    else:
        logger.info("No activities found for today or an error occurred.")
