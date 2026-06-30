import axios from 'axios';
import dotenv from 'dotenv';

dotenv.config();

const API_KEY = process.env.GROWW_API_KEY;
const BASE_URL = process.env.GROWW_BASE_URL || 'https://api.groww.in/v1/trading';
const STOCK_IDS = (process.env.STOCK_IDS || 'RELIANCE').split(',').map((id) => id.trim()).filter(Boolean);
const POLL_INTERVAL_SECONDS = Number(process.env.POLL_INTERVAL_SECONDS || 30);

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

async function main() {
  console.log('[ingest] Starting Groww quote ingestion');
  console.log(`[ingest] Polling every ${POLL_INTERVAL_SECONDS}s for ${STOCK_IDS.join(', ')}`);

  const quotes = await fetchAllQuotes();
  console.log('[ingest] In-memory quotes snapshot:');
  console.log(JSON.stringify(quotes, null, 2));

  setInterval(async () => {
    const refreshedQuotes = await fetchAllQuotes();
    console.log('[ingest] Refreshed in-memory quotes snapshot:');
    console.log(JSON.stringify(refreshedQuotes, null, 2));
  }, POLL_INTERVAL_SECONDS * 1000);
}

main().catch((error) => {
  console.error('[ingest] Fatal error', error);
  process.exit(1);
});
