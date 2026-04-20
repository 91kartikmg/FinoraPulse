const express = require('express');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs').promises; 
const fsSync = require('fs'); 
const mongoose = require('mongoose');
const session = require('express-session');
const bcrypt = require('bcryptjs');
const User = require('./models/User');
const Admin = require('./models/Admin'); // <-- Isolated Admin Model
const axios = require('axios');
const cron = require('node-cron');
const { Mutex } = require('async-mutex');
const { LRUCache } = require('lru-cache');

const app = express();

// ==========================================
// 1. CONFIGURATION & ENVIRONMENT
// ==========================================
const MONGO_URI = 'mongodb://127.0.0.1:27017/stockpulse_db';
const SESSION_SECRET = process.env.SESSION_SECRET || 'supersecret_stockpulse_key';
const DATASET_PATH = path.resolve(__dirname, 'datasets');
const CACHE_DIR = path.resolve(__dirname, 'server_cache');

let PYTHON_PATH = process.env.PYTHON_PATH || (process.platform === 'win32' ? 'python' : 'python3');
const serverVenvPath = '/var/www/FinoraPulse/venv/bin/python3';
if (fsSync.existsSync(serverVenvPath)) {
    PYTHON_PATH = serverVenvPath;
    console.log(`🐍 Using Server Python Environment: ${PYTHON_PATH}`);
} else {
    console.log(`🐍 Using Python: ${PYTHON_PATH}`);
}

[ DATASET_PATH, CACHE_DIR ].forEach(dir => {
    if (!fsSync.existsSync(dir)) {
        fsSync.mkdirSync(dir, { recursive: true, mode: 0o755 });
        console.log(`📁 Created ${path.basename(dir)} folder`);
    }
});

// ==========================================
// 2. CACHE SYSTEM
// ==========================================
const memoryCache = new LRUCache({
    max: 500,
    ttl: 60 * 1000, 
    allowStale: false,
    updateAgeOnGet: true
});

const cacheMutexes = new Map();

function sanitizeKey(rawKey) {
    return rawKey.replace(/[^a-z0-9_]/gi, '_').toLowerCase();
}

async function getCache(rawKey, ttlMs) {
    const safeKey = sanitizeKey(rawKey);
    const memData = memoryCache.get(safeKey);
    if (memData !== undefined) return memData;

    const filePath = path.join(CACHE_DIR, `${safeKey}.json`);
    try {
        const stats = await fs.stat(filePath);
        if (Date.now() - stats.mtimeMs < ttlMs) {
            const raw = await fs.readFile(filePath, 'utf-8');
            const data = JSON.parse(raw);
            memoryCache.set(safeKey, data);
            return data;
        } else {
            fs.unlink(filePath).catch(() => {});
        }
    } catch (e) {}
    return null;
}

async function setCache(rawKey, data) {
    const safeKey = sanitizeKey(rawKey);
    const filePath = path.join(CACHE_DIR, `${safeKey}.json`);
    const tmpPath = `${filePath}.tmp`;

    if (!cacheMutexes.has(safeKey)) cacheMutexes.set(safeKey, new Mutex());
    const mutex = cacheMutexes.get(safeKey);

    await mutex.runExclusive(async () => {
        try {
            await fs.writeFile(tmpPath, JSON.stringify(data));
            await fs.rename(tmpPath, filePath);
            memoryCache.set(safeKey, data);
        } catch (err) {
            console.error(`❌ Cache write error for ${safeKey}:`, err.message);
            await fs.unlink(tmpPath).catch(() => {});
            throw err;
        }
    });
}

async function cleanupExpiredCache() {
    const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000; 
    try {
        const files = await fs.readdir(CACHE_DIR);
        const now = Date.now();
        let deletedCount = 0;
        for (const file of files) {
            if (!file.endsWith('.json')) continue;
            const filePath = path.join(CACHE_DIR, file);
            try {
                const stats = await fs.stat(filePath);
                if (now - stats.mtimeMs > MAX_AGE_MS) {
                    await fs.unlink(filePath);
                    deletedCount++;
                }
            } catch (e) {}
        }
        if (deletedCount > 0) console.log(`🧹 Cleaned ${deletedCount} expired cache files`);
    } catch (err) {}
}

cron.schedule('0 4 * * *', cleanupExpiredCache);
cleanupExpiredCache();

// ==========================================
// 3. MIDDLEWARE & AUTHENTICATION
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

// --- GATEKEEPERS ---
const requireLogin = (req, res, next) => {
    if (!req.session.userId) return res.redirect('/auth');
    next();
};

const requireAdmin = (req, res, next) => {
    // Isolated check: Admin session must exist
    if (!req.session.adminId) return res.redirect('/admin/login');
    next();
};

app.use(async (req, res, next) => {
    res.locals.user = null;
    if (req.session.userId) {
        try {
            const user = await User.findById(req.session.userId);
            if (user) res.locals.user = user;
        } catch (e) {}
    }
    next();
});

// ==========================================
// 4. USER AUTH ROUTES
// ==========================================
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
// ADMIN ROUTES (Combined View)
// ==========================================

// 1. Main Admin Route (Handles both Login UI and Dashboard UI)
app.get('/admin', (req, res) => {
    // We pass adminId and error to the EJS file so it knows which UI to render
    res.render('admin', { 
        adminId: req.session.adminId || null, 
        error: null 
    });
});

// 2. Process Admin Login
app.post('/admin/login', async (req, res) => {
    const { email, password } = req.body;
    try {
        const admin = await Admin.findOne({ email: email.toLowerCase() });
        if (!admin) return res.render('admin', { adminId: null, error: "Access Denied: Invalid Credentials" });

        const isMatch = await bcrypt.compare(password, admin.password);
        if (!isMatch) return res.render('admin', { adminId: null, error: "Access Denied: Invalid Credentials" });

        // Success! Set session and reload the page (it will now show the dashboard)
        req.session.adminId = admin._id;
        res.redirect('/admin');
    } catch (err) {
        res.render('admin', { adminId: null, error: "System Error. Try again." });
    }
});

// 3. Admin Logout
app.get('/admin/logout', (req, res) => {
    req.session.adminId = null;
    res.redirect('/admin'); // Redirecting back to /admin will automatically show the login form
});


// 4. Admin API (Protected by manual check)
app.get('/api/admin/analytics', async (req, res) => {
    if (!req.session.adminId) return res.status(403).json({ error: "Unauthorized" });

    try {
        const totalUsers = await User.countDocuments();
        
        // Fetch Page Views
        const viewsDoc = await mongoose.connection.db.collection('site_analytics').findOne({ metric: 'total_views' });
        const totalViews = viewsDoc ? viewsDoc.count : 0;

        // 👇 NEW: Fetch API Requests 👇
        const apiDoc = await mongoose.connection.db.collection('site_analytics').findOne({ metric: 'total_api_requests' });
        const totalApiRequests = apiDoc ? apiDoc.count : 0;

        // Fetch the last 7 days of traffic for the chart
        const past7Days = [...Array(7)].map((_, i) => {
            const d = new Date();
            d.setDate(d.getDate() - i);
            return d.toISOString().split('T')[0];
        }).reverse();

        const dailyStats = await mongoose.connection.db.collection('site_analytics')
            .find({ metric: 'daily_views', date: { $in: past7Days } })
            .toArray();

        const chartData = past7Days.map(date => {
            const stat = dailyStats.find(s => s.date === date);
            return stat ? stat.count : 0;
        });

        const chartLabels = past7Days.map(date => new Date(date).toLocaleDateString('en-US', { weekday: 'short' }));

        res.json({
            success: true,
            totalSignups: totalUsers,
            totalPageViews: totalViews,
            totalApiRequests: totalApiRequests, // <-- Added here
            serverUptime: Math.floor(process.uptime() / 3600) + " Hours",
            chartData: { labels: chartLabels, data: chartData }
        });
    } catch (error) {
        res.status(500).json({ success: false, error: "Analytics fetch failed" });
    }
});

// --- TRAFFIC TRACKING MIDDLEWARE ---
app.use(async (req, res, next) => {
    // Only track GET requests (ignore CSS, JS, or images)
    if (req.method === 'GET' && !req.url.includes('.')) {
        try {
            const today = new Date().toISOString().split('T')[0]; 
            
            // IF it's a normal page visit (not an API call)
            if (!req.url.startsWith('/api')) {
                await mongoose.connection.db.collection('site_analytics').updateOne(
                    { metric: 'total_views' }, { $inc: { count: 1 } }, { upsert: true }
                );
                await mongoose.connection.db.collection('site_analytics').updateOne(
                    { metric: 'daily_views', date: today }, { $inc: { count: 1 } }, { upsert: true }
                );
            } 
            // IF it IS an API call (but ignore the admin dashboard polling itself)
            else if (req.url.startsWith('/api') && !req.url.includes('/admin/analytics')) {
                await mongoose.connection.db.collection('site_analytics').updateOne(
                    { metric: 'total_api_requests' }, { $inc: { count: 1 } }, { upsert: true }
                );
            }
        } catch (e) {
            console.error("Tracking Error:", e.message);
        }
    }
    next();
});
// ==========================================
// 6. FRONTEND PAGE ROUTES
// ==========================================
app.get('/', (req, res) => res.render('home'));
app.get('/predict', requireLogin, (req, res) => res.render('predict', { ticker: (req.query.ticker || 'RELIANCE.NS').toUpperCase() }));
app.get('/macro', requireLogin, (req, res) => res.render('macro', { country: req.query.country || 'IN' }));
app.get('/heatmap', requireLogin, (req, res) => res.render('heatmap', { country: (req.query.country || 'US').toUpperCase() }));
app.get('/calculators', (req, res) => res.render('calculator'));


// ==========================================
// 8. PYTHON EXECUTION HELPER
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
// 9. CACHE TTL & APIS
// ==========================================
const TTL = {
    PREDICT: 12 * 60 * 60 * 1000,
    MACRO: 12 * 60 * 60 * 1000,
    FUNDAMENTALS: 15 * 24 * 60 * 60 * 1000,
    PEERS: 15 * 24 * 60 * 60 * 1000,
    SMART_MONEY_13F: 15 * 24 * 60 * 60 * 1000,
    SMART_MONEY_SMI: 24 * 60 * 60 * 1000,
    SMART_MONEY_OPTIONS: 5 * 60 * 1000,
    SENTIMENT: 4 * 60 * 60 * 1000,
    EARNINGS_NLP: 24 * 60 * 60 * 1000,
    HEATMAP: 1 * 60 * 60 * 1000,
    SEARCH: 30 * 60 * 1000,
    CORRELATION: 12 * 60 * 60 * 1000
};

async function cachedFetch(cacheKey, ttlMs, fetchFn) {
    const cached = await getCache(cacheKey, ttlMs);
    if (cached !== null) {
        console.log(`⚡ Cache HIT: ${cacheKey}`);
        return cached;
    }
    console.log(`🔄 Cache MISS: ${cacheKey}, fetching fresh...`);
    const data = await fetchFn();
    if (data && !data.error) {
        await setCache(cacheKey, data);
    }
    return data;
}

app.get('/api/stats', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d';
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const todayDate = new Date().toISOString().split('T')[0];
    const cacheKey = `predict_${ticker}_${timeframe}_${todayDate}`;
    const result = await cachedFetch(cacheKey, TTL.PREDICT, () =>
        fetchPythonData('ml_models', 'ml_engine.py', ['predict', ticker, timeframe, DATASET_PATH])
    );
    res.json(result);
});

app.get('/api/macro-explorer', async (req, res) => {
    const country = (req.query.country || 'IN').toUpperCase();
    const result = await cachedFetch(`macro_${country}`, TTL.MACRO, () =>
        fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country])
    );
    res.json(result);
});

app.get('/api/global-liquidity', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const result = await cachedFetch(`liquidity_${country}`, TTL.MACRO, () =>
        fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country])
    );
    res.json(result);
});

app.get('/api/correlation', async (req, res) => {
    const result = await cachedFetch('macro_correlation', TTL.CORRELATION, () =>
        fetchPythonData('macro_quant', 'macro_engine.py', ['correlation'])
    );
    res.json(result);
});

app.get('/api/fundamentals', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const result = await cachedFetch(`feature_fundamentals_${ticker}`, TTL.FUNDAMENTALS, () =>
        fetchPythonData('fundamentals', 'fundamentals_engine.py', ['fundamentals', ticker])
    );
    res.json(result);
});

app.get('/api/peers', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const result = await cachedFetch(`feature_peers_${ticker}`, TTL.PEERS, () =>
        fetchPythonData('fundamentals', 'fundamentals_engine.py', ['peers', ticker])
    );
    res.json(result);
});

app.get('/api/smart-money', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const type = req.query.type || 'smi';
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    let cacheKey, ttlMs;
    if (type === '13f') { cacheKey = `feature_smart_money_13f_${ticker}`; ttlMs = TTL.SMART_MONEY_13F; }
    else if (type === 'options') { cacheKey = `feature_smart_money_options_${ticker}`; ttlMs = TTL.SMART_MONEY_OPTIONS; }
    else { cacheKey = `feature_smart_money_smi_${ticker}`; ttlMs = TTL.SMART_MONEY_SMI; }

    const result = await cachedFetch(cacheKey, ttlMs, () =>
        fetchPythonData('fundamentals', 'fundamentals_engine.py', ['smart_money', ticker, type])
    );
    res.json(result);
});

app.get('/api/sentiment', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const result = await cachedFetch(`feature_sentiment_${ticker}`, TTL.SENTIMENT, () =>
        fetchPythonData('ml_models', 'ml_engine.py', ['sentiment', ticker])
    );
    res.json(result);
});

app.get('/api/earnings-nlp', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    const result = await cachedFetch(`feature_earnings_nlp_${ticker}`, TTL.EARNINGS_NLP, () =>
        fetchPythonData('ml_models', 'ml_engine.py', ['earnings', ticker])
    );
    res.json(result);
});

app.get('/api/heatmap-data', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const result = await cachedFetch(`heatmap_${country}`, TTL.HEATMAP, () =>
        fetchPythonData('macro_quant', 'macro_engine.py', ['heatmap', country])
    );
    res.json(result);
});

function formatType(type) {
    const types = {
        'EQUITY': 'Stock', 'CRYPTO': 'Crypto', 'ETF': 'ETF', 'INDEX': 'Index',
        'CURRENCY': 'Forex', 'MUTUALFUND': 'Fund', 'FUTURE': 'Commodity'
    };
    return types[type] || type;
}

app.get('/api/search-suggest', async (req, res) => {
    const query = req.query.q?.toLowerCase();
    if (!query) return res.json([]);

    const result = await cachedFetch(`search_${query}`, TTL.SEARCH, async () => {
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

            if (query.includes('gold rate')) {
                suggestions.unshift({
                    symbol: 'GC=F', name: 'Spot Gold Rate', region: '🇺🇸 Global/USA', type: 'Rate', exchange: 'COMEX'
                });
            }
            return suggestions;
        } catch (err) {
            return [];
        }
    });
    res.json(result || []);
});

// ==========================================
// 10. MACRO BATCH PRE-WARMER
// ==========================================
const SUPPORTED_COUNTRIES = [
    "US", "CN", "DE", "JP", "IN", "GB", "FR", "IT", "BR", "CA",
    "KR", "AU", "MX", "ES", "ID", "NL", "SA", "CH", "TW", "PL",
    "SE", "BE", "SG", "HK", "ZA"
];

async function runMacroBatchUpdate() {
    console.log("🌎 [MACRO BATCH] Starting Global Economic Sync...");
    try {
        const corrData = await fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']);
        if (!corrData.error) await setCache('macro_correlation', corrData);
    } catch (e) {}

    for (const country of SUPPORTED_COUNTRIES) {
        try {
            const macroData = await fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]);
            if (!macroData.error) {
                await setCache(`macro_${country}`, macroData);
                console.log(`✅ Cached Macro: ${country}`);
            }
            const liquidityData = await fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]);
            if (!liquidityData.error) await setCache(`liquidity_${country}`, liquidityData);

            await new Promise(resolve => setTimeout(resolve, 5000));
        } catch (err) {}
    }
    console.log("🏁 [MACRO BATCH] Sync Complete!");
}

cron.schedule('0 3 * * 0', runMacroBatchUpdate);
runMacroBatchUpdate();

// ==========================================
// 11. SITEMAP GENERATION
// ==========================================
const { SitemapStream, streamToPromise } = require('sitemap');
const { Readable } = require('stream');

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
        const xmlString = await streamToPromise(Readable.from(links).pipe(stream)).then(data => data.toString());
        res.header('Content-Type', 'application/xml');
        res.send(xmlString);
    } catch (e) {
        res.status(500).end();
    }
});

// 🚨 TEMPORARY SETUP ROUTE: DELETE AFTER RUNNING ONCE 🚨
// 🚨 TEMPORARY SETUP ROUTE: DELETE AFTER RUNNING ONCE 🚨
app.get('/setup-admin', async (req, res) => {
    try {
        const hashedPassword = await bcrypt.hash('91kartikmg@KKK', 10);
        const newAdmin = new Admin({
            email: 'kartikgowda94@gmail.com', // Replace with your desired admin email
            password: hashedPassword
        });
        await newAdmin.save();
        res.send("Admin created successfully! Now delete this route from server.js.");
    } catch (err) {
        res.send("Error or admin already exists.");
    }
});

// ==========================================
// 12. START SERVER
// ==========================================
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 FinoraPulse Live at: http://localhost:${PORT}`));