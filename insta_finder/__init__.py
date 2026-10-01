"""사진으로 인스타그램 게시물과 올린 사용자를 찾는 봇."""

from .ai_targets import SuggestionError, TargetSuggester, TargetSuggestion
from .crawl_finder import CrawlFinder, CrawlOptions
from .crawler import CrawlerError, InstagramSession, LoginError, Target, parse_targets
from .finder import FinderOptions, PhotoFinder
from .models import InstagramRef, Match, SearchReport
from .serpapi_client import SerpApiClient, SerpApiError

__all__ = [
    "CrawlFinder",
    "CrawlOptions",
    "CrawlerError",
    "FinderOptions",
    "InstagramRef",
    "InstagramSession",
    "LoginError",
    "Match",
    "PhotoFinder",
    "SearchReport",
    "SerpApiClient",
    "SerpApiError",
    "SuggestionError",
    "Target",
    "TargetSuggester",
    "TargetSuggestion",
    "parse_targets",
]

__version__ = "0.3.0"
