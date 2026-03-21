const express = require('express');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const mongoose = require('mongoose');
const session = require('express-session');
const bcrypt = require('bcryptjs');
const User = require('./models/User'); 
const axios = require('axios'); 
const app = express();

// --- CONFIGURATION ---
const MONGO_URI = 'mongodb://127.0.0.1:27017/stockpulse_db';
const SESSION_SECRET = 'supersecret_stockpulse_key';
const DATASET_PATH = path.join(__dirname, 'datasets');

// --- DYNAMIC PYTHON PATH LOGIC ---
// Automatically switches between Server (Linux Venv) and Local (Windows)
let PYTHON_PATH = 'python'; // Default for Local Windows
const serverVenvPath = '/var/www/FinoraPulse/venv/bin/python3'; // Standard Linux venv path

if (fs.existsSync(serverVenvPath)) {
    PYTHON_PATH = serverVenvPath;
    console.log(`🐍 SERVER MODE: Using Venv Python at ${PYTHON_PATH}`);
} else {
    console.log(`🐍 LOCAL MODE: Using System Python`);
}

// Ensure the dataset folder exists for CSV storage
if (!fs.existsSync(DATASET_PATH)) {
    fs.mkdirSync(DATASET_PATH, { recursive: true });
    console.log("📁 Created datasets folder");
}

// --- MIDDLEWARE ---
app.use(express.urlencoded({ extended: true }));
app.use(express.json());
app.use(express.static('public'));

app.use(session({
    secret: SESSION_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: { maxAge: 1000 * 60 * 60 * 24 * 7 } // 7 Days
}));

app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// --- DATABASE CONNECTION ---
mongoose.connect(MONGO_URI)
    .then(() => console.log("✅ MongoDB Connected"))
    .catch(err => console.error("❌ MongoDB Error:", err));

// --- AUTH MIDDLEWARE ---
const requireLogin = (req, res, next) => {
    if (!req.session.userId) return res.redirect('/auth');
    next();
};

app.use(async (req, res, next) => {
    res.locals.user = null;
    if (req.session.userId) {
        try {
            const user = await User.findById(req.session.userId);
            if (user) res.locals.user = user;
        } catch (e) { console.error("Session User Error"); }
    }
    next();
});

// --- ROUTES ---

app.get('/', (req, res) => res.render('home'));

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
        res.render('auth', { error: "Error creating account." });
    }
});

app.post('/login', async (req, res) => {
    const { loginInput, password } = req.body;
    try {
        const user = await User.findOne({ $or: [{ email: loginInput }, { username: loginInput }] });
        if (!user || !(await bcrypt.compare(password, user.password))) {
            return res.render('auth', { error: "Invalid credentials" });
        }
        req.session.userId = user._id;
        res.redirect('/');
    } catch (err) {
        res.render('auth', { error: "Login failed." });
    }
});

app.get('/logout', (req, res) => req.session.destroy(() => res.redirect('/')));

app.get('/predict', requireLogin, (req, res) => {
    const ticker = (req.query.ticker || 'RELIANCE.NS').toUpperCase();
    startPythonWorker(ticker, "1h");
    res.render('predict', { ticker: ticker });
});

// --- PYTHON ENGINE LOGIC (AI PREDICTOR) ---
let statsCache = {};
let pythonProcesses = {};

function startPythonWorker(ticker, timeframe = "1h") {
    const cacheKey = `${ticker}_${timeframe}`;

    // Kill existing processes for the same ticker but different timeframe to save CPU
    Object.keys(pythonProcesses).forEach(key => {
        if (key.startsWith(`${ticker}_`) && key !== cacheKey) {
            console.log(`[Manager] Stopping worker: ${key}`);
            pythonProcesses[key].kill();
            delete pythonProcesses[key];
            delete statsCache[key];
        }
    });

    if (pythonProcesses[cacheKey]) return;

    console.log(`[Manager] Spawning AI Engine for ${ticker} (${timeframe})...`);
    
    const pythonWorker = spawn(PYTHON_PATH, ['predict.py', ticker, timeframe, DATASET_PATH]);
    pythonProcesses[cacheKey] = pythonWorker;
    statsCache[cacheKey] = { waiting: true };

    pythonWorker.stdout.on('data', (data) => {
        try {
            const lines = data.toString().split('\n');
            lines.forEach(line => {
                if (line.trim().startsWith('{')) {
                    const parsed = JSON.parse(line);
                    if (parsed.current) statsCache[cacheKey] = parsed;
                }
            });
        } catch (e) { /* Buffer might be partial */ }
    });

    pythonWorker.on('close', (code) => {
        console.log(`[Manager] AI Engine ${cacheKey} closed (Code: ${code})`);
        delete pythonProcesses[cacheKey];
    });

    pythonWorker.stderr.on('data', (data) => {
        console.error(`[Python Error - Predict]: ${data}`);
    });
}

// --- API ENDPOINTS ---

app.get('/api/stats', (req, res) => {
    const { ticker, timeframe = '1h' } = req.query;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });
    
    const cacheKey = `${ticker}_${timeframe}`;
    if (!pythonProcesses[cacheKey]) startPythonWorker(ticker, timeframe);
    
    res.json(statsCache[cacheKey] || { waiting: true });
});

// --- FUNDAMENTALS API ---
app.get('/api/fundamentals', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const py = spawn(PYTHON_PATH, ['fundamental.py', ticker]);
    let output = '';
    
    py.stdout.on('data', (data) => output += data.toString());
    py.on('close', (code) => {
        try {
            if (!output.trim()) throw new Error("Empty output");
            res.json(JSON.parse(output));
        } catch (e) {
            console.error(`Fundamentals Parse Error for ${ticker}`);
            res.status(500).json({ error: "Python logic failed" });
        }
    });
});

// --- SENTIMENT API ---
app.get('/api/sentiment', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const py = spawn(PYTHON_PATH, ['sentiment.py', ticker]);
    let output = '';
    py.stdout.on('data', (data) => output += data.toString());
    py.on('close', () => {
        try {
            res.json(JSON.parse(output));
        } catch (e) { res.status(500).json({ error: "Sentiment failed" }); }
    });
});

// --- PEER COMPARISON ---
app.get('/api/peers', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const py = spawn(PYTHON_PATH, ['peers.py', ticker]);
    let output = '';
    py.stdout.on('data', (data) => output += data.toString());
    py.on('close', () => {
        try {
            res.json(JSON.parse(output));
        } catch (e) { res.status(500).json({ error: "Peers failed" }); }
    });
});

// --- SEARCH SUGGESTIONS ---
app.get('/api/search-suggest', async (req, res) => {
    const query = req.query.q;
    if (!query) return res.json([]);
    try {
        const response = await axios.get(`https://query1.finance.yahoo.com/v1/finance/search?q=${query}`);
        const suggestions = response.data.quotes.map(quote => {
            const isIndian = quote.exchange === 'NSI' || (quote.symbol && quote.symbol.endsWith('.NS'));
            return {
                symbol: quote.symbol,
                name: quote.shortname || quote.symbol,
                region: isIndian ? '🇮🇳 India' : '🇺🇸 Global',
                type: quote.quoteType,
                exchange: quote.exchDisp
            };
        }).slice(0, 10);
        res.json(suggestions);
    } catch (err) { res.json([]); }
});

const PORT = 3000;
app.listen(PORT, () => console.log(`🚀 FinoraPulse running on port ${PORT}`));