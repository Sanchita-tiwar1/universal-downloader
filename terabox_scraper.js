/**
 * TeraBox Scraper for DownTG Bot
 * Dual Engine: Direct Ultra-Fast REST API + Headless Puppeteer Chromium Fallback
 */

const fs = require('fs');
const path = require('path');
const https = require('https');
let puppeteer = null;
try {
  puppeteer = require('puppeteer');
} catch (e) {}

let SITE_URL = 'https://gregarious-haupia-5bced4.netlify.app/';
let EXTRACT_API = 'https://gregarious-haupia-5bced4.netlify.app/api/extract';
let API_KEY = process.env.TERABOX_API_KEY || '';

try {
  const cfgPath = path.join(__dirname, 'data', 'terabox_config.json');
  if (fs.existsSync(cfgPath)) {
    const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
    if (cfg.terabox_api_key) API_KEY = cfg.terabox_api_key;
    if (cfg.terabox_endpoint) {
      EXTRACT_API = cfg.terabox_endpoint;
      SITE_URL = cfg.terabox_endpoint.replace(/\/api\/.*$/, '/');
    }
  }
} catch (e) {}

if (process.env.TERABOX_ENDPOINT) {
  EXTRACT_API = process.env.TERABOX_ENDPOINT;
  SITE_URL = process.env.TERABOX_ENDPOINT.replace(/\/api\/.*$/, '/');
}

/**
 * 1. Ultra-fast direct API extraction (responds in ~0.5s without browser overhead)
 */
function directApiExtract(targetUrl) {
  return new Promise((resolve) => {
    const postData = JSON.stringify({ url: targetUrl, key: API_KEY, api_key: API_KEY });
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(postData),
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    };
    if (API_KEY) {
      headers['Authorization'] = 'Bearer ' + API_KEY;
      headers['X-API-Key'] = API_KEY;
    }

    let transport = https;
    try {
      const parsed = new URL(EXTRACT_API);
      if (parsed.protocol === 'http:') transport = require('http');
    } catch (e) {}

    const req = transport.request(EXTRACT_API, {
      method: 'POST',
      headers: headers,
      timeout: 15000
    }, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try {
          const json = JSON.parse(body);
          if (res.statusCode === 200 && json && json.status === 'success' && json.files && json.files.length > 0) {
            resolve({ success: true, file: json.files[0] });
          } else {
            resolve({ success: false, error: json.error || ('API returned status ' + res.statusCode) });
          }
        } catch (e) {
          resolve({ success: false, error: 'Invalid JSON response from extractor' });
        }
      });
    });
    req.on('error', (e) => resolve({ success: false, error: e.message }));
    req.on('timeout', () => { req.destroy(); resolve({ success: false, error: 'API Timeout' }); });
    req.write(postData);
    req.end();
  });
}

/**
 * Main scraper with automatic fallback
 */
async function scrapeTerabox(targetUrl) {
  const result = {
    success: false,
    title: null,
    size: null,
    size_bytes: null,
    duration: null,
    thumbnail: null,
    media_url: null,
    download_url: null,
    stream_url: null,
    quality: null,
    error: null
  };

  // Step 1: Try Direct API (Instant, light, works 99% of the time)
  try {
    const directRes = await directApiExtract(targetUrl);
    if (directRes.success && directRes.file) {
      const f = directRes.file;
      let fastStream = null;
      let selectedQuality = null;

      if (Array.isArray(f.streams) && f.streams.length > 0) {
        const sorted = [...f.streams].sort((a, b) => {
          const na = parseInt(String(a.label || '').replace(/\D/g, '') || '0', 10);
          const nb = parseInt(String(b.label || '').replace(/\D/g, '') || '0', 10);
          return nb - na;
        });
        fastStream = sorted[0].url;
        selectedQuality = sorted[0].label;
      }

      result.success = true;
      result.title = f.name || 'TeraBox_Video';
      result.size = f.size || null;
      result.size_bytes = f.size_bytes || null;
      result.duration = typeof f.duration === 'number' ? Math.round(f.duration) : null;
      result.thumbnail = f.thumbnail || null;
      result.download_url = f.download_url || null;
      result.stream_url = fastStream || f.download_url;
      result.media_url = result.stream_url || result.download_url;
      result.quality = selectedQuality;
      return result;
    } else if (directRes.error && directRes.error.includes('Code 102')) {
      result.success = false;
      result.error = 'Invalid TeraBox Link or File has been deleted by owner (Code 102).';
      return result;
    }
  } catch (apiErr) {
    // Fall through to Puppeteer
  }

  // Step 2: Puppeteer Fallback (If direct API had connection issue)
  if (!puppeteer) {
    result.success = false;
    result.error = 'TeraBox API extraction failed and Puppeteer is not installed.';
    return result;
  }

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: 'new',
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-blink-features=AutomationControlled',
        '--window-size=1280,800'
      ]
    });

    const page = await browser.newPage();
    await page.setUserAgent(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    );
    await page.setViewport({ width: 1280, height: 800 });

    let apiResponseData = null;
    page.on('response', async (res) => {
      if (res.url().includes('/api/extract')) {
        try {
          const json = await res.json();
          if (json && json.status === 'success' && json.files && json.files.length > 0) {
            apiResponseData = json.files[0];
          }
        } catch (e) {}
      }
    });

    await page.goto(SITE_URL, { waitUntil: 'networkidle2', timeout: 35000 });

    const inputSelector = 'input#share-url, input[placeholder*="Paste any TeraBox share link here"]';
    await page.waitForSelector(inputSelector, { timeout: 15000 });
    await page.click(inputSelector, { clickCount: 3 }).catch(() => {});
    await page.type(inputSelector, targetUrl.trim());

    const btnSelector = 'button#extract-btn, form button[type="submit"]';
    await page.waitForSelector(btnSelector, { timeout: 10000 });
    await page.click(btnSelector);

    await page.waitForFunction(() => {
      const qs = document.querySelector('#quality-select');
      const ws = document.querySelector('#workspace');
      const err = document.querySelector('#notice');
      if (err && !err.hidden && err.classList.contains('notice') && !err.classList.contains('success')) {
        return true;
      }
      return (ws && !ws.hidden) && (qs && qs.options && qs.options.length > 0 && qs.options[0].text !== 'Auto');
    }, { timeout: 25000 });

    const noticeText = await page.evaluate(() => {
      const el = document.querySelector('#notice');
      if (el && !el.hidden && !el.classList.contains('success')) {
        return el.textContent.trim();
      }
      return null;
    });

    if (noticeText) {
      throw new Error(noticeText);
    }

    const selectedQuality = await page.evaluate(() => {
      const select = document.querySelector('#quality-select');
      if (!select || !select.options || select.options.length === 0) return null;
      const opts = Array.from(select.options).map((opt) => ({
        value: opt.value,
        text: opt.textContent.trim(),
        numeric: parseInt(opt.textContent.replace(/\D/g, '') || '0', 10)
      }));
      opts.sort((a, b) => b.numeric - a.numeric);
      const best = opts[0];
      if (best) {
        select.value = best.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
        return best.text;
      }
      return null;
    });
    result.quality = selectedQuality;

    await page.waitForFunction(() => {
      const player = document.querySelector('video#video-player');
      const src = player ? (player.getAttribute('src') || player.src || player.currentSrc) : null;
      return src && src.startsWith('http') && !src.startsWith('blob:');
    }, { timeout: 8000 }).catch(() => {});

    const uiData = await page.evaluate(() => {
      const nameEl = document.querySelector('#selected-name');
      const sizeEl = document.querySelector('#selected-size');
      const durEl = document.querySelector('#selected-duration');
      const dlBtn = document.querySelector('#download-btn');
      const player = document.querySelector('video#video-player');
      const thumbEl = document.querySelector('#file-list img');
      const dlUrl = dlBtn && !dlBtn.classList.contains('disabled') ? dlBtn.href : null;
      const stUrl = player ? (player.getAttribute('src') || player.src || player.currentSrc) : null;
      return {
        title: nameEl ? nameEl.textContent.trim() : null,
        sizeText: sizeEl ? sizeEl.textContent.trim() : null,
        duration: durEl ? durEl.textContent.trim() : null,
        downloadUrl: dlUrl && !dlUrl.endsWith('#') ? dlUrl : null,
        streamUrl: stUrl && !stUrl.startsWith('blob:') ? stUrl : null,
        thumbnail: thumbEl ? thumbEl.src : null
      };
    });

    let fastStreamUrl = uiData.streamUrl;
    if (!fastStreamUrl && apiResponseData && Array.isArray(apiResponseData.streams) && apiResponseData.streams.length > 0) {
      const matched = apiResponseData.streams.find(s => s.label === selectedQuality);
      fastStreamUrl = matched ? matched.url : apiResponseData.streams[apiResponseData.streams.length - 1].url;
    }

    result.title = uiData.title || (apiResponseData ? apiResponseData.name : 'TeraBox_Video');
    result.size = uiData.sizeText || (apiResponseData ? apiResponseData.size : null);
    result.size_bytes = apiResponseData ? apiResponseData.size_bytes : null;

    let durationSeconds = null;
    const durRaw = uiData.duration || (apiResponseData ? apiResponseData.duration : null);
    if (durRaw) {
      if (typeof durRaw === 'number') {
        durationSeconds = Math.round(durRaw);
      } else if (typeof durRaw === 'string') {
        const parts = durRaw.split(':').map(Number);
        if (parts.length === 3 && !parts.some(isNaN)) {
          durationSeconds = parts[0] * 3600 + parts[1] * 60 + parts[2];
        } else if (parts.length === 2 && !parts.some(isNaN)) {
          durationSeconds = parts[0] * 60 + parts[1];
        } else if (!isNaN(Number(durRaw))) {
          durationSeconds = parseInt(durRaw, 10);
        }
      }
    }
    result.duration = durationSeconds;
    result.thumbnail = uiData.thumbnail || (apiResponseData ? apiResponseData.thumbnail : null);
    result.download_url = uiData.downloadUrl || (apiResponseData ? apiResponseData.download_url : null);
    result.stream_url = fastStreamUrl;
    result.media_url = result.stream_url || result.download_url;

    if (!result.media_url) {
      throw new Error('Could not retrieve fast stream or download URL from website.');
    }

    result.success = true;
    return result;
  } catch (err) {
    result.success = false;
    result.error = err.message || String(err);
    return result;
  } finally {
    if (browser) {
      await browser.close().catch(() => {});
    }
  }
}

// CLI Interface
const inputUrl = process.argv[2];
if (!inputUrl) {
  console.log(JSON.stringify({ success: false, error: 'No URL provided' }));
  process.exit(1);
}

scrapeTerabox(inputUrl).then((res) => {
  console.log(JSON.stringify(res));
  process.exit(res.success ? 0 : 1);
});
