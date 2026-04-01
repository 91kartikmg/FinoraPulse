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

let PYTHON_PATH = 'python'; 
const serverVenvPath = '/var/www/FinoraPulse/venv/bin/python3';

if (fs.existsSync(serverVenvPath)) {
    PYTHON_PATH = serverVenvPath;
    console.log(`🐍 Using Server Python Environment: ${PYTHON_PATH}`);
} else {
    console.log(`🐍 Using Local Python Environment: ${PYTHON_PATH}`);
}

// Check and create datasets directory with robust permissions
if (!fs.existsSync(DATASET_PATH)) {
    fs.mkdirSync(DATASET_PATH, { recursive: true, mode: 0o777 });
    console.log("📁 Created datasets folder");
} else {
    // Attempt to test write permissions
    try {
        fs.accessSync(DATASET_PATH, fs.constants.W_OK);
        console.log("📁 Dataset folder is writable.");
    } catch (err) {
        console.error("❌ CRITICAL: No write access to the datasets folder. Python will fail to save CSVs!");
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
// 4. PYTHON EXECUTION HELPER (HEAVILY UPGRADED)
// ==========================================
function fetchPythonData(folder, scriptName, argsArray = []) {
    return new Promise((resolve) => {
        const scriptPath = path.resolve(__dirname, 'python_engine', folder, scriptName);
        const args = [scriptPath, ...argsArray]; 
        
        // Log exactly what is being executed for easy debugging
        console.log(`🚀 Executing: ${PYTHON_PATH} ${args.join(' ')}`);
        
        const pythonProcess = spawn(PYTHON_PATH, args);
        let dataString = '';
        let errorString = '';
        
        // Capture standard output (JSON from Python)
        pythonProcess.stdout.on('data', (data) => { dataString += data.toString(); });
        
        // Capture standard error (Crashes, warnings, missing libraries)
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
                console.error(`RAW OUTPUT RECEIVED:`, dataString);
                resolve({ error: "Prediction engine failed on server. Check server console logs for Python errors." }); 
            }
        });
    });
}

// ==========================================
// 5. PREDICT PAGE CHART CACHE
// ==========================================
const predictCache = {}; 
const PREDICT_CACHE_TTL = 3 * 60 * 1000; 

app.get('/api/stats', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d'; // <-- UPDATED
    
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const cacheKey = `${ticker}_${timeframe}`;

    if (predictCache[cacheKey] && (Date.now() - predictCache[cacheKey].timestamp < PREDICT_CACHE_TTL)) {
        return res.json(predictCache[cacheKey].data);
    }

    const result = await fetchPythonData('ml_models', 'ml_engine.py', ['predict', ticker, timeframe, DATASET_PATH]);
    
    if (!result.error) predictCache[cacheKey] = { data: result, timestamp: Date.now() };
    res.json(result);
});

// ==========================================
// 6. MACRO BATCH DOWNLOADER & CACHE WARMER
// ==========================================
const CACHE_TTL_MS = 12 * 60 * 60 * 1000; 
const apiCache = { macro: {}, liquidity: {}, correlation: null };
const SUPPORTED_COUNTRIES = [
    "US", "CN", "DE", "JP", "IN", "GB", "FR", "IT", "BR", "CA", 
    "KR", "AU", "MX", "ES", "ID", "NL", "SA", "CH", "TW", "PL", 
    "SE", "BE", "SG", "HK", "ZA"
]; 

async function runMacroBatchUpdate() {
    console.log("🌎 [MACRO BATCH] Starting Global Economic Sync...");
    const corrData = await fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']);
    if (!corrData.error) apiCache.correlation = { data: corrData, timestamp: Date.now() };

    for (const country of SUPPORTED_COUNTRIES) {
        try {
            const macroData = await fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]);
            if (!macroData.error) {
                const filePath = path.join(DATASET_PATH, `${country}_macro.json`);
                fs.writeFileSync(filePath, JSON.stringify(macroData));
                apiCache.macro[country] = { data: macroData, timestamp: Date.now() };
                console.log(`✅ Cached Macro: ${country}`);
            }

            const liquidityData = await fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]);
            if (!liquidityData.error) apiCache.liquidity[country] = { data: liquidityData, timestamp: Date.now() };

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
app.get('/api/macro-explorer', async (req, res) => {
    const country = (req.query.country || 'IN').toUpperCase();
    const diskPath = path.join(DATASET_PATH, `${country}_macro.json`);

    if (apiCache.macro[country]) return res.json(apiCache.macro[country].data);

    if (fs.existsSync(diskPath)) {
        try {
            const data = JSON.parse(fs.readFileSync(diskPath));
            apiCache.macro[country] = { data, timestamp: Date.now() }; 
            return res.json(data);
        } catch (e) { console.error("Disk read error", e); }
    }

    if (!apiCache.macro[`building_${country}`]) {
        apiCache.macro[`building_${country}`] = true;
        fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]).then(liveData => {
            if (!liveData.error) {
                fs.writeFileSync(diskPath, JSON.stringify(liveData));
                apiCache.macro[country] = { data: liveData, timestamp: Date.now() };
            }
            delete apiCache.macro[`building_${country}`];
        });
    }

    return res.status(202).json({ status: "building", message: "Compiling global economic data. Please wait a few seconds..." });
});

app.get('/api/global-liquidity', (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    if (apiCache.liquidity[country] && (Date.now() - apiCache.liquidity[country].timestamp < CACHE_TTL_MS)) return res.json(apiCache.liquidity[country].data);
    fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]).then(data => res.json(data));
});

app.get('/api/correlation', (req, res) => {
    if (apiCache.correlation && (Date.now() - apiCache.correlation.timestamp < CACHE_TTL_MS)) return res.json(apiCache.correlation.data);
    fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']).then(data => res.json(data));
});

const featureCache = {};

// 🎯 UPDATED CACHE STRATEGY 
const TTL_MAP = {
    'fundamentals': 15 * 24 * 60 * 60 * 1000,    // 15 Days (Core corporate data)
    'peers': 15 * 24 * 60 * 60 * 1000,           // 15 Days (Competitors don't change often)
    'smart_money_13f': 15 * 24 * 60 * 60 * 1000, // 15 Days (Quarterly institutional holdings)
    'smart_money_smi': 24 * 60 * 60 * 1000,      // 24 Hours (End of day Smart Money Index)
    'smart_money_options': 5 * 60 * 1000,        // 5 Minutes (Real-time whales/block trades)
    'sentiment': 4 * 60 * 60 * 1000,             // 4 Hours (News cycles)
    'earnings_nlp': 24 * 60 * 60 * 1000,         // 24 Hours (Earnings calls)
    'peer_history': 12 * 60 * 60 * 1000,         // 12 Hours
    'heatmap': 1 * 60 * 60 * 1000                // 1 Hour
};

async function getCachedFeature(featureType, folder, scriptName, argsArray) {
    const cacheKey = `${featureType}_${argsArray.join('_')}`;
    const ttl = TTL_MAP[featureType] || (4 * 60 * 60 * 1000); 

    if (featureCache[cacheKey] && (Date.now() - featureCache[cacheKey].timestamp < ttl)) {
        return featureCache[cacheKey].data;
    }

    const data = await fetchPythonData(folder, scriptName, argsArray);
    if (!data.error) {
        featureCache[cacheKey] = { data: data, timestamp: Date.now() };
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

// 🎯 UPDATED SMART MONEY ROUTE
app.get('/api/smart-money', async (req, res) => {
    const ticker = req.query.ticker;
    const type = req.query.type || 'smi'; // Defaults to Smart Money Index if no type is passed
    
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    // Determine the correct cache key based on the type of smart money data
    let cacheFeatureType = 'smart_money_smi';
    if (type === '13f') cacheFeatureType = 'smart_money_13f';
    if (type === 'options') cacheFeatureType = 'smart_money_options';

    // Note: We now pass the 'type' to the python script so it knows what to scrape/calculate
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

app.get('/api/peer-history', async (req, res) => {
    if (!req.query.ticker) return res.status(400).json({ error: "Ticker required" });
    res.json(await getCachedFeature('peer_history', 'ml_models', 'ml_engine.py', ['peers', req.query.ticker]));
});

app.get('/api/heatmap-data', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const data = await fetchPythonData('macro_quant', 'macro_engine.py', ['heatmap', country]);
    res.json(data);
});
const searchCache = {}; 
const SEARCH_CACHE_TTL = 60 * 60 * 1000; 

app.get('/api/search-suggest', async (req, res) => {
    const query = req.query.q?.toLowerCase();
    if (!query) return res.json([]);

    if (searchCache[query] && (Date.now() - searchCache[query].timestamp < SEARCH_CACHE_TTL)) return res.json(searchCache[query].data);

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
        
        searchCache[query] = { data: suggestions, timestamp: Date.now() };
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

        // Create a stream to write to
        const stream = new SitemapStream({ hostname: 'https://finorapulse.com' });

        // Return a promise that resolves with the XML string
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