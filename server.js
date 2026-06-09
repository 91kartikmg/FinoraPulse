require('dotenv').config();
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
const Suggestion = require('./models/Suggestion');
const crypto = require('crypto');
const { sendOTPEmail } = require('./utils/mailer');
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

[DATASET_PATH, CACHE_DIR].forEach(dir => {
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
            fs.unlink(filePath).catch(() => { });
        }
    } catch (e) { }
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
            await fs.unlink(tmpPath).catch(() => { });
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
            } catch (e) { }
        }
        if (deletedCount > 0) console.log(`🧹 Cleaned ${deletedCount} expired cache files`);
    } catch (err) { }
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
        } catch (e) { }
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

app.post('/auth/forgot-password', async (req, res) => {
    const { loginInput } = req.body;
    if (!loginInput) {
        return res.status(400).json({ success: false, error: "User ID or Gmail is required." });
    }

    try {
        // Query by either username or email
        const user = await User.findOne({ 
            $or: [
                { email: loginInput.toLowerCase() }, 
                { username: loginInput }
            ] 
        });

        if (!user) {
            return res.status(404).json({ success: false, error: "No account found with this User ID or Gmail." });
        }

        // Generate 6-digit numeric OTP
        const otp = crypto.randomInt(100000, 999999).toString();
        
        // Save to DB with 10-minute expiry
        user.resetPasswordOTP = otp;
        user.resetPasswordOTPExpires = new Date(Date.now() + 10 * 60 * 1000); // 10 minutes
        await user.save();

        // Send email in the background to prevent blocking and ensure instant response times
        sendOTPEmail(user.email, user.username, otp).then(emailSent => {
            if (!emailSent) {
                console.error(`❌ [MAILER FAILURE] Background OTP email failed to send to ${user.email}. Check SMTP credentials.`);
            }
        }).catch(err => {
            console.error(`❌ [MAILER ERROR] Error sending background OTP email to ${user.email}:`, err.message);
        });

        // Mask the email for privacy (e.g. k***a94@gmail.com)
        const emailParts = user.email.split('@');
        const mainPart = emailParts[0];
        const domainPart = emailParts[1];
        const maskedMain = mainPart.length > 3 
            ? mainPart.substring(0, 2) + '*'.repeat(mainPart.length - 3) + mainPart.slice(-1)
            : mainPart.substring(0, 1) + '*'.repeat(mainPart.length - 1);
        const maskedEmail = `${maskedMain}@${domainPart}`;

        res.json({ 
            success: true, 
            message: `Verification OTP has been sent to your registered email: ${maskedEmail}` 
        });
    } catch (err) {
        console.error("Forgot password route error:", err);
        res.status(500).json({ success: false, error: "Internal server error. Please try again." });
    }
});

app.post('/auth/verify-otp', async (req, res) => {
    const { loginInput, otp } = req.body;

    if (!loginInput || !otp) {
        return res.status(400).json({ success: false, error: "All fields are required." });
    }

    try {
        const user = await User.findOne({ 
            $or: [
                { email: loginInput.toLowerCase() },
                { username: loginInput }
            ],
            resetPasswordOTP: otp,
            resetPasswordOTPExpires: { $gt: new Date() }
        });

        if (!user) {
            return res.status(400).json({ success: false, error: "Invalid or expired OTP." });
        }

        res.json({ success: true, message: "OTP verified successfully. Please set your new password." });
    } catch (err) {
        console.error("Verify OTP route error:", err);
        res.status(500).json({ success: false, error: "Internal server error. Please try again." });
    }
});

app.post('/auth/reset-password', async (req, res) => {
    const { loginInput, otp, password, confirmPassword } = req.body;

    if (!loginInput || !otp || !password || !confirmPassword) {
        return res.status(400).json({ success: false, error: "All fields are required." });
    }

    if (password !== confirmPassword) {
        return res.status(400).json({ success: false, error: "Passwords do not match." });
    }

    try {
        const user = await User.findOne({ 
            $or: [
                { email: loginInput.toLowerCase() },
                { username: loginInput }
            ],
            resetPasswordOTP: otp,
            resetPasswordOTPExpires: { $gt: new Date() }
        });

        if (!user) {
            return res.status(400).json({ success: false, error: "Invalid or expired OTP." });
        }

        // Update password
        const hashedPassword = await bcrypt.hash(password, 10);
        user.password = hashedPassword;
        user.resetPasswordOTP = null;
        user.resetPasswordOTPExpires = null;
        await user.save();

        res.json({ success: true, message: "Your password has been successfully reset! You can now log in." });
    } catch (err) {
        console.error("Reset password route error:", err);
        res.status(500).json({ success: false, error: "Internal server error. Please try again." });
    }
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
// 2. Process Admin Login
app.post('/admin/login', async (req, res) => {
    const { email, password } = req.body;
    try {
        const admin = await Admin.findOne({ email: email.toLowerCase() });
        if (!admin) return res.render('admin', { adminId: null, error: "Access Denied: Invalid Credentials" });

        const isMatch = await bcrypt.compare(password, admin.password);
        if (!isMatch) return res.render('admin', { adminId: null, error: "Access Denied: Invalid Credentials" });

        // CRITICAL FIX: Force the session to save before redirecting
        req.session.adminId = admin._id;
        req.session.save((err) => {
            if (err) console.error("Session save error:", err);
            res.redirect('/admin');
        });
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

        // Fetch API Requests
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

        // Node process statistics
        const memoryUsage = (process.memoryUsage().rss / 1024 / 1024).toFixed(1) + " MB";

        // Count cached items in server_cache directory
        let cacheCount = 0;
        try {
            const files = await fs.readdir(CACHE_DIR);
            cacheCount = files.filter(f => f.endsWith('.json')).length;
        } catch (err) {}

        res.json({
            success: true,
            totalSignups: totalUsers,
            totalPageViews: totalViews,
            totalApiRequests: totalApiRequests,
            serverUptime: Math.floor(process.uptime() / 3600) + " Hours",
            memoryUsage: memoryUsage,
            cacheCount: cacheCount + " Files",
            chartData: { labels: chartLabels, data: chartData }
        });
    } catch (error) {
        res.status(500).json({ success: false, error: "Analytics fetch failed" });
    }
});

// Admin Cache Management API
app.post('/api/admin/clear-cache', async (req, res) => {
    if (!req.session.adminId) return res.status(403).json({ error: "Unauthorized" });
    try {
        const files = await fs.readdir(CACHE_DIR);
        let cleared = 0;
        for (const file of files) {
            if (file.endsWith('.json')) {
                await fs.unlink(path.join(CACHE_DIR, file));
                cleared++;
            }
        }
        memoryCache.clear();
        res.json({ success: true, message: `Successfully cleared ${cleared} cache files and flushed RAM buffer.` });
    } catch (err) {
        res.status(500).json({ success: false, error: "Failed to clear server cache" });
    }
});

// Admin Pre-Warm API
app.post('/api/admin/prewarm', async (req, res) => {
    if (!req.session.adminId) return res.status(403).json({ error: "Unauthorized" });
    runMacroBatchUpdate().catch(err => console.error("Batch update error:", err));
    res.json({ success: true, message: "Macro pre-warming background task successfully triggered." });
});

// Admin Suggestions Deletion API
app.delete('/api/admin/suggestions/:id', async (req, res) => {
    if (!req.session.adminId) return res.status(403).json({ error: "Unauthorized" });
    try {
        await Suggestion.findByIdAndDelete(req.params.id);
        res.json({ success: true });
    } catch (err) {
        res.status(500).json({ success: false, error: "Failed to delete suggestion" });
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
// 5. SUGGESTION ROUTES
// ==========================================
app.post('/api/suggestion', async (req, res) => {
    try {
        const { text } = req.body;
        if (!text || text.trim() === '') return res.status(400).json({ error: "Suggestion text is required" });

        let username = 'Anonymous';
        let userId = null;
        if (req.session.userId) {
            const user = await User.findById(req.session.userId);
            if (user) {
                username = user.username;
                userId = user._id;
            }
        }

        const newSuggestion = new Suggestion({ text, username, userId });
        await newSuggestion.save();

        res.json({ success: true, message: "Suggestion submitted successfully!" });
    } catch (err) {
        console.error("Suggestion Error:", err);
        res.status(500).json({ success: false, error: "Failed to submit suggestion" });
    }
});

app.get('/api/admin/suggestions', async (req, res) => {
    if (!req.session.adminId) return res.status(403).json({ error: "Unauthorized" });
    try {
        const suggestions = await Suggestion.find().sort({ createdAt: -1 }).limit(50);
        res.json({ success: true, suggestions });
    } catch (err) {
        console.error("Admin Suggestions Error:", err);
        res.status(500).json({ success: false, error: "Failed to fetch suggestions" });
    }
});

// ==========================================
// 6. FRONTEND PAGE ROUTES
// ==========================================
app.get('/', (req, res) => res.render('home'));
app.get('/predict', requireLogin, (req, res) => res.render('predict', { ticker: (req.query.ticker || 'RELIANCE.NS').toUpperCase() }));
app.get('/technical', requireLogin, async (req, res) => {
    const ticker = (req.query.ticker || 'RELIANCE.NS').toUpperCase();
    const timeframe = req.query.timeframe || '1d';
    const cacheKey = `technical_html_${ticker}_${timeframe}`;

    try {
        const cachedHTML = await getCache(cacheKey, TTL.TECHNICAL);
        if (cachedHTML) {
            console.log(`⚡ Technical HTML Cache HIT: ${cacheKey}`);
            const customizedHTML = cachedHTML.replace('___USER_NAME_PLACEHOLDER___', res.locals.user.username);
            return res.send(customizedHTML);
        }

        console.log(`🔄 Technical HTML Cache MISS: ${cacheKey}, rendering fresh...`);
        const placeholderUser = { username: '___USER_NAME_PLACEHOLDER___' };

        res.render('technical', {
            ticker,
            timeframe,
            user: placeholderUser
        }, async (err, html) => {
            if (err) {
                console.error("Technical EJS render error:", err);
                return res.status(500).send("Render error");
            }
            await setCache(cacheKey, html);
            const customizedHTML = html.replace('___USER_NAME_PLACEHOLDER___', res.locals.user.username);
            res.send(customizedHTML);
        });
    } catch (e) {
        console.error("Technical page cache handler error:", e);
        res.render('technical', { ticker, timeframe });
    }
});
app.get('/macro', requireLogin, (req, res) => res.render('macro', { country: req.query.country || 'IN' }));
app.get('/heatmap', requireLogin, (req, res) => res.render('heatmap', { country: (req.query.country || 'US').toUpperCase() }));
app.get('/calculator', (req, res) => res.render('calculator'));
app.get('/privacy-policy', (req, res) => res.render('privacy_policy'));


// ==========================================
// 8. PYTHON EXECUTION HELPER
// ==========================================
function fetchPythonData(folder, scriptName, argsArray = []) {
    return new Promise((resolve) => {
        const scriptPath = path.resolve(__dirname, 'python_engine', folder, scriptName);
        const args = [scriptPath, ...argsArray];

        console.log(`🚀 Executing: ${PYTHON_PATH} ${args.join(' ')}`);

        const pythonProcess = spawn(PYTHON_PATH, args, {
            env: {
                ...process.env,
                PYTHONDONTWRITEBYTECODE: '1'
            }
        });
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
    HEATMAP: 1 * 60 * 60 * 1000,
    SEARCH: 30 * 60 * 1000,
    CORRELATION: 12 * 60 * 60 * 1000,
    TECHNICAL: 4 * 60 * 60 * 1000,
    SENTIMENT: 1 * 60 * 60 * 1000
};

async function cachedFetch(cacheKey, ttlMs, fetchFn) {
    const cached = await getCache(cacheKey, ttlMs);
    if (cached !== null) {
        console.log(`⚡ Cache HIT: ${cacheKey}`);
        return cached;
    }

    // 5-minute cooldown for transient errors (rate-limiting, timeout, etc.)
    const errorKey = `err_${cacheKey}`;
    const cachedError = await getCache(errorKey, 5 * 60 * 1000);
    if (cachedError !== null) {
        console.log(`⚡ Cache HIT (Error Cooldown): ${cacheKey}`);
        return cachedError;
    }

    console.log(`🔄 Cache MISS: ${cacheKey}, fetching fresh...`);
    const data = await fetchFn();
    if (data) {
        if (!data.error) {
            await setCache(cacheKey, data);
        } else {
            // Cache error temporarily to prevent DDOS loops under rate limits
            await setCache(errorKey, data);
        }
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



app.get('/api/heatmap-data', async (req, res) => {
    const country = (req.query.country || 'US').toUpperCase();
    const result = await cachedFetch(`heatmap_${country}`, TTL.HEATMAP, () =>
        fetchPythonData('macro_quant', 'macro_engine.py', ['heatmap', country])
    );
    res.json(result);
});

app.get('/api/sentiment', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) return res.status(400).json({ error: "Missing ticker query parameter." });

    try {
        const result = await cachedFetch(`sentiment_${ticker}`, TTL.SENTIMENT, () =>
            fetchPythonData('fundamentals', 'news_engine.py', [ticker])
        );
        res.json(result);
    } catch (err) {
        console.error(`Error in /api/sentiment for ${ticker}:`, err);
        res.status(500).json({ error: "Failed to fetch news sentiment analysis." });
    }
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

async function runMacroBatchUpdate(force = false) {
    console.log(`🌎 [MACRO BATCH] Starting Global Economic Sync (Force Update: ${force})...`);
    try {
        const hasCorr = await getCache('macro_correlation', TTL.CORRELATION);
        if (force || hasCorr === null) {
            const corrData = await fetchPythonData('macro_quant', 'macro_engine.py', ['correlation']);
            if (!corrData.error) await setCache('macro_correlation', corrData);
        }
    } catch (e) { }

    for (const country of SUPPORTED_COUNTRIES) {
        try {
            const hasMacro = await getCache(`macro_${country}`, TTL.MACRO);
            if (force || hasMacro === null) {
                const macroData = await fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country]);
                if (!macroData.error) {
                    await setCache(`macro_${country}`, macroData);
                    console.log(`✅ Cached Macro: ${country}`);
                }
            } else {
                console.log(`⚡ Warm Cache for Macro: ${country} (Skipping Python spawn)`);
            }

            const hasLiquidity = await getCache(`liquidity_${country}`, TTL.MACRO);
            if (force || hasLiquidity === null) {
                const liquidityData = await fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country]);
                if (!liquidityData.error) await setCache(`liquidity_${country}`, liquidityData);
            }

            if (force || hasMacro === null || hasLiquidity === null) {
                // Yield to event loop and delay slightly between active Python syncs
                await new Promise(resolve => setTimeout(resolve, 5000));
            }
        } catch (err) { }
    }
    console.log("🏁 [MACRO BATCH] Sync Complete!");
}

cron.schedule('0 3 * * 0', () => runMacroBatchUpdate(true));
runMacroBatchUpdate(false);

// ==========================================
// 11. SITEMAP GENERATION
// ==========================================
const { SitemapStream, streamToPromise } = require('sitemap');
const { Readable } = require('stream');

app.get('/sitemap.xml', async (req, res) => {
    try {
        const links = [
            { url: '/', changefreq: 'daily', priority: 1.0 },
            { url: '/calculator', changefreq: 'weekly', priority: 0.9 },
            { url: '/auth', changefreq: 'monthly', priority: 0.5 },
        ];

        // 1. Dynamically generate links for each supported country
        SUPPORTED_COUNTRIES.forEach(country => {
            links.push({ url: `/macro?country=${country}`, changefreq: 'weekly', priority: 0.7 });
            links.push({ url: `/heatmap?country=${country}`, changefreq: 'weekly', priority: 0.7 });
        });

        // 2. Dynamically scan datasets directory to extract active tickers
        try {
            const files = await fs.readdir(DATASET_PATH);
            const tickers = new Set();
            for (const file of files) {
                if (file.startsWith('data_') && file.endsWith('.csv')) {
                    const parts = file.split('_');
                    if (parts.length >= 2) {
                        tickers.add(parts[1].toUpperCase());
                    }
                }
            }

            // Always guarantee default active ticker fallbacks
            if (tickers.size === 0) {
                tickers.add('RELIANCE.NS');
                tickers.add('AAPL');
            }

            // Add dynamic routes for each found ticker
            tickers.forEach(ticker => {
                links.push({ url: `/predict?ticker=${ticker}`, changefreq: 'daily', priority: 0.8 });
                links.push({ url: `/technical?ticker=${ticker}`, changefreq: 'daily', priority: 0.8 });
                links.push({ url: `/chart-view?ticker=${ticker}`, changefreq: 'weekly', priority: 0.6 });
                links.push({ url: `/pattern-test?ticker=${ticker}`, changefreq: 'monthly', priority: 0.4 });
            });
        } catch (err) {
            console.error("Sitemap ticker scanning error:", err);
            // Stable hardcoded fallback in case of folder read error
            const fallbackTickers = ['RELIANCE.NS', 'AAPL', 'MSFT', 'NVDA', 'TCS.NS', 'SBIN.NS', 'HDFCBANK.NS'];
            fallbackTickers.forEach(ticker => {
                links.push({ url: `/predict?ticker=${ticker}`, changefreq: 'daily', priority: 0.8 });
                links.push({ url: `/technical?ticker=${ticker}`, changefreq: 'daily', priority: 0.8 });
                links.push({ url: `/chart-view?ticker=${ticker}`, changefreq: 'weekly', priority: 0.6 });
                links.push({ url: `/pattern-test?ticker=${ticker}`, changefreq: 'monthly', priority: 0.4 });
            });
        }

        const stream = new SitemapStream({ hostname: 'https://finorapulse.com' });
        const xmlString = await streamToPromise(Readable.from(links).pipe(stream)).then(data => data.toString());
        res.header('Content-Type', 'application/xml');
        res.send(xmlString);
    } catch (e) {
        res.status(500).end();
    }
});

// Add this inside the TTL object definition near the top:
// STRATEGY: 4 * 60 * 60 * 1000

app.get('/api/strategy/overload', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d';

    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const cacheKey = `strategy_overload_${ticker}_${timeframe}`;

    // Uses 4-hour cache TTL (14400000 ms) to save server resources
    const result = await cachedFetch(cacheKey, 14400000, () =>
        fetchPythonData('ml_models', 'technical_engine.py', [ticker, timeframe, DATASET_PATH])
    );

    res.json(result);
});

// Add to your server.js API section
app.get('/api/strategy/trend', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d';
    const type = req.query.type || 'sma'; // 'sma', 'macd', or 'psar'

    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const cacheKey = `strategy_trend_${type}_${ticker}_${timeframe}`;

    // Using 4-hour cache (14400000 ms)
    const result = await cachedFetch(cacheKey, 14400000, () =>
        fetchPythonData('ml_models', 'trend_engine.py', [ticker, timeframe, DATASET_PATH, type])
    );

    res.json(result);
}); 

app.get('/api/strategy/momentum', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d';
    const type = req.query.type || 'bollinger'; // 'bollinger', 'rsi_div', or 'stochastic'

    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const cacheKey = `strategy_momentum_${type}_${ticker}_${timeframe}`;

    // Using 4-hour cache (14400000 ms)
    const result = await cachedFetch(cacheKey, 14400000, () =>
        fetchPythonData('ml_models', 'momentum_engine.py', [ticker, timeframe, DATASET_PATH, type])
    );

    res.json(result);
});

app.get('/api/strategy/volatility', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    const timeframe = req.query.timeframe || '1d';
    const type = req.query.type || 'donchian'; // 'donchian', 'squeeze', or 'vol_breakout'

    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const cacheKey = `strategy_volatility_${type}_${ticker}_${timeframe}`;

    // Using 4-hour cache (14400000 ms)
    const result = await cachedFetch(cacheKey, 14400000, () =>
        fetchPythonData('ml_models', 'volatility_engine.py', [ticker, timeframe, DATASET_PATH, type])
    );

    res.json(result);
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

app.get('/pattern-test', requireLogin, (req, res) => {
    res.render('pattern_test', { ticker: (req.query.ticker || 'RELIANCE.NS').toUpperCase() });
});

app.get('/api/patterns', async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase() || 'RELIANCE.NS';
    const cacheKey = `patterns_${ticker}`;
    const result = await cachedFetch(cacheKey, TTL.TECHNICAL, () =>
        fetchPythonData('ml_models', 'pattern_engine.py', [ticker])
    );
    res.json(result);
});

// --- CANDLESTICK CHART API ---
app.get('/api/chart-svg', async (req, res) => {
    const ticker = req.query.ticker || 'RELIANCE.NS';
    const timeframe = req.query.timeframe || '1d'; // <-- ADD THIS

    // Pass timeframe to Python and cache using Technical TTL (4 hours)
    const result = await cachedFetch(`svg_chart_${ticker}_${timeframe}`, TTL.TECHNICAL, () =>
        fetchPythonData('ml_models', 'ohlc_engine.py', [ticker, DATASET_PATH, timeframe])
    );
    res.json(result);
});

// --- PAGE ROUTE ---
app.get('/chart-view', requireLogin, (req, res) => {
    res.render('chart_view', { ticker: (req.query.ticker || 'RELIANCE.NS').toUpperCase() });
});

// --- FINORA AI TERMINAL CHATBOT ---
app.post('/api/chat', requireLogin, async (req, res) => {
    const { message, contextTicker, contextTimeframe } = req.body;
    if (!message || message.trim() === '') {
        return res.status(400).json({ error: "Message is required" });
    }

    try {
        // 1. Parse ticker or country
        let ticker = extractTicker(message, contextTicker);
        let country = extractCountry(message);
        
        let contextData = { type: 'general', ticker: ticker, country: country };

        // Helper regex matching
        function extractTicker(msg, ctxTicker) {
            const matches = msg.match(/\b([A-Z]{2,10}(?:\.[A-Z]{2,4})?|-USD|=X)\b/g);
            if (matches && matches.length > 0) {
                return matches[0];
            }
            const commonTickers = {
                'reliance': 'RELIANCE.NS', 'tcs': 'TCS.NS', 'hdfc': 'HDFCBANK.NS', 
                'infy': 'INFY.NS', 'sbin': 'SBIN.NS', 'aapl': 'AAPL', 
                'msft': 'MSFT', 'nvda': 'NVDA', 'amzn': 'AMZN', 
                'meta': 'META', 'googl': 'GOOGL', 'tsla': 'TSLA',
                'bitcoin': 'BTC-USD', 'ethereum': 'ETH-USD'
            };
            const lowerMsg = msg.toLowerCase();
            for (const key in commonTickers) {
                if (lowerMsg.includes(key)) {
                    return commonTickers[key];
                }
            }
            return ctxTicker || null;
        }

        function extractCountry(msg) {
            const countryMap = {
                "india": "IN", "china": "CN", "japan": "JP", "germany": "DE", 
                "uk": "GB", "united kingdom": "GB", "canada": "CA", "australia": "AU", 
                "brazil": "BR", "mexico": "MX", "france": "FR", "italy": "IT", "usa": "US", "us": "US"
            };
            const lowerMsg = msg.toLowerCase();
            for (const key in countryMap) {
                if (lowerMsg.includes(key)) {
                    return countryMap[key];
                }
            }
            return null;
        }

        const tf = contextTimeframe || '1d';

        // 2. Fetch context data
        if (ticker) {
            const chartData = await cachedFetch(`svg_chart_${ticker}_${tf}`, TTL.TECHNICAL, () =>
                fetchPythonData('ml_models', 'ohlc_engine.py', [ticker, DATASET_PATH, tf])
            );
            if (chartData && !chartData.error) {
                contextData = {
                    type: 'technical',
                    ticker,
                    timeframe: tf,
                    data: chartData
                };
            }
        } else if (country) {
            const macroResult = await cachedFetch(`macro_${country}`, TTL.MACRO, () =>
                fetchPythonData('macro_quant', 'macro_engine.py', ['macro', country])
            );
            const liqResult = await cachedFetch(`liquidity_${country}`, TTL.MACRO, () =>
                fetchPythonData('macro_quant', 'macro_engine.py', ['liquidity', country])
            );

            if (macroResult && !macroResult.error) {
                const getLatest = (arr) => {
                    if (!arr || arr.length === 0) return 0;
                    for (let i = arr.length - 1; i >= 0; i--) {
                        if (arr[i] !== 0 && arr[i] !== null) return arr[i];
                    }
                    return 0;
                };
                contextData = {
                    type: 'macro',
                    country,
                    data: {
                        gdp_growth: getLatest(macroResult.gdp_trend),
                        inflation: getLatest(macroResult.inflation_trend),
                        unemployment: getLatest(macroResult.unemployment_trend),
                        bond_yield: getLatest(macroResult.bond_trend),
                        interest_rate: getLatest(macroResult.interest_rate_trend),
                        debt_to_gdp: getLatest(macroResult.debt_trend),
                        foreign_val: liqResult?.foreign_val || 0,
                        domestic_val: liqResult?.domestic_val || 0,
                        net: liqResult?.net || 0,
                        currency: liqResult?.currency || 'USD',
                        status: liqResult?.status || 'Neutral'
                    }
                };
            }
        }

        // 3. Generate response using Gemini API key if present, otherwise local quant fallback
        const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
        if (GEMINI_API_KEY) {
            try {
                const systemPrompt = `You are Finora AI, a premium quantitative stock analyst & macroeconomic terminal assistant at FinoraPulse.com.
You have access to real-time market data, moving averages, forecasting engines, and macroeconomic indicators.
Your tone is professional, technical, clear, and quantitative.
Here is the context data for the query:
${JSON.stringify(contextData, null, 2)}

Instructions:
- Format your output in clean Markdown with headers, bolding, and bullet points.
- Quote actual numbers, percentages, and prices from the dataset.
- Be concise and answer the user's question directly.
- If the user asks about topics outside finance or macroeconomics, politely guide them back to trading and terminal analytics.`;

                const response = await axios.post(
                    `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${GEMINI_API_KEY}`,
                    {
                        contents: [
                            {
                                role: "user",
                                parts: [{ text: `${systemPrompt}\n\nUser Question: ${message}` }]
                            }
                        ]
                    },
                    { headers: { 'Content-Type': 'application/json' } }
                );

                const aiResponse = response.data?.candidates?.[0]?.content?.parts?.[0]?.text;
                if (aiResponse) {
                    return res.json({ response: aiResponse });
                }
            } catch (apiErr) {
                console.error("Gemini API Error, falling back to local engine:", apiErr.message);
            }
        }

        // Fallback Local Quant Rules Engine
        let responseText = "";
        const fmt = (num) => new Intl.NumberFormat('en-US').format(Math.abs(num));

        if (contextData.type === 'technical' && contextData.data) {
            const data = contextData.data;
            const tName = contextData.ticker;
            const tFrame = contextData.timeframe;
            const histData = data.filter(d => !d.is_future);
            const futData = data.filter(d => d.is_future);

            if (histData.length > 0) {
                const lastHist = histData[histData.length - 1];
                const lastFut = futData.length > 0 ? futData[futData.length - 1] : lastHist;

                const currentPrice = lastHist.close;
                const ema9Hist = lastHist.ema9;
                const ema9Fut = lastFut.ema9;
                const ema21Hist = lastHist.ema21;
                const ema21Fut = lastFut.ema21;
                const sma20Hist = lastHist.sma20;
                const sma20Fut = lastFut.sma20;
                const sma50Hist = lastHist.sma50;
                const sma50Fut = lastFut.sma50;
                const rsi = lastHist.rsi;
                const macd = lastHist.macd;
                const macdSignal = lastHist.signal;

                // Technical Score calculation
                let score = 50;
                const checkTrendLocal = (hV, fV) => {
                    if (hV && fV) {
                        const d = fV - hV;
                        if (d / hV > 0.0002) return 'up';
                        if (d / hV < -0.0002) return 'down';
                    }
                    return 'flat';
                };

                const e9Tr = checkTrendLocal(ema9Hist, ema9Fut);
                const e21Tr = checkTrendLocal(ema21Hist, ema21Fut);
                const s20Tr = checkTrendLocal(sma20Hist, sma20Fut);
                const s50Tr = checkTrendLocal(sma50Hist, sma50Fut);

                if (e9Tr === 'up') score += 10; else if (e9Tr === 'down') score -= 10;
                if (e21Tr === 'up') score += 10; else if (e21Tr === 'down') score -= 10;
                if (s20Tr === 'up') score += 10; else if (s20Tr === 'down') score -= 10;
                if (s50Tr === 'up') score += 10; else if (s50Tr === 'down') score -= 10;

                if (ema9Hist && ema21Hist) score += (ema9Hist > ema21Hist ? 5 : -5);
                if (sma20Hist && sma50Hist) score += (sma20Hist > sma50Hist ? 5 : -5);
                if (currentPrice && sma50Hist) score += (currentPrice > sma50Hist ? 10 : -10);
                if (currentPrice && sma20Hist) score += (currentPrice > sma20Hist ? 5 : -5);

                score = Math.max(1, Math.min(100, Math.round(score)));
                const trendText = score > 50 ? 'Bullish' : (score < 50 ? 'Bearish' : 'Neutral');

                responseText = `### 🤖 Finora AI Technical Report: **${tName}** (${tFrame})

**Current Price:** \`${currentPrice.toFixed(2)}\`
**AI Forecast Target:** \`${lastFut.close.toFixed(2)}\` (\`${(lastFut.close > currentPrice ? '+' : '')}${((lastFut.close - currentPrice)/currentPrice * 100).toFixed(2)}%\`)

#### 📈 Trend & Momentum Analysis
- **Technical Quant Score:** **${score}/100** (${trendText} momentum)
- **Moving Average Slopes:**
  - **EMA 9 (Short term):** ${e9Tr === 'up' ? '↗ Rising' : (e9Tr === 'down' ? '↘ Falling' : '→ Flat')} (from ${ema9Hist?.toFixed(2)} to ${ema9Fut?.toFixed(2)})
  - **SMA 50 (Medium term):** ${s50Tr === 'up' ? '↗ Rising' : (s50Tr === 'down' ? '↘ Falling' : '→ Flat')} (from ${sma50Hist?.toFixed(2)} to ${sma50Fut?.toFixed(2)})
- The price is trading **${currentPrice > sma50Hist ? 'above' : 'below'}** its 50-period SMA, which indicates a **${currentPrice > sma50Hist ? 'primary uptrend' : 'primary downtrend'}**.

#### 📊 Indicators & Levels
- **RSI (14):** **${rsi ? rsi.toFixed(2) : '--'}** (${rsi > 70 ? '⚠️ Overbought' : (rsi < 30 ? '🚀 Oversold' : 'Neutral')})
- **MACD:** ${macd ? `Line at ${macd.toFixed(2)} vs Signal ${macdSignal?.toFixed(2)} (${macd > macdSignal ? 'Bullish bias' : 'Bearish bias'})` : 'N/A'}

#### 💡 Trading Recommendation
The quantitative model suggests a **${trendText.toUpperCase()}** bias. ${score > 50 ? `Consider looking for long entry opportunities on minor pullbacks near short-term supports, targeting the forecasted level of **${lastFut.close.toFixed(2)}**.` : (score < 50 ? `Sustained downside pressure is expected. Maintain defensive risk management or watch resistance ceilings for short setups.` : `Market is consolidating. Scalping between key support and resistance limits is advised.`)}`;
            } else {
                responseText = `### 🤖 Finora AI Chatbot
I found the ticker **${tName}** in our database, but we are currently waiting for historical OHLC data to build indicators. Please try again in a moment.`;
            }
        } else if (contextData.type === 'macro' && contextData.data) {
            const data = contextData.data;
            const cName = contextData.country;
            responseText = `### 🤖 Finora AI Macro Report: **${cName}**

#### 📊 Sovereign Growth & Economic Metrics
- **GDP Growth Rate:** **${data.gdp_growth || '0.00'}%**
- **Inflation Rate:** **${data.inflation || '0.00'}%**
- **Unemployment Rate:** **${data.unemployment || '0.00'}%**
- **Central Bank Interest Rate:** **${data.interest_rate || '0.00'}%**
- **Government Debt to GDP:** **${data.debt_to_gdp || '0.00'}%**
- **10-Year Bond Yield:** **${data.bond_yield || '0.00'}%**

#### 💼 Capital Flows & Liquidity Matrix
- **Foreign Flow (FII):** ${data.foreign_val >= 0 ? '+' : '-'}${fmt(data.foreign_val)} ${data.currency}
- **Local Flow (DII):** ${data.domestic_val >= 0 ? '+' : '-'}${fmt(data.domestic_val)} ${data.currency}
- **Net Market Flow:** **${data.net >= 0 ? '+' : '-'}${fmt(data.net)} ${data.currency}**
- **Market Sentiment Status:** **${data.status || 'Neutral'}**

#### 💡 Macro Analysis
The macro indicators for **${cName}** show GDP growth at **${data.gdp_growth}%** against an inflation rate of **${data.inflation}%**. The net flow of institutional money stands at **${data.net >= 0 ? '+' : '-'}${fmt(data.net)} ${data.currency}**, which makes the primary equity market outlook **${data.net >= 0 ? 'favorable' : 'bearish/volatile'}** in the near term.`;
        } else {
            responseText = `### 🤖 Finora AI Terminal Assistant
Welcome to the FinoraPulse AI Assistant! I can help you analyze stock/crypto charts and global macroeconomic metrics.

Here are some things you can ask me:
- **"Analyze RELIANCE.NS"** or **"Is AAPL bullish?"** to get a detailed technical indicator and AI forecast report.
- **"GDP of India"** or **"Macro indicators for USA"** to get a comprehensive macroeconomic liquidity and growth breakdown.
- **"Compare AAPL vs MSFT"** (if Gemini is activated via a \`GEMINI_API_KEY\` in your env).

I am currently running on a **Local Quant Rules Engine** fallback. Add a \`GEMINI_API_KEY\` to your server's \`.env\` file to unlock fully conversational reasoning!`;
        }

        res.json({ response: responseText });
    } catch (chatErr) {
        console.error("Chatbot API Error:", chatErr);
        res.status(500).json({ error: "Failed to generate AI response. Please try again." });
    }
});

// ==========================================
// 11.5 VIRTUAL DUMMY TRADING ROUTES
// ==========================================

// Helper function to fetch quotes using python quote engine
async function fetchQuotes(tickers) {
    const tickersArray = Array.isArray(tickers) ? tickers : [tickers];
    if (tickersArray.length === 0) return [];
    
    try {
        const result = await fetchPythonData('fundamentals', 'quote_engine.py', [tickersArray.join(',')]);
        if (result && result.error) {
            throw new Error(result.error);
        }
        return Array.isArray(result) ? result : [];
    } catch (err) {
        console.error("Quote fetch helper error:", err.message);
        throw err;
    }
}

// Helper function to fetch USD/INR rate
async function getUsdInrRate() {
    try {
        const results = await fetchQuotes('USDINR=X');
        const result = results.find(r => r.symbol === 'USDINR=X');
        if (result && result.price) {
            return result.price;
        }
    } catch (e) {
        console.error("Error fetching USD/INR rate, using fallback:", e.message);
    }
    return 83.5; // robust fallback rate
}

// Page Route: Dedicated Trade History
app.get('/trade-history', requireLogin, (req, res) => {
    res.render('trade_history');
});

// API Route: Get real-time price quote from Yahoo Finance
app.get('/api/trading/quote', requireLogin, async (req, res) => {
    const ticker = req.query.ticker?.toUpperCase();
    if (!ticker) {
        return res.status(400).json({ error: "Ticker is required." });
    }

    try {
        const results = await fetchQuotes(ticker);
        const result = results.find(r => r.symbol === ticker);
        if (!result || result.error) {
            return res.status(404).json({ error: `Asset not found: ${ticker}` });
        }

        res.json({
            symbol: result.symbol,
            price: result.price || 0,
            change: result.change || 0,
            changePercent: result.changePercent || 0,
            name: result.name || result.symbol,
            currency: result.currency || 'USD'
        });
    } catch (err) {
        console.error("Quote fetch error:", err.message);
        res.status(500).json({ error: "Failed to fetch live price quote. Please try again." });
    }
});

// API Route: Get user's current virtual portfolio
app.get('/api/trading/portfolio', requireLogin, async (req, res) => {
    try {
        const user = await User.findById(req.session.userId);
        if (!user) {
            return res.status(404).json({ error: "User not found." });
        }

        // Initialize virtualBalance if not already set (e.g. for pre-existing accounts)
        if (user.virtualBalance === undefined) {
            user.virtualBalance = 100000;
            await user.save();
        }

        const holdings = user.portfolio || [];
        const enrichedHoldings = [];
        let holdingsValue = 0;

        if (holdings.length > 0) {
            const tickers = holdings.map(h => h.ticker);
            try {
                const quotes = await fetchQuotes(tickers);
                const quoteMap = {};
                quotes.forEach(q => {
                    quoteMap[q.symbol] = q;
                });

                const rate = await getUsdInrRate();

                holdings.forEach(h => {
                    const quote = quoteMap[h.ticker] || {};
                    const currentPrice = quote.price || h.averageBuyPrice;
                    const value = currentPrice * h.shares;

                    const isUSD = quote.currency === 'USD' || (!h.ticker.endsWith('.NS') && !h.ticker.endsWith('.BO') && h.ticker !== 'INR=X');
                    const valueInInr = isUSD ? value * rate : value;
                    holdingsValue += valueInInr;

                    const profitLoss = (currentPrice - h.averageBuyPrice) * h.shares;
                    const profitLossPct = h.averageBuyPrice > 0 
                        ? ((currentPrice - h.averageBuyPrice) / h.averageBuyPrice) * 100 
                        : 0;

                    enrichedHoldings.push({
                        ticker: h.ticker,
                        shares: h.shares,
                        averageBuyPrice: h.averageBuyPrice,
                        currentPrice: currentPrice,
                        currentValue: value,
                        name: quote.name || h.ticker,
                        profitLoss: profitLoss,
                        profitLossPct: profitLossPct
                    });
                });
            } catch (err) {
                console.error("Holdings quote fetch error:", err.message);
                // Fallback to average buy price if quote fetch fails
                holdings.forEach(h => {
                    const value = h.averageBuyPrice * h.shares;
                    holdingsValue += value;
                    enrichedHoldings.push({
                        ticker: h.ticker,
                        shares: h.shares,
                        averageBuyPrice: h.averageBuyPrice,
                        currentPrice: h.averageBuyPrice,
                        currentValue: value,
                        name: h.ticker,
                        profitLoss: 0,
                        profitLossPct: 0
                    });
                });
            }
        }

        const totalPortfolioValue = user.virtualBalance + holdingsValue;
        const totalProfitLoss = totalPortfolioValue - 100000;
        const totalProfitLossPct = (totalProfitLoss / 100000) * 100;

        res.json({
            virtualBalance: user.virtualBalance,
            holdingsValue: holdingsValue,
            totalPortfolioValue: totalPortfolioValue,
            totalProfitLoss: totalProfitLoss,
            totalProfitLossPct: totalProfitLossPct,
            holdings: enrichedHoldings,
            tradeHistory: user.tradeHistory || []
        });
    } catch (err) {
        console.error("Portfolio retrieval error:", err);
        res.status(500).json({ error: "Failed to load portfolio." });
    }
});

// API Route: Process Buy/Sell order
app.post('/api/trading/trade', requireLogin, async (req, res) => {
    let { ticker, action, shares } = req.body;
    ticker = ticker?.toUpperCase();
    action = action?.toUpperCase();
    shares = Number(shares);

    if (!ticker || !action || isNaN(shares) || shares <= 0) {
        return res.status(400).json({ error: "Invalid trade arguments. Symbol, action (BUY/SELL), and quantity must be valid." });
    }

    try {
        const user = await User.findById(req.session.userId);
        if (!user) {
            return res.status(404).json({ error: "User not found." });
        }

        // Initialize virtualBalance if not already set
        if (user.virtualBalance === undefined) {
            user.virtualBalance = 100000;
        }

        // Fetch current quote
        const results = await fetchQuotes(ticker);
        const result = results.find(r => r.symbol === ticker);
        if (!result || result.error) {
            return res.status(404).json({ error: `Could not retrieve live price for: ${ticker}` });
        }

        const price = result.price || 0;
        if (price <= 0) {
            return res.status(400).json({ error: "Invalid asset price." });
        }

        const isUSD = result.currency === 'USD' || (!ticker.endsWith('.NS') && !ticker.endsWith('.BO') && ticker !== 'INR=X');
        const rate = isUSD ? await getUsdInrRate() : 1;
        const totalCostInAssetCurrency = price * shares;
        const totalCostInInr = isUSD ? totalCostInAssetCurrency * rate : totalCostInAssetCurrency;

        if (action === 'BUY') {
            if (user.virtualBalance < totalCostInInr) {
                return res.status(400).json({ error: `Insufficient virtual cash. Required: ₹${totalCostInInr.toFixed(2)}, Available: ₹${user.virtualBalance.toFixed(2)}` });
            }

            // Deduct balance
            user.virtualBalance -= totalCostInInr;

            // Update portfolio
            const holdingIndex = user.portfolio.findIndex(h => h.ticker === ticker);
            if (holdingIndex > -1) {
                const existing = user.portfolio[holdingIndex];
                const totalShares = existing.shares + shares;
                const newAvgPrice = ((existing.shares * existing.averageBuyPrice) + totalCostInAssetCurrency) / totalShares;
                
                user.portfolio[holdingIndex].shares = totalShares;
                user.portfolio[holdingIndex].averageBuyPrice = newAvgPrice;
            } else {
                user.portfolio.push({
                    ticker: ticker,
                    shares: shares,
                    averageBuyPrice: price
                });
            }
        } else if (action === 'SELL') {
            const holdingIndex = user.portfolio.findIndex(h => h.ticker === ticker);
            if (holdingIndex === -1 || user.portfolio[holdingIndex].shares < shares) {
                const owned = holdingIndex === -1 ? 0 : user.portfolio[holdingIndex].shares;
                return res.status(400).json({ error: `Insufficient shares. You want to sell ${shares} shares of ${ticker}, but only own ${owned}.` });
            }

            // Add balance
            user.virtualBalance += totalCostInInr;

            // Update portfolio
            user.portfolio[holdingIndex].shares -= shares;
            if (user.portfolio[holdingIndex].shares <= 0) {
                user.portfolio.splice(holdingIndex, 1);
            }
        } else {
            return res.status(400).json({ error: "Invalid action. Use BUY or SELL." });
        }

        // Record history
        user.tradeHistory.push({
            ticker: ticker,
            type: action,
            shares: shares,
            price: price,
            timestamp: new Date()
        });

        await user.save();

        res.json({
            success: true,
            message: `Successfully executed ${action} of ${shares} shares of ${ticker} at $${price.toFixed(2)}`,
            virtualBalance: user.virtualBalance
        });
    } catch (err) {
        console.error("Trade execution error:", err);
        res.status(500).json({ error: "Trade execution failed. Please try again." });
    }
});

// API Route: Global Leaderboard
app.get('/api/trading/leaderboard', requireLogin, async (req, res) => {
    try {
        const users = await User.find({}, 'username virtualBalance portfolio');
        const leaderboard = [];

        // Identify all tickers across all portfolios to fetch quotes in batch
        const allTickersSet = new Set();
        users.forEach(u => {
            if (u.portfolio && u.portfolio.length > 0) {
                u.portfolio.forEach(h => allTickersSet.add(h.ticker));
            }
        });

        // Batch fetch quotes
        const tickerList = Array.from(allTickersSet);
        const quoteMap = {};

        if (tickerList.length > 0) {
            try {
                const quotes = await fetchQuotes(tickerList);
                quotes.forEach(q => {
                    if (q.price) quoteMap[q.symbol] = q;
                });
            } catch (err) {
                console.error("Leaderboard batch quote fetch error:", err.message);
            }
        }

        const rate = await getUsdInrRate();

        users.forEach(u => {
            // Default virtual balance to 100000 if not present
            const cash = u.virtualBalance !== undefined ? u.virtualBalance : 100000;
            let holdingsValue = 0;

            if (u.portfolio && u.portfolio.length > 0) {
                u.portfolio.forEach(h => {
                    const quote = quoteMap[h.ticker] || {};
                    const price = quote.price || h.averageBuyPrice;
                    const value = price * h.shares;

                    const isUSD = quote.currency === 'USD' || (!h.ticker.endsWith('.NS') && !h.ticker.endsWith('.BO') && h.ticker !== 'INR=X');
                    const valueInInr = isUSD ? value * rate : value;
                    holdingsValue += valueInInr;
                });
            }

            const netWorth = cash + holdingsValue;
            const roi = ((netWorth - 100000) / 100000) * 100;

            leaderboard.push({
                username: u.username,
                netWorth: netWorth,
                roi: roi
            });
        });

        // Sort leaderboard by networth descending
        leaderboard.sort((a, b) => b.netWorth - a.netWorth);

        // Keep top 10
        const top10 = leaderboard.slice(0, 10);

        res.json(top10);
    } catch (err) {
        console.error("Leaderboard calculation error:", err);
        res.status(500).json({ error: "Failed to compile leaderboard." });
    }
});

// ==========================================
// 12. START SERVER
// ==========================================
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`🚀 FinoraPulse Live at: http://localhost:${PORT}`));