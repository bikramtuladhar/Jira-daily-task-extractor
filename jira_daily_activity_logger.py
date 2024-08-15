import os
import logging
from datetime import datetime, timedelta
from jira import JIRA
from jira.exceptions import JIRAError
from dotenv import load_dotenv
import pytz
from tzlocal import get_localzone

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# JIRA authentication details
jira_server = os.getenv('JIRA_SERVER')
username = os.getenv('JIRA_USERNAME')
api_token = os.getenv('JIRA_API_TOKEN')

logger.info(f"Connecting to JIRA at {jira_server}")
jira = JIRA(server=jira_server, basic_auth=(username, api_token))

# Define the start date custom field ID
START_DATE_FIELD_ID = "customfield_10014"  # Replace with your actual custom field ID
local_tz = get_localzone()
tokyo_tz = pytz.timezone('Asia/Tokyo')

def get_previous_working_day(custom_date=None):
    """
    Fetches the start and end datetime for the previous working day based on the custom date.
    If no custom date is provided, it defaults to the previous working day.
    """
    if custom_date:
        try:
            now_local = datetime.strptime(custom_date, '%Y-%m-%d')
        except ValueError:
            logger.error(f"Invalid date format: {custom_date}. Please use 'YYYY-MM-DD'.")
            return None, None
    else:
        now_local = datetime.now(local_tz)
        day_delta = {0: 3, 6: 2, 5: 1}.get(now_local.weekday(), 1)
        now_local -= timedelta(days=day_delta)

    start_of_day = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = start_of_day + timedelta(days=1) - timedelta(seconds=1)

    return start_of_day.astimezone(tokyo_tz), end_of_day.astimezone(tokyo_tz)

def fetch_issues_for_day(start_of_day, end_of_day):
    """
    Fetches the issues updated by the current user within the specified day.
    """
    start_of_day_str = start_of_day.strftime('%Y-%m-%d %H:%M')
    end_of_day_str = end_of_day.strftime('%Y-%m-%d %H:%M')
    jql_query = (
        f'updated >= "{start_of_day_str}" AND updated < "{end_of_day_str}" '
        f'AND assignee = currentUser() AND project != DEV ORDER BY updated DESC'
    )
    return jira.search_issues(jql_query, expand='changelog', maxResults=100)

def fetch_worklog_for_issue(issue_key, start_of_day, end_of_day):
    """
    Fetches the worklog entries for the given issue within the specified day.
    """
    worklogs = jira.worklogs(issue_key)
    worklog_time_total = sum(
        worklog.timeSpentSeconds
        for worklog in worklogs
        if start_of_day <= datetime.strptime(worklog.started, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(tokyo_tz) <= end_of_day
    )
    return worklog_time_total

def fetch_comments_for_issue(issue, start_of_day_str):
    """
    Fetches the comments for the given issue made after the start of the day.
    """
    return [
        {"body": comment.body, "created": datetime.strptime(comment.created,'%Y-%m-%dT%H:%M:%S.%f%z').astimezone(local_tz).strftime('%Y-%m-%d %H:%M')}
        for comment in jira.comments(issue)
        if comment.created > start_of_day_str and comment.body
    ]

def fetch_status_changes_for_issue(issue, start_of_day, end_of_day):
    """
    Fetches the status changes for the given issue within the specified day.
    """
    status_changes = []
    for history in issue.changelog.histories:
        for item in history.items:
            if item.field == 'status':
                status_change_time = datetime.strptime(history.created, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(tokyo_tz)
                if start_of_day <= status_change_time <= end_of_day:
                    status_changes.append({
                        "from": item.fromString,
                        "to": item.toString,
                        "date": status_change_time.astimezone(local_tz).strftime('%Y-%m-%d %H:%M')
                    })
    return status_changes

def format_issue_activity(issue, start_of_day, end_of_day):
    """
    Formats the issue activity into a structured dictionary.
    """
    issue_key = issue.key
    issue_summary = issue.fields.summary
    issue_link = f"{jira_server}/browse/{issue_key}"

    worklog_time_total = fetch_worklog_for_issue(issue_key, start_of_day, end_of_day)
    comments = fetch_comments_for_issue(issue, start_of_day.strftime('%Y-%m-%d %H:%M'))
    status_changes = fetch_status_changes_for_issue(issue, start_of_day, end_of_day)

    return {
        'issue_key': issue_key,
        'issue_summary': issue_summary,
        'issue_link': issue_link,
        'original_estimate': issue.fields.timeoriginalestimate or 0,
        'time_spent': worklog_time_total,
        'comments': comments,
        'status_changes': status_changes,
        'created': datetime.strptime(issue.fields.created, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(local_tz).strftime('%Y-%m-%d %H:%M'),
        'updated': datetime.strptime(issue.fields.updated, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(local_tz).strftime('%Y-%m-%d %H:%M'),
    }

def fetch_daily_activities(custom_date=None):
    """
    Fetches and structures the daily activities for the specified or previous working day.
    """
    start_of_day, end_of_day = get_previous_working_day(custom_date)
    if not start_of_day or not end_of_day:
        return None

    issues = fetch_issues_for_day(start_of_day, end_of_day)
    activity_list = [format_issue_activity(issue, start_of_day, end_of_day) for issue in issues]
    return activity_list

def create_sub_task(monthly_issue_key, day, month, activity_string, today_str, current_user_display_name):
    """
    Creates a new sub-task under the monthly issue with the given details.
    """
    sub_task_summary = f"{day}, {month}"
    sub_task_data = {
        "project": {"key": "DEV"},
        "parent": {"key": monthly_issue_key},
        "summary": sub_task_summary,
        "description": activity_string,
        "issuetype": {"name": "Sub-task"},
        START_DATE_FIELD_ID: today_str
    }
    sub_task = jira.create_issue(fields=sub_task_data)
    jira.assign_issue(sub_task.key, current_user_display_name)
    logger.info(f"Created sub-task with key: {sub_task.key} under issue: {monthly_issue_key}")

def create_daily_work_log(activity_string):
    """
    Creates or updates a daily work log in Jira by either adding a comment to an existing sub-task or creating a new one.
    """
    try:
        current_user = jira.current_user()
        current_user_display_name = jira.user(current_user).displayName
        logger.info(f"Current user: {current_user_display_name}")

        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)

        epic_jql = f'project = DEV AND issuetype = Epic AND summary ~ "{current_user_display_name}"'
        epics = jira.search_issues(epic_jql)
        if not epics:
            logger.error(f"No epic found with the summary containing '{current_user_display_name}'")
            return

        epic_key = epics[0].key
        logger.info(f"Found epic: {epic_key}")

        monthly_jql = (
            f'project = DEV AND type = Task AND summary ~ "{current_user_display_name}" '
            f'AND created >= "{month_start.strftime("%Y-%m-%d")}" AND created <= "{month_end.strftime("%Y-%m-%d")}"'
        )
        monthly_issues = jira.search_issues(monthly_jql)
        if not monthly_issues:
            logger.error(f"No issue found with the summary '{current_user_display_name}' created in the current month")
            return

        monthly_issue_key = monthly_issues[0].key
        logger.info(f"Found monthly issue: {monthly_issue_key}")

        sub_task_jql = f'parent = {monthly_issue_key} AND "Start date" = "{today_str}"'
        existing_sub_tasks = jira.search_issues(sub_task_jql)

        if existing_sub_tasks:
            sub_task_key = existing_sub_tasks[0].key
            logger.info(f"Existing sub-task found with the start date today: {sub_task_key}")
            jira.add_comment(sub_task_key, activity_string)
            logger.info(f"Added comment to existing sub-task: {sub_task_key}")
        else:
            create_sub_task(monthly_issue_key, now.strftime('%d'), now.strftime('%b'), activity_string, today_str, current_user_display_name)

    except JIRAError as e:
        logger.error(f"JIRA Error: {e.text}")
    except Exception as e:
        logger.error(f"An unexpected error occurred: {str(e)}")

def format_time(seconds):
    """
    Formats seconds into hours and minutes as a string.
    """
    if seconds is None:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"

def main():
    custom_date = input("Enter the date (YYYY-MM-DD) to fetch activities for (leave empty for previous working day): ").strip() or None

    daily_activities = fetch_daily_activities(custom_date)
    if not daily_activities:
        logger.info("No activities found for the specified date or an error occurred.")
        return

    activity_string = ""
    total_original_estimate = 0
    total_time_spent = 0

    for activity in daily_activities:
        original_estimate = activity['original_estimate']
        time_spent = activity['time_spent']
        total_original_estimate += original_estimate
        total_time_spent += time_spent

        activity_string += (
            f"{{panel:title={activity['issue_key']} - {activity['issue_summary']}|borderStyle=dashed|borderColor=#A9A9A9|titleBGColor=#E6F7E6|bgColor=#deebff}}\n"
            f"*Link*: [{activity['issue_link']}]\n"
            f"*Original Estimate*: {format_time(original_estimate)}\n"
            f"*Created*: {activity['created']}\n"
            f"*Updated*: {activity['updated']}\n"
            f"*Time Spent*: {format_time(time_spent)}\n"
        )

        if activity['status_changes']:
            activity_string += "\n*Status Changes:*\n"
            for change in activity['status_changes']:
                activity_string += f" - From '{change['from']}' to '{change['to']}' on {change['date']}\n"

        if activity['comments']:
            activity_string += "\n*Comments:*\n"
            for comment in activity['comments']:
                activity_string += f"* {comment['body']} (on {comment['created']})\n"

        activity_string += "{panel}\n\n----\n\n"

    activity_string += (
        f"{{panel:title=Total Time Summary|borderStyle=dashed|borderColor=#A9A9A9|titleBGColor=#E6F7E6|bgColor=#deebff}}\n"
        f"*Total Original Estimate*: {format_time(total_original_estimate)}\n"
        f"*Total Time Spent*: {format_time(total_time_spent)}\n"
        "{panel}\n"
    )

    create_daily_work_log(activity_string)

if __name__ == "__main__":
    main()
