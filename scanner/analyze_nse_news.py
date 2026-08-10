#!/usr/bin/env python3
"""
NSE news analyzer for intraday equity rankings.
Processes nse_extracts_*.json files to extract signals and rank equities.
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional

sys.path.insert(0, '.')


def find_latest_extract_file(data_dir: str) -> Optional[str]:
    """Find the most recent nse_extracts_*.json file."""
    data_path = Path(data_dir)
    extract_files = sorted(data_path.glob("nse_extracts_*.json"))
    if extract_files:
        return str(extract_files[-1])
    return None


def load_extract_file(filepath: str) -> dict:
    """Load the NSE extracts JSON file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_date_from_filename(filename: str) -> str:
    """Extract date from nse_extracts_YYYY-MM-DD.json."""
    match = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    return match.group(1) if match else datetime.now().strftime('%Y-%m-%d')


def analyze_earnings_disclosure(text: str) -> Dict:
    """Extract earnings-related signals from disclosure text."""
    signals = {
        'has_earnings': False,
        'revenue_growth': None,
        'profit_growth': None,
        'margin_trend': None,
        'guidance': 'neutral',
        'events': []
    }
    
    text_lower = text.lower()
    
    # Detect earnings results
    if any(word in text_lower for word in ['financial results', 'earnings', 'q1', 'q2', 'q3', 'q4', 'fy']):
        signals['has_earnings'] = True
        
        # Look for YoY growth indicators
        if any(word in text_lower for word in ['increase', '+', 'growth', 'up', 'higher', 'surge', 'spike']):
            # Check if it's revenue or profit
            if 'revenue' in text_lower or 'income' in text_lower:
                signals['revenue_growth'] = 'positive'
            if 'profit' in text_lower or 'pat' in text_lower or 'pbt' in text_lower or 'ebitda' in text_lower:
                signals['profit_growth'] = 'positive'
        
        if any(word in text_lower for word in ['decrease', '-', 'decline', 'down', 'lower']):
            if 'revenue' in text_lower or 'income' in text_lower:
                signals['revenue_growth'] = 'negative'
            if 'profit' in text_lower or 'pat' in text_lower or 'pbt' in text_lower or 'ebitda' in text_lower:
                signals['profit_growth'] = 'negative'
        
        # Margin analysis
        if 'margin' in text_lower:
            if any(word in text_lower for word in ['expansion', 'improved', 'increase']):
                signals['margin_trend'] = 'expansion'
            elif any(word in text_lower for word in ['compression', 'decline', 'decrease']):
                signals['margin_trend'] = 'compression'
    
    # Guidance tone
    if any(word in text_lower for word in ['strong', 'positive', 'growth', 'momentum']):
        signals['guidance'] = 'positive'
    elif any(word in text_lower for word in ['challenging', 'headwind', 'pressure', 'risk']):
        signals['guidance'] = 'negative'
    
    return signals


def analyze_corporate_actions(text: str) -> Dict:
    """Extract corporate action signals."""
    signals = {
        'buyback': False,
        'dividend': False,
        'bonus': False,
        'split': False,
        'demerger': False,
        'merger': False
    }
    
    text_lower = text.lower()
    
    if 'buyback' in text_lower or 'share buyback' in text_lower:
        signals['buyback'] = True
    if 'dividend' in text_lower:
        signals['dividend'] = True
    if 'bonus' in text_lower:
        signals['bonus'] = True
    if 'split' in text_lower:
        signals['split'] = True
    if 'demerger' in text_lower:
        signals['demerger'] = True
    if 'merger' in text_lower:
        signals['merger'] = True
    
    return signals


def analyze_governance_signals(text: str) -> Dict:
    """Extract governance and regulatory signals."""
    signals = {
        'leadership_change': False,
        'regulatory_issue': False,
        'audit_resignation': False,
        'penalty': False,
        'litigation': False,
        'insider_activity': False
    }
    
    text_lower = text.lower()
    
    if any(word in text_lower for word in ['resignation', 'appointment', 'director', 'md', 'ceo', 'leadership']):
        signals['leadership_change'] = True
    
    if any(word in text_lower for word in ['sebi', 'nse', 'bse', 'rbi', 'notice', 'compliance', 'violation', 
                                            'regulatory', 'pollution', 'environment', 'labor']):
        signals['regulatory_issue'] = True
    
    if 'auditor' in text_lower and any(word in text_lower for word in ['resign', 'resignation']):
        signals['audit_resignation'] = True
    
    if any(word in text_lower for word in ['penalty', 'fine', 'fine', 'warning']):
        signals['penalty'] = True
    
    if any(word in text_lower for word in ['litigation', 'lawsuit', 'legal', 'dispute']):
        signals['litigation'] = True
    
    if any(word in text_lower for word in ['insider', 'bulk deal', 'block deal', 'promoter']):
        signals['insider_activity'] = True
    
    return signals


def calculate_sentiment_and_confidence(stock_name: str, text: str, daily_reports: dict) -> Tuple[str, int, str, float]:
    """
    Analyze text and daily reports to generate sentiment, score, and confidence.
    Returns: (sentiment_label, sentiment_score, confidence_label, confidence_score)
    """
    earnings = analyze_earnings_disclosure(text)
    corporate = analyze_corporate_actions(text)
    governance = analyze_governance_signals(text)
    
    sentiment_score = 0
    confidence_score = 0.5
    reasons = []
    
    # Earnings analysis (strong positive signals)
    if earnings['has_earnings']:
        confidence_score += 0.15
        if earnings['revenue_growth'] == 'positive':
            sentiment_score += 25
            reasons.append("Revenue growth")
        elif earnings['revenue_growth'] == 'negative':
            sentiment_score -= 20
            reasons.append("Revenue decline")
        
        if earnings['profit_growth'] == 'positive':
            sentiment_score += 30
            reasons.append("Profit growth")
        elif earnings['profit_growth'] == 'negative':
            sentiment_score -= 25
            reasons.append("Profit decline")
        
        if earnings['margin_trend'] == 'expansion':
            sentiment_score += 15
            reasons.append("Margin expansion")
        elif earnings['margin_trend'] == 'compression':
            sentiment_score -= 15
            reasons.append("Margin compression")
    
    # Corporate actions (moderate positive signals)
    if corporate['buyback']:
        sentiment_score += 15
        reasons.append("Buyback announced")
    if corporate['dividend']:
        sentiment_score += 10
        reasons.append("Dividend positive")
    if corporate['bonus']:
        sentiment_score += 10
        reasons.append("Bonus issue")
    
    # Governance signals (negative signals)
    if governance['audit_resignation']:
        sentiment_score -= 30
        confidence_score += 0.15
        reasons.append("Auditor resignation")
    if governance['regulatory_issue']:
        sentiment_score -= 15
        reasons.append("Regulatory concerns")
    if governance['penalty']:
        sentiment_score -= 20
        reasons.append("Penalty/violation")
    if governance['litigation']:
        sentiment_score -= 10
        reasons.append("Litigation risks")
    
    # Leadership changes (context-dependent)
    if governance['leadership_change']:
        confidence_score -= 0.1
        reasons.append("Leadership change")
    
    # Apply daily reports adjustments
    daily = daily_reports or {}
    price_bands = daily.get('price_band_changes', [])
    series_changes = daily.get('series_changes', [])
    
    # Price band changes
    for pb in price_bands:
        if pb.get('symbol') == stock_name:
            if pb.get('direction') == 'expanded':
                sentiment_score += 10
                confidence_score += 0.1
                reasons.append("Price band expanded - pent-up demand")
            elif pb.get('direction') == 'contracted':
                sentiment_score -= 15
                confidence_score += 0.1
                reasons.append("Price band contracted - caution")
    
    # Series changes
    for sc in series_changes:
        if sc.get('symbol') == stock_name:
            if sc.get('to_series') == 'BE':
                sentiment_score -= 25
                confidence_score += 0.15
                reasons.append("Moved to BE (trade-to-trade) - bearish")
            elif sc.get('to_series') == 'EQ' and sc.get('from_series') == 'BE':
                sentiment_score += 10
                reasons.append("Graduated from BE to EQ")
    
    # Determine confidence label
    if confidence_score >= 0.75:
        confidence_label = "High"
    elif confidence_score >= 0.50:
        confidence_label = "Medium"
    else:
        confidence_label = "Low"
    
    # Determine sentiment label and clamp score
    sentiment_score = max(-100, min(100, sentiment_score))
    
    if sentiment_score >= 30:
        sentiment_label = "Positive"
    elif sentiment_score <= -30:
        sentiment_label = "Negative"
    else:
        sentiment_label = "Neutral"
    
    reason = " | ".join(reasons) if reasons else "Insufficient signals"
    
    return sentiment_label, sentiment_score, confidence_label, confidence_score, reason


def rank_equities(extracts: dict, daily_reports: dict) -> List[Dict]:
    """Rank all equities based on extracted signals."""
    rankings = []
    
    for symbol, data in extracts.items():
        if symbol == '_daily_reports':
            continue
        
        text = data.get('text', '')
        is_tradable = data.get('is_tradable_intraday', False)
        
        sentiment_label, sentiment_score, confidence_label, confidence_score, reason = \
            calculate_sentiment_and_confidence(symbol, text, daily_reports)
        
        # Rank score: higher sentiment + higher confidence = higher rank
        rank_score = (sentiment_score + 100) / 2 * (confidence_score * 100) / 100
        
        rankings.append({
            'symbol': symbol,
            'rank_score': rank_score,
            'reason': reason,
            'sentiment_label': sentiment_label,
            'sentiment_score': sentiment_score,
            'confidence_label': confidence_label,
            'confidence_score': round(confidence_score, 2),
            'is_tradable_intraday': is_tradable
        })
    
    # Sort by rank score (descending)
    rankings.sort(key=lambda x: x['rank_score'], reverse=True)
    
    # Assign ranks
    for idx, ranking in enumerate(rankings, 1):
        ranking['rank'] = idx
    
    return rankings


def output_csv(rankings: List[Dict], output_path: str):
    """Output rankings to CSV file."""
    import csv
    
    intraday = [r for r in rankings if r['is_tradable_intraday']]
    non_intraday = [r for r in rankings if not r['is_tradable_intraday']]
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        
        # Intraday section header and data
        writer.writerow(['Symbol', 'Rank', 'Reason', 'SentimentLabel', 'SentimentScore', 'ConfidenceLabel', 'ConfidenceScore'])
        for r in intraday:
            writer.writerow([
                r['symbol'],
                r['rank'],
                r['reason'],
                r['sentiment_label'],
                r['sentiment_score'],
                r['confidence_label'],
                r['confidence_score']
            ])
        
        # Separator and non-intraday section
        if non_intraday:
            writer.writerow([])  # Blank row
            writer.writerow(['Section', 'SHORT_TERM_INVESTMENT', '', '', '', '', ''])
            writer.writerow(['Symbol', 'Rank', 'Reason', 'SentimentLabel', 'SentimentScore', 'ConfidenceLabel', 'ConfidenceScore'])
            for r in non_intraday:
                writer.writerow([
                    r['symbol'],
                    r['rank'],
                    r['reason'],
                    r['sentiment_label'],
                    r['sentiment_score'],
                    r['confidence_label'],
                    r['confidence_score']
                ])


def print_summary(rankings: List[Dict]):
    """Print summary to console."""
    intraday = [r for r in rankings if r['is_tradable_intraday']]
    non_intraday = [r for r in rankings if not r['is_tradable_intraday']]
    
    print("\n" + "="*80)
    print("NSE EQUITY RANKINGS - INTRADAY CANDIDATES")
    print("="*80)
    
    if intraday:
        print(f"\n{len(intraday)} INTRADAY TRADABLE STOCKS:\n")
        for r in intraday[:10]:  # Top 10
            print(f"#{r['rank']:2d}  {r['symbol']:12s} | Sentiment: {r['sentiment_label']:8s} ({r['sentiment_score']:+4d}) | "
                  f"Confidence: {r['confidence_label']:6s} ({r['confidence_score']:.2f})")
            print(f"       {r['reason'][:70]}")
    else:
        print("\nNo high-conviction BUY candidates from current NSE equity disclosures.")
    
    if non_intraday:
        print(f"\n{len(non_intraday)} SHORT-TERM INVESTMENT CANDIDATES:\n")
        for r in non_intraday[:5]:  # Top 5
            print(f"#{r['rank']:2d}  {r['symbol']:12s} | Sentiment: {r['sentiment_label']:8s} ({r['sentiment_score']:+4d}) | "
                  f"Confidence: {r['confidence_label']:6s} ({r['confidence_score']:.2f})")
            print(f"       {r['reason'][:70]}")
    
    print("\n" + "="*80)


def main():
    # Look for data directory in repo root or parent
    repo_root = Path(__file__).parent.parent
    data_dir = str(repo_root / 'data')
    
    # Find latest extract file
    extract_file = find_latest_extract_file(data_dir)
    if not extract_file:
        print("No nse_extracts_*.json file found in data/ directory")
        return
    
    print(f"Processing: {Path(extract_file).name}")
    
    # Load extract data
    extracts = load_extract_file(extract_file)
    daily_reports = extracts.get('_daily_reports', {})
    
    # Rank equities
    rankings = rank_equities(extracts, daily_reports)
    
    # Generate output CSV path
    extract_date = extract_date_from_filename(extract_file)
    output_csv_path = Path(data_dir) / f'nse_rankings_{extract_date}.csv'
    
    # Output CSV
    output_csv(rankings, str(output_csv_path))
    print(f"✓ Rankings saved to: {output_csv_path}")
    
    # Print summary
    print_summary(rankings)


if __name__ == '__main__':
    main()
