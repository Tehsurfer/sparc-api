import logging
from datetime import datetime

from app.config import Config
from dateutil.relativedelta import relativedelta
from googleapiclient.discovery import build
from oauth2client.service_account import ServiceAccountCredentials

SCOPE = Config.GOOGLE_API_GA_SCOPE
KEY_PATH = Config.GOOGLE_API_GA_KEY_PATH
VIEW_ID = Config.GOOGLE_API_GA_VIEW_ID


def init_ga_reporting():
    try:
        credentials = ServiceAccountCredentials.from_json_keyfile_name(
            KEY_PATH,
            SCOPE
        )
        analytics = build('analyticsreporting', 'v4', credentials=credentials)
        return analytics

    except Exception as e:
        logging.error('An error occured while instantiating the GA reporter.', e)
        return None


def get_ga_1year_sessions(analytics):

    start_date = datetime.now() - relativedelta(years=1)
    formatted_start_date = start_date.strftime('%Y-%m-%d')

    try:
        report = analytics.reports().batchGet(
            body={
                "reportRequests": [{
                    "viewId": VIEW_ID,
                    "dateRanges": [{
                        "startDate": formatted_start_date,
                        "endDate": datetime.now().strftime('%Y-%m-%d')
                    }],
                    "metrics": [{"expression": "ga:sessions"}]
                }]
            }
        ).execute()

        if len(report["reports"]):
            total = report["reports"][0]["data"]["totals"][0]["values"][0]
            return int(total)

    except:
        return None

def get_ga_page_views_report(analytics, months=1):

    start_date = datetime.now() - relativedelta(months=months)
    formatted_start_date = start_date.strftime('%Y-%m-%d')
    report = analytics.reports().batchGet(
        body={
         "reportRequests": [{
           "pageSize": "100000",
           "viewId": VIEW_ID,
           "dimensions": [{
             "name": "ga:pagePath"
            }],
           "metrics": [
            {
             "expression": "ga:pageviews"
            }
           ],
           "dateRanges": [
            {
             "startDate": formatted_start_date,
             "endDate": datetime.now().strftime('%Y-%m-%d')
            }
           ]
          }
         ]
        }
    ).execute()

    return report

def get_views_for_pages(analytics, pages=[], months=1):
    report = get_ga_page_views_report(analytics, months)
    views_for_page = {}
    try:
        page_data = report['reports'][0]['data']['rows']
        for page in pages:
            views_for_page[page] = 0
            for pd in page_data:
                if page in pd['dimensions'][0]:
                    views_for_page[page] += int(pd['metrics'][0]['values'][0])
    except KeyError:
        return {}
    return views_for_page
