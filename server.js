const express = require('express');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs'); 
const mongoose = require('mongoose');
const session = require('express-session');
const bcrypt = require('bcryptjs');
const User = require('./models/User'); 
const axios = require('axios'); 
const cron = require('node-cron'); 
const app = express();

// ==========================================
// 1. CONFIGURATION & ENVIRONMENT
// ==========================================
const MONGO_URI = 'mongodb://127.0.0.1:27017/stockpulse_db'; 
const SESSION_SECRET = 'supersecret_stockpulse_key'; 
const DATASET_PATH = path.resolve(__dirname, 'datasets'); 
const CACHE_DIR = path.resolve(__dirname, 'server_cache'); // NEW: Dedicated disk cache folder

let PYTHON_PATH = 'python'; 
const serverVenvPath = '/var/www/FinoraPulse/venv/bin/python3';

if (fs.existsSync(serverVenvPath)) {
    PYTHON_PATH = serverVenvPath;
    console.log(`🐍 Using Server Python Environment: ${PYTHON_PATH}`);
} else {
    console.log(`🐍 Using Local Python Environment: ${PYTHON_PATH}`);
}

// Check and create folders with robust permissions
if (!fs.existsSync(DATASET_PATH)) {
    fs.mkdirSync(DATASET_PATH, { recursive: true, mode: 0o777 });
    console.log("📁 Created datasets folder");
}
if (!fs.existsSync(CACHE_DIR)) {
    fs.mkdirSync(CACHE_DIR, { recursive: true, mode: 0o777 });
    console.log("📁 Created server_cache folder for Disk Caching");
}

// ==========================================
// 1.5 ASYNC DISK CACHE SYSTEM (Saves RAM)
// ==========================================
async function getDiskCache(rawKey, ttlMs) {
    // Make key safe for filenames (removes weird characters)
    const safeKey = rawKey.replace(/[^a-z0-9_]/gi, '_');
    const filePath = path.join(CACHE_DIR, `${safeKey}.json`);
    
    try {
        const stats = await fs.promises.stat(filePath);
        if (Date.now() - stats.mtimeMs < ttlMs) {
            const rawData = await fs.promises.readFile(filePath, 'utf-8');
            return JSON.parse(rawData);
        } else {
            // Delete expired cache file to save disk space
            await fs.promises.unlink(filePath).catch(() => {});
        }
    } catch (e) {
        return null; // File doesn't exist or expired
    }
    return null;
}

async function setDiskCache(rawKey, data) {
    const safeKey = rawKey.replace(/[^a-z0-9_]/gi, '_');
    const filePath = path.join(CACHE_DIR, `${safeKey}.json`);
    try {
        await fs.promises.writeFile(filePath, JSON.stringify(data));
    } catch (e) {
        console.error(`❌ Disk Cache Write Error for ${safeKey}:`, e.message);
    }
}


// ==========================================
// 2. MIDDLEWARE & AUTHENTICATION
// ==========================================
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static('public'));

app.use(session({
    secret: SESSION_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: { maxAge: 1000 * 60 * 60 * 24 * 7 } 
}));

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

mongoose.connect(MONGO_URI)
    .then(() => console.log("✅ MongoDB Connected"))
    .catch(err => console.error("❌ MongoDB Error:", err));

const requireLogin = (req, res, next) => {
    if (!req.session.userId) return res.redirect('/auth');
    next();
};

app.use(async (req, res, next) => {
    res.locals.user = null;
    if (req.session.userId) {
        const user = await User.findById(req.session.userId);
        if (user) res.locals.user = user;
    }
    next();
});

// --- AUTH ROUTES ---
app.get('/auth', (req, res) => {
    if (req.session.userId) return res.redirect('/');
    res.render('auth');
});

app.post('/register', async (req, res) => {
    const { email, username, password, confirmPassword } = req.body;
    if (password !== confirmPassword) return res.render('auth', { error: "Passwords do not match" });

    try {
        const existingUser = await User.findOne({ $or: [{ email }, { username }] });
        if (existingUser) return res.render('auth', { error: "User ID or Email already exists" });

        const hashedPassword = await bcrypt.hash(password, 10);
        const newUser = new User({ email, username, password: hashedPassword });
        await newUser.save();

        req.session.userId = newUser._id;
        res.redirect('/');
    } catch (err) {
        res.render('auth', { error: "Error creating account. Try again." });
    }
});

app.post('/login', async (req, res) => {
    const { loginInput, password } = req.body;
    try {
        const user = await User.findOne({ $or: [{ email: loginInput }, { username: loginInput }] });
        if (!user) return res.render('auth', { error: "Invalid credentials" });

        const isMatch = await bcrypt.compare(password, user.password);
        if (!isMatch) return res.render('auth', { error: "Invalid credentials" });

        req.session.userId = user._id;
        res.redirect('/');
    } catch (err) {
        res.render('auth', { error: "Login failed. Please try again." });
    }
});

app.get('/logout', (req, res) => {
    req.session.destroy(() => res.redirect('/'));
});

// ==========================================
// 3. FRONTEND PAGE ROUTES
// ==========================================
app.get('/', (req, res) => res.render('home'));
app.get('/predict', requireLogin, (req, res) => res.render('predict', { ticker: (req.query.ticker || 'RELIANCE.NS').toUpperCase() }));
app.get('/macro', requireLogin, (req, res) => res.render('macro', { country: req.query.country || 'IN' }));
app.get('/heatmap', requireLogin, (req, res) => res.render('heatmap', { country: (req.query.country || 'US').toUpperCase() }));

// ==========================================
// 4. PYTHON EXECUTION HELPER 
// ==========================================
function fetchPythonData(folder, scriptName, argsArray = []) {
    return new Promise((resolve) => {
        const scriptPath = path.resolve(__dirname, 'python_engine', folder, scriptName);
        const args = [scriptPath, ...argsArray]; 
        
        console.log(`🚀 Executing: ${PYTHON_PATH} ${args.join(' ')}`);
        
        const pythonProcess = spawn(PYTHON_PATH, args);
        let dataString = '';
        let errorString = '';
        
        pythonProcess.stdout.on('data', (data) => { dataString += data.toString(); });
        pythonProcess.stderr.on('data', (data) => { errorString += data.toString(); });
        
        pythonProcess.on('close', (code) => {
            if (errorString) {
                console.error(`\n[🐍 PYTHON STDERR] ${scriptName}:\n${errorString}\n`);
            }
            try { 
                const jsonData = JSON.parse(dataString);
                resolve(jsonData); 
            } catch (e) { 
                console.error(`❌ [JSON PARSE ERROR] Failed to parse output from ${scriptName}.`);
                resolve({ error: "Prediction engine failed on server. Check server console logs for Python errors." }); 
            }
        });
    });
}

// ==========================================
// 5. PREDICT PAGE CHART CACHE (Disk Based)
// ==========================================
// ==========================================
// 5. PREDICT PAGE CHART CACHE (Disk Based)
// ==========================================
const PREDICT_CACHE_TTL = 12 * 60 * 60 * 1000; // 12 Hours

app.get('/api/stats', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d'; 
    
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const todayDate = new Date().toISOString().split('T')[0]; 
    const cacheKey = `predict_${ticker}_${timeframe}_${todayDate}`;

    // Read from Hard Drive instead of RAM
    const cachedData = await getDiskCache(cacheKey, PREDICT_CACHE_TTL);
    if (cachedData) {
        console.log(`⚡ Serving locked daily prediction from DISK for ${ticker} (${timeframe})`);
        return res.json(cachedData);
    }

    // Fetch data using the newly optimized Python script
    const result = await fetchPythonData('ml_models', 'ml_engine.py', ['predict', ticker, timeframe, DATASET_PATH]);
    
    if (!result.error) {
        await setDiskCache(cacheKey, result); // Save to Hard Drive
    }
    
    res.json(result); 
});
// ==========================================
// 6. MACRO BATCH DOWNLOADER & CACHE WARMER
// ==========================================
const MACRO_CACHE_TTL_MS = 12 * 60 * 60 * 1000; 
const SUPPORTED_COUNTRIES = [
    "US", "CN", "DE", "JP", "IN", "GB", "FR", "IT", "BR", "CA", 
    "KR", "AU", "MX", "ES", "ID", "NL", "SA", "CH", "TW", "PL", 
    "SE", "BE", "SG", "HK", "ZA"
]; 

async function runMacroBatchUpdate() {
    console.log("🌎 [MACRO BATCH] Starting Global Economic Sync...");
    const corrData = await fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']);
    if (!corrData.error) await setDiskCache('macro_correlation', corrData);

    for (const country of SUPPORTED_COUNTRIES) {
        try {
            const macroData = await fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]);
            if (!macroData.error) {
                await setDiskCache(`macro_${country}`, macroData);
                console.log(`✅ Cached Macro to Disk: ${country}`);
            }

            const liquidityData = await fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]);
            if (!liquidityData.error) await setDiskCache(`liquidity_${country}`, liquidityData);

            await new Promise(resolve => setTimeout(resolve, 5000)); 
        } catch (err) {
            console.error(`❌ Batch failed for ${country}:`, err.message);
        }
    }
    console.log("🏁 [MACRO BATCH] Sync Complete!");
}

cron.schedule('0 3 * * 0', runMacroBatchUpdate);
runMacroBatchUpdate();

// ==========================================
// API ROUTES (Macro, Features, Search)
// ==========================================
const buildLocks = {}; // Tiny RAM object just to prevent simultaneous Python script spawns

app.get('/api/macro-explorer', async (req, res) => {
    const country = (req.query.country || 'IN').toUpperCase();
    const cacheKey = `macro_${country}`;

    const cachedData = await getDiskCache(cacheKey, MACRO_CACHE_TTL_MS);
    if (cachedData) return res.json(cachedData);

    if (!buildLocks[`building_${country}`]) {
        buildLocks[`building_${country}`] = true;
        fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]).then(async liveData => {
            if (!liveData.error) {
                await setDiskCache(cacheKey, liveData);
            }
            delete buildLocks[`building_${country}`];
        });
    }

    return res.status(202).json({ status: "building", message: "Compiling global economic data. Please wait a few seconds..." });
});

app.get('/api/global-liquidity', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const cachedData = await getDiskCache(`liquidity_${country}`, MACRO_CACHE_TTL_MS);
    if (cachedData) return res.json(cachedData);
    
    fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]).then(data => res.json(data));
});

app.get('/api/correlation', async (req, res) => {
    const cachedData = await getDiskCache('macro_correlation', MACRO_CACHE_TTL_MS);
    if (cachedData) return res.json(cachedData);

    fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']).then(data => res.json(data));
});


// 🎯 DISK CACHE STRATEGY FOR FEATURES
const TTL_MAP = {
    'fundamentals': 15 * 24 * 60 * 60 * 1000,    // 15 Days 
    'peers': 15 * 24 * 60 * 60 * 1000,           // 15 Days 
    'smart_money_13f': 15 * 24 * 60 * 60 * 1000, // 15 Days 
    'smart_money_smi': 24 * 60 * 60 * 1000,      // 24 Hours 
    'smart_money_options': 5 * 60 * 1000,        // 5 Minutes 
    'sentiment': 4 * 60 * 60 * 1000,             // 4 Hours 
    'earnings_nlp': 24 * 60 * 60 * 1000,         // 24 Hours 
    'peer_history': 12 * 60 * 60 * 1000,         // 12 Hours
    'heatmap': 1 * 60 * 60 * 1000                // 1 Hour
};

async function getCachedFeature(featureType, folder, scriptName, argsArray) {
    const cacheKey = `feature_${featureType}_${argsArray.join('_')}`;
    const ttl = TTL_MAP[featureType] || (4 * 60 * 60 * 1000); 

    // Look for file on disk
    const cachedData = await getDiskCache(cacheKey, ttl);
    if (cachedData) return cachedData;

    const data = await fetchPythonData(folder, scriptName, argsArray);
    if (!data.error) {
        await setDiskCache(cacheKey, data); // Write file to disk
    }
    return data;
}

app.get('/api/fundamentals', async (req, res) => {
    if (!req.query.ticker) return res.status(400).json({ error: "Ticker required" });
    res.json(await getCachedFeature('fundamentals', 'fundamentals', 'fundamentals_engine.py', ['fundamentals', req.query.ticker]));
});

app.get('/api/peers', async (req, res) => {
    if (!req.query.ticker) return res.status(400).json({ error: "Ticker required" });
    res.json(await getCachedFeature('peers', 'fundamentals', 'fundamentals_engine.py', ['peers', req.query.ticker]));
});

app.get('/api/smart-money', async (req, res) => {
    const ticker = req.query.ticker;
    const type = req.query.type || 'smi'; 
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    let cacheFeatureType = 'smart_money_smi';
    if (type === '13f') cacheFeatureType = 'smart_money_13f';
    if (type === 'options') cacheFeatureType = 'smart_money_options';

    res.json(await getCachedFeature(cacheFeatureType, 'fundamentals', 'fundamentals_engine.py', ['smart_money', ticker, type]));
});

app.get('/api/sentiment', async (req, res) => {
    if (!req.query.ticker) return res.status(400).json({ error: "Ticker required" });
    res.json(await getCachedFeature('sentiment', 'ml_models', 'ml_engine.py', ['sentiment', req.query.ticker]));
});

app.get('/api/earnings-nlp', async (req, res) => {
    if (!req.query.ticker) return res.status(400).json({ error: "Ticker required" });
    res.json(await getCachedFeature('earnings_nlp', 'ml_models', 'ml_engine.py', ['earnings', req.query.ticker]));
});

app.get('/api/heatmap-data', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const data = await fetchPythonData('macro_quant', 'macro_engine.py', ['heatmap', country]);
    res.json(data);
});


// 🎯 DISK CACHE STRATEGY FOR SEARCH
const SEARCH_CACHE_TTL = 60 * 60 * 1000; 

app.get('/api/search-suggest', async (req, res) => {
    const query = req.query.q?.toLowerCase();
    if (!query) return res.json([]);

    const cacheKey = `search_${query}`;
    const cachedData = await getDiskCache(cacheKey, SEARCH_CACHE_TTL);
    if (cachedData) return res.json(cachedData);

    try {
        const response = await axios.get(`https://query1.finance.yahoo.com/v1/finance/search?q=${query}`);
        const suggestions = response.data.quotes.map(quote => {
            const isIndian = quote.exchange === 'NSI' || quote.exchange === 'BSE' || (quote.symbol && quote.symbol.endsWith('.NS')) || (quote.symbol && quote.symbol.endsWith('.BO'));
            return {
                symbol: quote.symbol,
                name: quote.shortname || quote.longname || quote.symbol,
                region: isIndian ? '🇮🇳 India' : '🇺🇸 Global/USA',
                type: formatType(quote.quoteType),
                exchange: quote.exchDisp
            };
        }).slice(0, 10);

        if (query.includes('gold rate')) suggestions.unshift({ symbol: 'GC=F', name: 'Spot Gold Rate', region: '🇺🇸 Global/USA', type: 'Rate', exchange: 'COMEX' });
        
        await setDiskCache(cacheKey, suggestions); // Save Search to Disk
        res.json(suggestions);
    } catch (err) { res.json([]); }
});

function formatType(type) {
    const types = { 'EQUITY': 'Stock', 'CRYPTO': 'Crypto', 'ETF': 'ETF', 'INDEX': 'Index', 'CURRENCY': 'Forex', 'MUTUALFUND': 'Fund', 'FUTURE': 'Commodity' };
    return types[type] || type;
}

const { SitemapStream, streamToPromise } = require('sitemap');
const { Readable } = require('stream');

// ==========================================
// SITEMAP GENERATION
// ==========================================
app.get('/sitemap.xml', async (req, res) => {
    try {
        const links = [
            { url: '/', changefreq: 'daily', priority: 1.0 },
            { url: '/auth', changefreq: 'monthly', priority: 0.5 },
            { url: '/predict', changefreq: 'daily', priority: 0.8 },
            { url: '/macro', changefreq: 'weekly', priority: 0.7 },
            { url: '/heatmap', changefreq: 'weekly', priority: 0.7 },
        ];

        const stream = new SitemapStream({ hostname: 'https://finorapulse.com' });
        const xmlString = await streamToPromise(Readable.from(links).pipe(stream)).then((data) =>
            data.toString()
        );

        res.header('Content-Type', 'application/xml');
        res.send(xmlString);
    } catch (e) {
        console.error(e);
        res.status(500).end();
    }
});

const PORT = 3000;
app.listen(PORT, () => console.log(`🚀 FinoraPulse Live at: http://localhost:${PORT}`));