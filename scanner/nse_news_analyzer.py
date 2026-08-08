#!/usr/bin/env python3
"""
NSE equity PDF extractor.
Extracts text from NSE post-close PDFs and prepares for Copilot analysis.
"""

import sys
import os
import csv
import json
import re
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '.')

try:
    import pdfplumber
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pdfplumber', '-q'])
    import pdfplumber


def load_tradable_symbols(stock_identifiers_path: str) -> set:
    """Load set of tradable intraday symbols from stock_identifiers.json."""
    try:
        with open(stock_identifiers_path, 'r') as f:
            identifiers = json.load(f)
        
        tradable = {item['symbol'] for item in identifiers if isinstance(item, dict) and 'symbol' in item}
        return tradable
    except Exception as e:
        print(f"⚠ Warning: Could not load stock identifiers: {e}")
        return set()


def extract_pdf_texts(download_dir: str, tradable_symbols: set) -> dict:
    """Extract text from all PDFs in directory, keyed by symbol."""
    pdf_dir = Path(download_dir)
    pdf_files = list(pdf_dir.glob("*.pdf"))
    
    extracted = {}
    
    for pdf_file in pdf_files:
        try:
            with pdfplumber.open(str(pdf_file)) as pdf:
                text = ''
                # Extract first 10 pages
                for page in pdf.pages[:10]:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
                
                # Try to infer stock symbol from filename
                # Format: SYMBOL_YYYYMMDD.pdf or similar
                filename = pdf_file.stem
                parts = filename.split('_')
                symbol = parts[0] if parts else 'UNKNOWN'
                
                if text.strip():
                    extracted[symbol] = {
                        'filename': pdf_file.name,
                        'text': text[:5000],  # Limit to first 5000 chars per file
                        'is_tradable_intraday': symbol in tradable_symbols
                    }
                    status = "✓" if symbol in tradable_symbols else "⚠"
                    print(f"{status} Extracted: {symbol} from {pdf_file.name} (tradable: {symbol in tradable_symbols})")
        except Exception as e:
            print(f"✗ Error reading {pdf_file.name}: {e}")
    
    return extracted


def extract_report_csvs(download_dir: Path, date_str: str) -> dict:
    """Parse CM daily report CSVs; returns signals relevant for equity analysis."""
    reports = {"price_band_changes": [], "series_changes": [], "52_week_highs": []}

    pb_file = download_dir / f"REPORT_CM_{date_str}_Price_Band_changes_from_next_trade_date_csv.csv"
    if pb_file.exists():
        try:
            with open(pb_file, newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    symbol = (row.get("Symbol") or "").strip()
                    if (row.get("Series") or "").strip() != "EQ" or not symbol:
                        continue
                    try:
                        from_band, to_band = int(row.get("From", 0)), int(row.get("To", 0))
                    except (ValueError, TypeError):
                        continue
                    reports["price_band_changes"].append({
                        "symbol": symbol,
                        "from_band": from_band,
                        "to_band": to_band,
                        "direction": "expanded" if to_band > from_band else "contracted",
                    })
            print(f"✓ Price band changes: {len(reports['price_band_changes'])} EQ symbols")
        except Exception as e:
            print(f"⚠ Price band CSV error: {e}")

    sc_file = download_dir / f"REPORT_CM_{date_str}_Latest_change_csv.csv"
    if sc_file.exists():
        try:
            with open(sc_file, newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    symbol = (row.get("Symbol") or "").strip()
                    if not symbol:
                        continue
                    to_series = (row.get("To Series") or "").strip()
                    reports["series_changes"].append({
                        "symbol": symbol,
                        "from_series": (row.get("From Series") or "").strip(),
                        "to_series": to_series,
                        # BE = trade-to-trade, exchange-imposed circuit; bearish signal
                        "signal": "bearish" if to_series == "BE" else "neutral",
                    })
            print(f"✓ Series changes: {len(reports['series_changes'])} symbols")
        except Exception as e:
            print(f"⚠ Series change CSV error: {e}")

    hl_file = download_dir / f"REPORT_CM_{date_str}_52_Week_High_Low_Report.csv"
    if hl_file.exists():
        try:
            with open(hl_file, newline='', encoding='utf-8-sig') as f:
                lines = f.readlines()
            # First 2 lines are disclaimer/effective-date metadata, not data
            for row in csv.DictReader(lines[2:]):
                symbol = (row.get("SYMBOL") or "").strip()
                high = (row.get("Adjusted_52_Week_High") or "").strip()
                if (row.get("SERIES") or "").strip() != "EQ" or not symbol or high == "-":
                    continue
                reports["52_week_highs"].append({
                    "symbol": symbol,
                    "high": high,
                    "high_date": (row.get("52_Week_High_Date") or "").strip(),
                    "low": (row.get("Adjusted_52_Week_Low") or "").strip(),
                    "low_date": (row.get("52_Week_Low_DT") or "").strip(),
                })
            print(f"✓ 52-week H/L: {len(reports['52_week_highs'])} EQ symbols with data")
        except Exception as e:
            print(f"⚠ 52-week H/L CSV error: {e}")

    return reports


def build_analysis_data(extracts: dict) -> str:
    """Format extracted PDFs and daily reports for Copilot analysis."""

    equity_entries = {k: v for k, v in extracts.items() if not k.startswith('_')}
    output = "NSE POST-CLOSE EQUITY ANNOUNCEMENTS - ANALYSIS DATA\n"
    output += f"Generated: {datetime.now().isoformat()}\n"
    output += f"Total Documents: {len(equity_entries)}\n"

    tradable_count = sum(1 for data in equity_entries.values() if data.get('is_tradable_intraday', False))
    output += f"Tradable Intraday: {tradable_count}/{len(equity_entries)}\n"
    output += "=" * 80 + "\n\n"

    if "_daily_reports" in extracts:
        reports = extracts["_daily_reports"]
        output += "DAILY MARKET REPORTS\n"
        output += "=" * 80 + "\n\n"

        pb = reports.get("price_band_changes", [])
        if pb:
            expanded = [r["symbol"] for r in pb if r["direction"] == "expanded"]
            contracted = [r["symbol"] for r in pb if r["direction"] == "contracted"]
            output += f"PRICE BAND CHANGES ({len(pb)} EQ symbols):\n"
            if expanded:
                output += f"  Expanded (bullish — exchange easing circuit): {', '.join(expanded)}\n"
            if contracted:
                output += f"  Contracted (bearish — exchange tightening circuit): {', '.join(contracted)}\n"
            output += "\n"

        sc = reports.get("series_changes", [])
        if sc:
            output += f"SERIES CHANGES ({len(sc)} symbols):\n"
            for r in sc:
                tag = " [BEARISH - moved to T2T/buyer-beware]" if r["signal"] == "bearish" else ""
                output += f"  {r['symbol']}: {r['from_series']} → {r['to_series']}{tag}\n"
            output += "\n"

        highs = reports.get("52_week_highs", [])
        if highs:
            output += f"52-WEEK HIGH/LOW REFERENCE ({len(highs)} EQ symbols with data):\n"
            for r in highs[:50]:  # cap to keep context size reasonable
                output += f"  {r['symbol']}: 52W High={r['high']} ({r['high_date']}), Low={r['low']} ({r['low_date']})\n"
            if len(highs) > 50:
                output += f"  ... and {len(highs) - 50} more\n"
            output += "\n"

        output += "=" * 80 + "\n\n"

    for symbol, data in equity_entries.items():
        output += f"SYMBOL: {symbol}\n"
        output += f"FILE: {data['filename']}\n"
        tradable_label = "✓ TRADABLE" if data.get('is_tradable_intraday', False) else "✗ NOT TRADABLE"
        output += f"INTRADAY TRADABLE: {tradable_label}\n"
        output += "-" * 80 + "\n"
        output += data['text'] + "\n"
        output += "=" * 80 + "\n\n"

    return output


def analyze_with_copilot():
    """Extract PDFs and prepare for Copilot analysis."""

    script_dir = Path(__file__).resolve().parent
    data_dir = script_dir.parent / "data"
    download_dir = data_dir / "nse_postclose_downloads"

    stock_identifier_candidates = [
        script_dir / "stock_identifiers.json",
        data_dir / "stock_identifiers.json",
    ]
    stock_identifiers_path = next((p for p in stock_identifier_candidates if p.exists()), stock_identifier_candidates[0])
    tradable_symbols = load_tradable_symbols(str(stock_identifiers_path))
    
    print("=" * 80)
    print("NSE Equity Extractor - Preparing Data for Copilot Chat Analysis")
    print("=" * 80)
    print(f"Loaded tradable symbols: {len(tradable_symbols)}")
    
    # Extract PDFs
    print("\n[1/3] Extracting PDFs from announcements...")
    extracts = extract_pdf_texts(str(download_dir), tradable_symbols)
    print(f"✓ Extracted {len(extracts)} PDF documents")

    # Extract daily report CSVs (price bands, series changes, 52W H/L)
    today_str = datetime.now().strftime("%Y%m%d")
    print(f"\n[2/3] Extracting daily report CSVs for {today_str}...")
    daily_reports = extract_report_csvs(download_dir, today_str)
    has_reports = any(daily_reports.values())
    if has_reports:
        extracts["_daily_reports"] = daily_reports
    else:
        print(f"⚠ No CM daily report CSVs found for {today_str}")

    if not extracts:
        print("✗ No data extracted (no PDFs and no daily reports found)")
        return

    # Save extracted data to JSON for easy import
    print("\n[3/3] Preparing analysis data...")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Save as JSON
    json_file = data_dir / f"nse_extracts_{today}.json"
    with open(json_file, 'w') as f:
        json.dump(extracts, f, indent=2)
    print(f"✓ Extracts saved to: {json_file}")
    
    # Also save as text for easy copy-paste to chat
    text_file = data_dir / f"nse_extracts_{today}.txt"
    analysis_data = build_analysis_data(extracts)
    with open(text_file, 'w', encoding='utf-8') as f:
        f.write(analysis_data)
    print(f"✓ Text format saved to: {text_file}")
    
    print(f"\n✓ Ready for analysis!\n")
    print("Next step:")
    print(f"  1. Share {text_file} or {json_file} with Copilot chat")
    print("  2. Ask Copilot to analyze and rank these equities for BUY signals")
    print("  3. Output will be CSV to data/news_rankings_<date>.csv")
    
    # Display summary
    print("\nDocuments extracted:")
    for symbol in sorted(extracts.keys()):
        print(f"  • {symbol}")


def save_csv_rankings(results: list):
    """Save ranked results to CSV."""
    
    today = datetime.now().strftime("%Y-%m-%d")
    output_file = Path("../data") / f"news_rankings_{today}.csv"
    
    fieldnames = ["Symbol", "Rank", "Reason", "SentimentLabel", "SentimentScore", 
                  "ConfidenceLabel", "ConfidenceScore"]
    
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\n✓ Rankings saved to: {output_file}")


if __name__ == "__main__":
    analyze_with_copilot()
