import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlparse

import requests

IST = datetime.now().astimezone().tzinfo
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_DOWNLOAD_DIR = DATA_DIR / "nse_postclose_downloads"
DEFAULT_FAILURE_LOG = DATA_DIR / "nse_postclose_failures.json"
DEFAULT_SYMBOLS_OUTPUT = DATA_DIR / "nse_postclose_symbols.txt"
DEFAULT_SUMMARY_OUTPUT = DATA_DIR / "nse_postclose_summary.json"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

CORPORATE_SOURCES = (
    {
        "name": "announcements",
        "url": "https://www.nseindia.com/api/corporate-announcements?index=equities",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        "symbol_fields": ("symbol",),
        "timestamp_fields": ("exchdisstime", "an_dt", "sort_date"),
        "date_fields": ("an_dt", "sort_date"),
        "attachment_fields": ("attchmntFile",),
    },
    {
        "name": "actions",
        "url": "https://www.nseindia.com/api/corporates-corporateActions?index=equities",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
        "symbol_fields": ("symbol",),
        "timestamp_fields": ("caBroadcastDate",),
        "date_fields": ("exDate", "recDate"),
        "attachment_fields": (),
    },
    {
        "name": "board_meetings",
        "url": "https://www.nseindia.com/api/corporate-board-meetings?index=equities",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-board-meetings?equitybmdatefilter=1",
        "symbol_fields": ("bm_symbol", "symbol"),
        "timestamp_fields": ("bm_timestamp", "sysTime"),
        "date_fields": ("bm_date", "oriiginalMeetingDate", "proposedMeetingDate"),
        "attachment_fields": ("attachment",),
    },
    {
        "name": "financial_results",
        "url": "https://www.nseindia.com/api/corporates-financial-results?index=equities&period=Quarterly",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-financial-results?equityfndatefilter=1",
        "symbol_fields": ("symbol",),
        "timestamp_fields": ("exchdisstime", "broadCastDate", "filingDate"),
        "date_fields": ("fromDate", "toDate"),
        "attachment_fields": ("resultDetailedDataLink", "xbrl"),
    },
    {
        "name": "shareholding_pattern",
        "url": "https://www.nseindia.com/api/corporate-share-holdings-master?index=equities",
        "referer": "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern",
        "symbol_fields": ("symbol",),
        "timestamp_fields": ("broadcastDate", "systemDate"),
        "date_fields": ("submissionDate", "date", "revisionDate", "revisedDate"),
        "attachment_fields": ("xbrl",),
    },
)

DAILY_REPORT_KEYS = ("CM", "INDEX", "SLBS", "SME", "FO", "COM", "CD", "NBF", "WDM", "CBM", "TRI-PARTY", "EGR")


@dataclass
class FilingSource:
    name: str
    url: str
    referer: str
    symbol_fields: Sequence[str]
    timestamp_fields: Sequence[str]
    date_fields: Sequence[str]
    attachment_fields: Sequence[str]


def _clean_symbol(value: Any) -> Optional[str]:
    if value is None:
        return None
    symbol = str(value).strip().upper()
    if not symbol:
        return None
    symbol = re.sub(r"[^A-Z0-9._-]+", "", symbol)
    return symbol or None


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    date_fragment = text.split(" ")[0].replace("/", "-")
    parts = date_fragment.split("-")
    if len(parts) >= 3:
        parts[1] = parts[1].title()
        date_fragment = "-".join(parts[:3])
    for fmt in ("%d-%b-%Y", "%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_fragment, fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    parts = normalized.split(" ")
    if parts:
        date_parts = parts[0].split("-")
        if len(date_parts) >= 3:
            date_parts[1] = date_parts[1].title()
            parts[0] = "-".join(date_parts[:3])
            normalized = " ".join(parts)
    for fmt in (
        "%d-%b-%Y %H:%M:%S",
        "%d-%b-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
    ):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    return None


def should_include_record(
    record: Dict[str, Any],
    source: FilingSource,
    target_date: date,
    cutoff_time: Tuple[int, int] = (15, 30),
) -> bool:
    for field in source.timestamp_fields:
        dt = parse_datetime(record.get(field))
        if dt is None:
            continue
        if dt.date() != target_date:
            continue
        if (dt.hour, dt.minute) >= cutoff_time:
            return True
        return False

    # step back over weekends (Monday → Friday)
    days_back = 3 if target_date.weekday() == 0 else 1
    fallback_date = target_date - timedelta(days=days_back)
    for field in source.date_fields:
        parsed = parse_date(record.get(field))
        if parsed == fallback_date:
            return True
    return False


def with_retry(
    operation: Callable[[], Any],
    max_attempts: int,
    base_delay_seconds: float,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    attempt = 0
    while True:
        try:
            return operation()
        except Exception:
            attempt += 1
            if attempt >= max_attempts:
                raise
            sleep_fn(base_delay_seconds * (2 ** (attempt - 1)))


def _is_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def _extension_from_url(url: str, default: str = ".bin") -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix
    if suffix:
        return suffix.lower()
    return default


def _build_filename(symbol: str, event_date: date, sequence: int, extension: str) -> str:
    base = f"{symbol}_{event_date.strftime('%Y%m%d')}"
    if sequence > 0:
        base = f"{base}_{sequence + 1}"
    return f"{base}{extension}"


def _extract_event_date(record: Dict[str, Any], source: FilingSource, target_date: date) -> date:
    for field in source.timestamp_fields:
        parsed = parse_datetime(record.get(field))
        if parsed is not None:
            return parsed.date()
    for field in source.date_fields:
        parsed_date = parse_date(record.get(field))
        if parsed_date is not None:
            return parsed_date
    return target_date


class NsePostCloseScraper:
    def __init__(self, max_attempts: int = 3, backoff_seconds: float = 1.0):
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.session = requests.Session()
        self.session.headers.update(NSE_HEADERS)
        self.failures: List[Dict[str, str]] = []
        self._download_count_per_symbol: Dict[str, int] = {}

    def _request_json(self, url: str, referer: str) -> Any:
        def _run() -> Any:
            response = self.session.get(
                url,
                headers={"Referer": referer, "Accept": "application/json,text/plain,*/*"},
                timeout=40,
            )
            response.raise_for_status()
            return response.json()

        return with_retry(_run, self.max_attempts, self.backoff_seconds)

    def _download_file(self, url: str, output_path: Path, referer: str) -> None:
        def _run() -> None:
            response = self.session.get(
                url,
                headers={"Referer": referer, "Accept": "*/*"},
                timeout=60,
            )
            response.raise_for_status()
            output_path.write_bytes(response.content)

        with_retry(_run, self.max_attempts, self.backoff_seconds)

    def warm_up(self) -> None:
        def _run() -> None:
            self.session.get("https://www.nseindia.com", timeout=20)
            response = self.session.get(
                "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                timeout=20,
            )
            response.raise_for_status()

        with_retry(_run, self.max_attempts, self.backoff_seconds)

    def scrape_post_close(
        self,
        target_date: date,
        download_dir: Path,
        download_only_pdf: bool = True,
    ) -> Dict[str, Any]:
        symbols: Set[str] = set()
        source_summaries: Dict[str, int] = {}
        download_dir.mkdir(parents=True, exist_ok=True)

        for source_cfg in CORPORATE_SOURCES:
            source = FilingSource(**source_cfg)
            rows = self._request_json(source.url, source.referer)
            if not isinstance(rows, list):
                rows = []

            included = [row for row in rows if isinstance(row, dict) and should_include_record(row, source, target_date)]
            source_summaries[source.name] = len(included)

            for row in included:
                symbol = None
                for field in source.symbol_fields:
                    symbol = _clean_symbol(row.get(field))
                    if symbol:
                        break
                if symbol:
                    symbols.add(symbol)

                event_date = _extract_event_date(row, source, target_date)
                for attachment_field in source.attachment_fields:
                    url = str(row.get(attachment_field) or "").strip()
                    if not url.startswith("http"):
                        continue
                    if download_only_pdf and not _is_pdf_url(url):
                        continue
                    download_symbol = symbol or "UNKNOWN"
                    sequence = self._download_count_per_symbol.get(download_symbol, 0)
                    extension = _extension_from_url(url, default=".pdf")
                    filename = _build_filename(download_symbol, event_date, sequence, extension)
                    output_path = download_dir / filename
                    try:
                        self._download_file(url, output_path, source.referer)
                        self._download_count_per_symbol[download_symbol] = sequence + 1
                    except Exception as exc:
                        self.failures.append(
                            {
                                "source": source.name,
                                "symbol": download_symbol,
                                "url": url,
                                "error": str(exc),
                            }
                        )

        report_download_count = self._download_daily_reports(target_date, download_dir)
        forthcoming_symbols, forthcoming_count = self._scrape_forthcoming_listings(
            target_date, download_dir, download_only_pdf
        )
        symbols |= forthcoming_symbols
        return {
            "symbols": sorted(symbols),
            "source_counts": source_summaries,
            "report_download_count": report_download_count,
            "forthcoming_count": forthcoming_count,
            "failures": self.failures,
        }

    def _scrape_forthcoming_listings(
        self,
        target_date: date,
        download_dir: Path,
        download_only_pdf: bool = True,
    ) -> Tuple[Set[str], int]:
        url = "https://www.nseindia.com/api/new-listing-today?index=ForthListing"
        referer = "https://www.nseindia.com/market-data/new-stock-exchange-listings-forthcoming"
        symbols: Set[str] = set()
        count = 0
        try:
            payload = self._request_json(url, referer)
        except Exception as exc:
            self.failures.append({"source": "forthcoming_listings", "symbol": "", "url": url, "error": str(exc)})
            return symbols, count

        rows = payload.get("data") or [] if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            effective_date = parse_date(row.get("effectiveDate"))
            if effective_date != target_date:
                continue
            symbol = _clean_symbol(row.get("symbol"))
            if symbol:
                symbols.add(symbol)
            attachment_url = str(row.get("shdAttachment") or "").strip()
            if not attachment_url.startswith("http"):
                continue
            if download_only_pdf and not _is_pdf_url(attachment_url):
                continue
            dl_sym = symbol or "UNKNOWN"
            seq = self._download_count_per_symbol.get(dl_sym, 0)
            ext = _extension_from_url(attachment_url, default=".pdf")
            filename = _build_filename(dl_sym, effective_date or target_date, seq, ext)
            try:
                self._download_file(attachment_url, download_dir / filename, referer)
                self._download_count_per_symbol[dl_sym] = seq + 1
                count += 1
            except Exception as exc:
                self.failures.append({"source": "forthcoming_listings", "symbol": dl_sym, "url": attachment_url, "error": str(exc)})
        return symbols, count

    def _download_daily_reports(self, target_date: date, download_dir: Path) -> int:
        target_label = target_date.strftime("%d-%b-%Y")
        count = 0
        referer = "https://www.nseindia.com/all-reports"

        for key in DAILY_REPORT_KEYS:
            url = f"https://www.nseindia.com/api/daily-reports?key={key}"
            try:
                payload = self._request_json(url, referer)
            except Exception as exc:
                self.failures.append(
                    {
                        "source": "daily_reports",
                        "symbol": key,
                        "url": url,
                        "error": str(exc),
                    }
                )
                continue

            if not isinstance(payload, dict):
                continue
            entries = payload.get("CurrentDay", [])
            if not isinstance(entries, list):
                continue

            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                trading_date = str(entry.get("tradingDate") or "").strip()
                if trading_date and trading_date != target_label:
                    continue

                file_path = str(entry.get("filePath") or "").strip()
                file_name = str(entry.get("fileActlName") or "").strip()
                if not file_path.startswith("http"):
                    continue
                file_url = file_path
                if file_name:
                    file_url = file_path.rstrip("/") + "/" + file_name

                display = str(entry.get("displayName") or entry.get("fileActlName") or key).strip()
                safe_display = re.sub(r"[^A-Za-z0-9._-]+", "_", display).strip("_") or key
                ext = _extension_from_url(file_name or file_url, default=".pdf")
                filename = f"REPORT_{key}_{target_date.strftime('%Y%m%d')}_{safe_display}{ext}"
                output_path = download_dir / filename
                try:
                    self._download_file(file_url, output_path, referer)
                    count += 1
                except Exception as exc:
                    self.failures.append(
                        {
                            "source": "daily_reports",
                            "symbol": key,
                            "url": file_url,
                            "error": str(exc),
                        }
                    )
        return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape NSE corporate filings after market close and download documents/reports."
    )
    parser.add_argument(
        "--target-date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="Target market date in YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--download-dir",
        default=str(DEFAULT_DOWNLOAD_DIR),
        help="Directory for downloaded attachments and reports.",
    )
    parser.add_argument(
        "--failure-log",
        default=str(DEFAULT_FAILURE_LOG),
        help="Path to write per-stock failure log JSON.",
    )
    parser.add_argument(
        "--symbols-output",
        default=str(DEFAULT_SYMBOLS_OUTPUT),
        help="Path to write ticker symbols list (one symbol per line).",
    )
    parser.add_argument(
        "--summary-output",
        default=str(DEFAULT_SUMMARY_OUTPUT),
        help="Path to write run summary JSON.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Max request attempts for retries.",
    )
    parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=1.0,
        help="Base delay for exponential backoff.",
    )
    parser.add_argument(
        "--download-non-pdf",
        action="store_true",
        help="Also download non-PDF attachments (default is PDF-only for corporate updates).",
    )
    return parser


def clean_old_downloads(download_dir: Path, target_date: date) -> int:
    """Delete equity PDFs, all REPORT_CD_* files, and REPORT_CM_* files not matching target_date."""
    if not download_dir.exists():
        return 0

    keep_date_str = target_date.strftime("%Y%m%d")
    deleted_count = 0

    for f in download_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name

        if name.startswith("REPORT_CD_"):
            # CD segment (currency derivatives) has no consumer — always delete
            should_delete = True
        elif name.startswith("REPORT_"):
            # Keep only the CM (and other) report files matching today's target date
            should_delete = keep_date_str not in name
        else:
            # Equity PDFs from corporate announcements
            should_delete = name.endswith(".pdf")

        if should_delete:
            try:
                f.unlink()
                print(f"[nse_postclose_scraper] Cleaned: {name}")
                deleted_count += 1
            except Exception as e:
                print(f"[nse_postclose_scraper] Error cleaning {name}: {e}")

    return deleted_count


def run_news_analyzer() -> int:
    """Execute nse_news_analyzer.py to extract PDFs for analysis."""
    print("\n[nse_postclose_scraper] Starting PDF extraction for analysis...")
    try:
        result = subprocess.run(
            [sys.executable, "nse_news_analyzer.py"],
            cwd=Path(__file__).resolve().parent,
            capture_output=False,
            timeout=60
        )
        if result.returncode == 0:
            print("[nse_postclose_scraper] PDF extraction completed\n")
            return 0
        else:
            print(f"[nse_postclose_scraper] PDF extraction failed (exit code {result.returncode})\n")
            return 1
    except subprocess.TimeoutExpired:
        print("[nse_postclose_scraper] PDF extraction timeout\n")
        return 1
    except Exception as e:
        print(f"[nse_postclose_scraper] PDF extraction error: {e}\n")
        return 1


def main() -> int:
    args = build_parser().parse_args()

    try:
        target_date = datetime.strptime(args.target_date, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit("Invalid --target-date. Use YYYY-MM-DD.")

    download_dir = Path(args.download_dir)

    # Step 1: Clean old downloads
    print("[nse_postclose_scraper] Cleaning old downloads...")
    cleaned = clean_old_downloads(download_dir, target_date)
    print(f"[nse_postclose_scraper] Removed {cleaned} stale files\n")
    failure_log = Path(args.failure_log)
    symbols_output = Path(args.symbols_output)
    summary_output = Path(args.summary_output)

    scraper = NsePostCloseScraper(max_attempts=args.max_attempts, backoff_seconds=args.backoff_seconds)
    scraper.warm_up()
    result = scraper.scrape_post_close(
        target_date=target_date,
        download_dir=download_dir,
        download_only_pdf=not args.download_non_pdf,
    )

    symbols_output.parent.mkdir(parents=True, exist_ok=True)
    symbols_output.write_text("\n".join(result["symbols"]) + ("\n" if result["symbols"] else ""), encoding="utf-8")

    failure_log.parent.mkdir(parents=True, exist_ok=True)
    failure_log.write_text(json.dumps(result["failures"], indent=2), encoding="utf-8")

    summary_payload = {
        "generated_at": datetime.now().isoformat(),
        "target_date": target_date.isoformat(),
        "symbol_count": len(result["symbols"]),
        "symbols": result["symbols"],
        "source_counts": result["source_counts"],
        "report_download_count": result["report_download_count"],
        "forthcoming_count": result["forthcoming_count"],
        "failure_count": len(result["failures"]),
    }
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")

    print(f"[nse_postclose_scraper] Symbols: {len(result['symbols'])}")
    print(f"[nse_postclose_scraper] Forthcoming listings on {target_date}: {result['forthcoming_count']}")
    print(f"[nse_postclose_scraper] Reports downloaded: {result['report_download_count']}")
    print(f"[nse_postclose_scraper] Failures logged: {len(result['failures'])}")
    print(f"[nse_postclose_scraper] Symbols file: {symbols_output}")
    
    # Step 2: Extract PDFs for analysis
    extract_result = run_news_analyzer()
    
    return extract_result


if __name__ == "__main__":
    raise SystemExit(main())
