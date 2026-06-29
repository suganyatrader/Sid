import fs from 'fs';
import path from 'path';
import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const API_KEY = process.env.GROWW_API_KEY;
const API_SECRET = process.env.GROWW_API_SECRET;
const BASE_URL = process.env.GROWW_BASE_URL || 'https://api.groww.in/v1/trading';
const STOCK_IDS = (process.env.STOCK_IDS || 'RELIANCE').split(',').map((id) => id.trim()).filter(Boolean);
const POLL_INTERVAL_SECONDS = Number(process.env.POLL_INTERVAL_SECONDS || 30);
const DATA_DIR = path.resolve(process.cwd(), '../data');
const LIVE_QUOTES_FILE = path.join(DATA_DIR, 'live_quotes.json');

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
  }
}

function getAuthHeader() {
  if (!API_KEY) {
    throw new Error('GROWW_API_KEY is required in .env');
  }

  return {
    Authorization: `Bearer ${API_KEY}`,
    'Content-Type': 'application/json'
  };
}

async function getLiveQuote(stockId) {
  const url = `${BASE_URL}/market/quote/${stockId}`;
  const response = await axios.get(url, { headers: getAuthHeader() });
  return response.data;
}

async function fetchAllQuotes() {
  const quotes = {};

  for (const stockId of STOCK_IDS) {
    try {
      const quote = await getLiveQuote(stockId);
      quotes[stockId] = {
        fetchedAt: new Date().toISOString(),
        quote
      };
      console.log(`[ingest] ${stockId}: retrieved quote successfully`);
    } catch (error) {
      console.error(`[ingest] ${stockId}: failed to fetch quote`, error.message || error);
    }
  }

  return quotes;
}

function writeLiveQuotes(quotes) {
  fs.writeFileSync(LIVE_QUOTES_FILE, JSON.stringify(quotes, null, 2), 'utf8');
}

async function main() {
  ensureDataDir();

  console.log('[ingest] Starting Groww quote ingestion');
  console.log(`[ingest] Polling every ${POLL_INTERVAL_SECONDS}s for ${STOCK_IDS.join(', ')}`);

  const quotes = await fetchAllQuotes();
  writeLiveQuotes(quotes);
  console.log(`[ingest] Wrote ${Object.keys(quotes).length} quotes to ${LIVE_QUOTES_FILE}`);

  setInterval(async () => {
    const refreshedQuotes = await fetchAllQuotes();
    writeLiveQuotes(refreshedQuotes);
    console.log(`[ingest] Updated ${Object.keys(refreshedQuotes).length} quotes at ${new Date().toISOString()}`);
  }, POLL_INTERVAL_SECONDS * 1000);
}

main().catch((error) => {
  console.error('[ingest] Fatal error', error);
  process.exit(1);
});
