#!/usr/bin/env python3
"""
NSE Extract Analyzer - Processes NSE PDFs and daily reports to rank equities for intraday trading.
"""

import json
import csv
import sys
import re
from pathlib import Path
from datetime import datetime
from collections import defaultdict

def extract_signals_from_text(symbol, text):
    """Extract market-moving signals from corporate disclosure text."""
    signals = {
        'earnings': {'direction': None, 'growth': None, 'margins': None, 'guidance': None},
        'corporate_actions': [],
        'regulatory': [],
        'market_activity': [],
    }
    
    # Earnings signals
    if re.search(r'profit|earnings|net income', text, re.I):
        if re.search(r'loss|declined|down|negative', text, re.I):
            signals['earnings']['direction'] = 'negative'
        elif re.search(r'growth|increase|surge|strong|higher', text, re.I):
            signals['earnings']['direction'] = 'positive'
    
    if re.search(r'revenue|sales', text, re.I):
        if re.search(r'growth|increase|surge', text, re.I):
            signals['earnings']['growth'] = 'positive'
        elif re.search(r'decline|down|weak', text, re.I):
            signals['earnings']['growth'] = 'negative'
    
    if re.search(r'margin|ebitda', text, re.I):
        if re.search(r'expansion|improve|better', text, re.I):
            signals['earnings']['margins'] = 'expanding'
        elif re.search(r'compress|decline|worse', text, re.I):
            signals['earnings']['margins'] = 'contracting'
    
    if re.search(r'guidance|outlook|expect', text, re.I):
        if re.search(r'optimistic|bullish|positive|strong', text, re.I):
            signals['earnings']['guidance'] = 'constructive'
        elif re.search(r'cautious|challenge|headwind', text, re.I):
            signals['earnings']['guidance'] = 'cautious'
    
    # Corporate actions
    if re.search(r'dividend.*rs\.?\s*(\d+)', text, re.I):
        match = re.search(r'dividend.*rs\.?\s*(\d+)', text, re.I)
        signals['corporate_actions'].append(f'Dividend: Rs. {match.group(1)}')
    
    if re.search(r'bonus|buyback|split|merger|demerger', text, re.I):
        actions = re.findall(r'(bonus|buyback|split|merger|demerger)', text, re.I)
        signals['corporate_actions'].extend([a.lower() for a in actions])
    
    # Regulatory and major announcements
    if re.search(r'resignation|penalty|order|regulatory|compliance', text, re.I):
        if re.search(r'resignation|leadership.*change', text, re.I):
            signals['regulatory'].append('Leadership change')
        if re.search(r'penalty|fine|violation|suspension', text, re.I):
            signals['regulatory'].append('Regulatory penalty/action')
    
    if re.search(r'agm|earnings call', text, re.I):
        signals['regulatory'].append('AGM/Earnings announcement')
    
    # Market activity
    if re.search(r'bulk.*deal|block.*deal|insider', text, re.I):
        if re.search(r'insider.*buy|promoter.*buy', text, re.I):
            signals['market_activity'].append('Insider buying')
        elif re.search(r'insider.*sell|promoter.*sell', text, re.I):
            signals['market_activity'].append('Insider selling')
    
    return signals

def calculate_sentiment_score(signals, daily_adjustments, symbol, w52_high_data):
    """Calculate sentiment score based on extracted signals and daily reports."""
    score = 0
    confidence = 0.5
    details = []
    
    # Earnings signal weight: ±15 points
    if signals['earnings']['direction'] == 'positive':
        score += 15
        details.append('Positive earnings')
        confidence += 0.1
    elif signals['earnings']['direction'] == 'negative':
        score -= 20
        details.append('Negative earnings')
        confidence -= 0.1
    
    # Growth signal weight: ±10 points
    if signals['earnings']['growth'] == 'positive':
        score += 10
        details.append('Revenue growth')
    elif signals['earnings']['growth'] == 'negative':
        score -= 12
        details.append('Revenue decline')
    
    # Margins signal weight: ±8 points
    if signals['earnings']['margins'] == 'expanding':
        score += 8
        details.append('Margin expansion')
    elif signals['earnings']['margins'] == 'contracting':
        score -= 10
        details.append('Margin contraction')
    
    # Guidance signal weight: ±10 points
    if signals['earnings']['guidance'] == 'constructive':
        score += 10
        details.append('Constructive guidance')
    elif signals['earnings']['guidance'] == 'cautious':
        score -= 8
        details.append('Cautious guidance')
    
    # Corporate actions: +5 to +10 points
    if 'dividend' in signals['corporate_actions']:
        score += 5
        details.append('Dividend declared')
    if 'bonus' in signals['corporate_actions']:
        score += 7
        details.append('Bonus issue')
    if 'buyback' in signals['corporate_actions']:
        score += 8
        details.append('Share buyback')
    
    # Regulatory/leadership: -10 to -15 points
    if 'Leadership change' in signals['regulatory']:
        score -= 10
        details.append('Leadership change')
    if 'Regulatory penalty/action' in signals['regulatory']:
        score -= 15
        details.append('Regulatory action')
    
    # Insider activity: ±8 points
    if 'Insider buying' in signals['market_activity']:
        score += 8
        details.append('Insider buying signal')
    if 'Insider selling' in signals['market_activity']:
        score -= 8
        details.append('Insider selling signal')
    
    # Daily report adjustments
    if 'price_band_expansion' in daily_adjustments:
        score += 10
        details.append('Price band expanded (gap-up expected)')
    if 'price_band_contraction' in daily_adjustments:
        score -= 12
        details.append('Price band contracted (avoid)')
    if 'series_to_be' in daily_adjustments:
        score -= 20
        details.append('Moved to T2T series (avoid)')
        confidence -= 0.15
    if 'series_from_be' in daily_adjustments:
        score += 8
        details.append('Graduated from T2T series')
    
    # 52W H/L context
    if '52w_high' in daily_adjustments and score > 0:
        score += 5
        details.append('Near 52W high (momentum confirmation)')
    elif '52w_low' in daily_adjustments and score > 0:
        confidence -= 0.15
        details.append('Near 52W low (reversal candidate)')
    
    # Ensure score bounds
    score = max(-100, min(100, score))
    confidence = max(0.0, min(1.0, confidence))
    
    return score, confidence, details

def rank_equities(extract_data):
    """Rank equities based on signals and daily reports."""
    rankings = []
    
    # Get daily reports data
    daily_reports = extract_data.get('_daily_reports', {})
    price_band_changes = {pb['symbol']: pb for pb in daily_reports.get('price_band_changes', [])}
    series_changes = {sc['symbol']: sc for sc in daily_reports.get('series_changes', [])}
    w52_highs = {wh['symbol']: wh for wh in daily_reports.get('52_week_highs', [])}
    
    # Process each stock
    for symbol, stock_data in extract_data.items():
        if symbol == '_daily_reports':
            continue
        
        if not isinstance(stock_data, dict):
            continue
        
        text = stock_data.get('text', '')
        is_tradable_intraday = stock_data.get('is_tradable_intraday', False)
        
        # Extract signals
        signals = extract_signals_from_text(symbol, text)
        
        # Build daily adjustments
        daily_adjustments = set()
        
        if symbol in price_band_changes:
            pb = price_band_changes[symbol]
            if pb['direction'] == 'expanded':
                daily_adjustments.add('price_band_expansion')
            elif pb['direction'] == 'contracted':
                daily_adjustments.add('price_band_contraction')
        
        if symbol in series_changes:
            sc = series_changes[symbol]
            if sc['to_series'] == 'BE':
                daily_adjustments.add('series_to_be')
            elif sc['from_series'] == 'BE':
                daily_adjustments.add('series_from_be')
        
        if symbol in w52_highs:
            wh = w52_highs[symbol]
            # Simplified check - if any recent data point near high
            daily_adjustments.add('52w_high')
        
        # Calculate scores
        sentiment_score, confidence_score, reason_list = calculate_sentiment_score(
            signals, daily_adjustments, symbol, w52_highs.get(symbol, {})
        )
        
        # Determine labels
        if sentiment_score >= 30:
            sentiment_label = 'Positive'
        elif sentiment_score <= -30:
            sentiment_label = 'Negative'
        else:
            sentiment_label = 'Neutral'
        
        if confidence_score >= 0.7:
            confidence_label = 'High'
        elif confidence_score >= 0.4:
            confidence_label = 'Medium'
        else:
            confidence_label = 'Low'
        
        reason = '; '.join(reason_list) if reason_list else 'AGM/Announcement'
        
        rankings.append({
            'symbol': symbol,
            'sentiment_score': sentiment_score,
            'sentiment_label': sentiment_label,
            'confidence_score': round(confidence_score, 2),
            'confidence_label': confidence_label,
            'reason': reason[:100],  # Truncate to reasonable length
            'is_tradable_intraday': is_tradable_intraday,
            'sort_key': (-sentiment_score, -confidence_score)  # For sorting
        })
    
    # Sort: intraday tradable first (by score), then non-intraday
    intraday = [r for r in rankings if r['is_tradable_intraday']]
    non_intraday = [r for r in rankings if not r['is_tradable_intraday']]
    
    intraday.sort(key=lambda x: x['sort_key'])
    non_intraday.sort(key=lambda x: x['sort_key'])
    
    return intraday, non_intraday

def main():
    # Find latest extract file
    data_dir = Path(__file__).parent.parent / 'data'
    extract_files = sorted(data_dir.glob('nse_extracts_*.json'), reverse=True)
    
    if not extract_files:
        print("No NSE extract files found in data/")
        return
    
    extract_file = extract_files[0]
    extract_date = extract_file.stem.replace('nse_extracts_', '')
    
    print(f"Processing: {extract_file.name}")
    
    # Load extract data
    with open(extract_file, 'r', encoding='utf-8') as f:
        extract_data = json.load(f)
    
    # Rank equities
    intraday_rankings, non_intraday_rankings = rank_equities(extract_data)
    
    # Generate output CSV
    csv_file = data_dir / f'nse_rankings_{extract_date}.csv'
    
    with open(csv_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Symbol', 'Rank', 'Reason', 'SentimentLabel', 'SentimentScore', 'ConfidenceLabel', 'ConfidenceScore'])
        
        # Intraday section
        for rank, item in enumerate(intraday_rankings[:20], 1):  # Top 20
            if item['sentiment_score'] >= 10 or item['confidence_score'] >= 0.5:  # Only high-conviction
                writer.writerow([
                    item['symbol'],
                    rank,
                    item['reason'],
                    item['sentiment_label'],
                    item['sentiment_score'],
                    item['confidence_label'],
                    item['confidence_score']
                ])
        
        # Separator
        writer.writerow([])
        
        # Short-term investment section
        if non_intraday_rankings:
            writer.writerow(['Section', 'SHORT_TERM_INVESTMENT', '', '', '', '', ''])
            writer.writerow(['Symbol', 'Rank', 'Reason', 'SentimentLabel', 'SentimentScore', 'ConfidenceLabel', 'ConfidenceScore'])
            
            for rank, item in enumerate(non_intraday_rankings[:10], 1):
                if item['sentiment_score'] >= 15:  # Higher threshold for non-intraday
                    writer.writerow([
                        item['symbol'],
                        rank,
                        item['reason'],
                        item['sentiment_label'],
                        item['sentiment_score'],
                        item['confidence_label'],
                        item['confidence_score']
                    ])
    
    print(f"\nRankings saved to: {csv_file}")
    print(f"\nIntraday BUY candidates: {len([r for r in intraday_rankings if r['sentiment_score'] >= 10])}")
    print(f"Short-term investment candidates: {len([r for r in non_intraday_rankings if r['sentiment_score'] >= 15])}")
    
    # Print summary
    print("\n" + "="*80)
    print("INTRADAY BUY WATCHLIST (Top 10)")
    print("="*80)
    for rank, item in enumerate(intraday_rankings[:10], 1):
        if item['sentiment_score'] >= 10:
            print(f"\n{rank}. {item['symbol']}")
            print(f"   Sentiment: {item['sentiment_label']} ({item['sentiment_score']})")
            print(f"   Confidence: {item['confidence_label']} ({item['confidence_score']})")
            print(f"   Reason: {item['reason']}")

if __name__ == '__main__':
    main()
