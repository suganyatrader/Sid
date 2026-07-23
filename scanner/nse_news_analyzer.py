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


def build_analysis_data(extracts: dict) -> str:
    """Format extracted PDFs for Copilot analysis."""
    
    output = "NSE POST-CLOSE EQUITY ANNOUNCEMENTS - ANALYSIS DATA\n"
    output += f"Generated: {datetime.now().isoformat()}\n"
    output += f"Total Documents: {len(extracts)}\n"
    
    tradable_count = sum(1 for data in extracts.values() if data.get('is_tradable_intraday', False))
    output += f"Tradable Intraday: {tradable_count}/{len(extracts)}\n"
    output += "=" * 80 + "\n\n"
    
    for symbol, data in extracts.items():
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
    
    download_dir = "../data/nse_postclose_downloads"
    
    print("=" * 80)
    print("NSE Equity Extractor - Preparing Data for Copilot Chat Analysis")
    print("=" * 80)
    
    # Extract PDFs
    print("\n[1/2] Extracting PDFs from announcements...")
    extracts = extract_pdf_texts(download_dir)
    
    if not extracts:
        print("✗ No PDFs found in", download_dir)
        return
    
    print(f"✓ Extracted {len(extracts)} documents")
    
    # Save extracted data to JSON for easy import
    print("\n[2/2] Preparing analysis data...")
    
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Save as JSON
    json_file = Path("../data") / f"nse_extracts_{today}.json"
    with open(json_file, 'w') as f:
        json.dump(extracts, f, indent=2)
    print(f"✓ Extracts saved to: {json_file}")
    
    # Also save as text for easy copy-paste to chat
    text_file = Path("../data") / f"nse_extracts_{today}.txt"
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
