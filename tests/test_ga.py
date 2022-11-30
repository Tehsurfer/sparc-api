import requests
from app.config import Config
from app.metrics.ga import get_ga_1year_sessions, init_ga_reporting, get_ga_page_views_report, get_views_for_pages
from nose.tools import assert_true

def test_ga_login():
    ga = init_ga_reporting()
    d = get_ga_1year_sessions(ga)
    assert_true(d is not None)

def test_ga_page_view_report():
    ga = init_ga_reporting()
    d = get_ga_page_views_report(ga)
    assert_true(d is not None)


def test_ga_on_pages():
    ga = init_ga_reporting()
    d = get_views_for_pages(ga, ['/datasets/76', '/maps', '/data', '/datasets/46'])
    assert_true(d is not None)

