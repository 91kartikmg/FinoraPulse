const express = require('express');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs'); // Added for file system operations
const mongoose = require('mongoose');
const session = require('express-session');
const bcrypt = require('bcryptjs');
const User = require('./models/User'); // Import User Model
const axios = require('axios'); // Install via: npm install axios
const app = express();

// --- CONFIGURATION ---
const MONGO_URI = 'mongodb://127.0.0.1:27017/stockpulse_db'; // Local MongoDB
const SESSION_SECRET = 'supersecret_stockpulse_key'; // Change this in production
const DATASET_PATH = path.join(__dirname, 'datasets'); // Specific folder for CSVs

// --- DYNAMIC PYTHON PATH LOGIC ---
// This automatically detects if it's running on your Hostinger server or local Windows PC
let PYTHON_PATH = 'python'; // Default for Local Windows Environment
const serverVenvPath = '/var/www/FinoraPulse/venv/bin/python3'

if (fs.existsSync(serverVenvPath)) {
    PYTHON_PATH = serverVenvPath;
    console.log(`🐍 Using Server Python Environment: ${PYTHON_PATH}`);
} else {
    console.log(`🐍 Using Local Python Environment: ${PYTHON_PATH}`);
}

// Ensure the dataset folder exists
if (!fs.existsSync(DATASET_PATH)) {
    fs.mkdirSync(DATASET_PATH);
    console.log("📁 Created datasets folder");
}


// --- MIDDLEWARE ---
app.use(express.urlencoded({ extended: true })); // Parse form data
app.use(express.json());
app.use(express.static('public'));

// Session Setup (Handles Auto-Login)
app.use(session({
    secret: SESSION_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: { 
        maxAge: 1000 * 60 * 60 * 24 * 7 // 7 Days auto-login
    }
}));

// View Engine
app.set('view engine', 'ejs');
app.set('views', path.join(__dirname, 'views'));

// --- DATABASE CONNECTION ---
mongoose.connect(MONGO_URI)
    .then(() => console.log("✅ MongoDB Connected"))
    .catch(err => console.error("❌ MongoDB Error:", err));

// --- AUTH MIDDLEWARE ---
// Protects the predict page
const requireLogin = (req, res, next) => {
    if (!req.session.userId) {
        return res.redirect('/auth');
    }
    next();
};

// Make 'user' available to all templates if logged in
app.use(async (req, res, next) => {
    res.locals.user = null;
    if (req.session.userId) {
        const user = await User.findById(req.session.userId);
        if (user) res.locals.user = user;
    }
    next();
});

// --- ROUTES ---

// 1. Home Page
app.get('/', (req, res) => {
    res.render('home');
});

// 2. Auth Page (Login/Register)
app.get('/auth', (req, res) => {
    if (req.session.userId) return res.redirect('/');
    res.render('auth');
});

// 3. Register Logic
app.post('/register', async (req, res) => {
    const { email, username, password, confirmPassword } = req.body;

    // Basic Validation
    if (password !== confirmPassword) {
        return res.render('auth', { error: "Passwords do not match" });
    }

    try {
        // Check if user exists
        const existingUser = await User.findOne({ $or: [{ email }, { username }] });
        if (existingUser) {
            return res.render('auth', { error: "User ID or Email already exists" });
        }

        // Hash Password
        const hashedPassword = await bcrypt.hash(password, 10);

        // Create User
        const newUser = new User({ email, username, password: hashedPassword });
        await newUser.save();

        // Auto Login after Register
        req.session.userId = newUser._id;
        res.redirect('/');
        
    } catch (err) {
        res.render('auth', { error: "Error creating account. Try again." });
    }
});

// 4. Login Logic
app.post('/login', async (req, res) => {
    const { loginInput, password } = req.body; // loginInput can be email OR username

    try {
        // Find by Email OR Username
        const user = await User.findOne({ 
            $or: [{ email: loginInput }, { username: loginInput }] 
        });

        if (!user) {
            return res.render('auth', { error: "Invalid credentials" });
        }

        // Check Password
        const isMatch = await bcrypt.compare(password, user.password);
        if (!isMatch) {
            return res.render('auth', { error: "Invalid credentials" });
        }

        // Set Session (Login Success)
        req.session.userId = user._id;
        res.redirect('/');

    } catch (err) {
        res.render('auth', { error: "Login failed. Please try again." });
    }
});

// 5. Logout
app.get('/logout', (req, res) => {
    req.session.destroy(() => {
        res.redirect('/');
    });
});

// 6. Predict Page (PROTECTED)
// Now uses requireLogin middleware
app.get('/predict', requireLogin, (req, res) => {
    const ticker = (req.query.ticker || 'RELIANCE.NS').toUpperCase();
    startPythonWorker(ticker, "1h"); // Start with 1h default
    res.render('predict', { ticker: ticker }); 
});

// --- PYTHON ENGINE LOGIC ---
let statsCache = {}; 
let pythonProcesses = {};

function startPythonWorker(ticker, timeframe = "1h") {
    const cacheKey = `${ticker}_${timeframe}`;
    
    Object.keys(pythonProcesses).forEach(key => {
        if (key.startsWith(`${ticker}_`) && key !== cacheKey) {
            console.log(`[Manager] Stopping old timeframe worker for ${key}`);
            pythonProcesses[key].kill();
            delete pythonProcesses[key];
            delete statsCache[key];
        }
    });

    if (pythonProcesses[cacheKey]) return;
    
    console.log(`[Manager] Starting AI Engine for ${ticker} (${timeframe})...`);
    console.log(`[Manager] Using Python: ${PYTHON_PATH}`);
    console.log(`[Manager] Dataset path: ${DATASET_PATH}`);
    
    const pythonWorker = spawn(PYTHON_PATH, ['predict.py', ticker, timeframe, DATASET_PATH]);
    pythonProcesses[cacheKey] = pythonWorker;
    statsCache[cacheKey] = { waiting: true };

    // Buffer for stdout
    let stdoutBuffer = '';
    
    pythonWorker.stdout.on('data', (data) => {
        stdoutBuffer += data.toString();
        const lines = stdoutBuffer.split('\n');
        
        // Keep the last incomplete line in buffer
        stdoutBuffer = lines.pop() || '';
        
        lines.forEach(line => {
            if (line.trim()) {
                try {
                    const parsed = JSON.parse(line);
                    if (parsed.current) {
                        statsCache[cacheKey] = parsed;
                        console.log(`[Manager] Received data for ${cacheKey}`);
                    }
                } catch (e) {
                    console.error(`[Manager] Parse error for ${cacheKey}:`, e.message);
                    console.error(`[Manager] Raw data:`, line.substring(0, 200));
                }
            }
        });
    });

    // CRITICAL: Log stderr to see Python errors
    pythonWorker.stderr.on('data', (data) => {
        const errorMsg = data.toString();
        console.error(`[Python Error for ${cacheKey}]:`, errorMsg);
    });

    pythonWorker.on('close', (code) => {
        console.log(`[Manager] Engine for ${cacheKey} closed (Code: ${code})`);
        if (code !== 0) {
            console.error(`[Manager] Python process exited with error code ${code}`);
            // Check if there's any remaining output in buffer
            if (stdoutBuffer.trim()) {
                console.error(`[Manager] Unprocessed stdout:`, stdoutBuffer);
            }
        }
        delete pythonProcesses[cacheKey];
    });
    
    pythonWorker.on('error', (err) => {
        console.error(`[Manager] Failed to start Python process:`, err);
    });
}

// 7. API Routes
app.get('/api/stats', (req, res) => {
    const ticker = req.query.ticker;
    const timeframe = req.query.timeframe || '1h'; // Defaulted to 1h
    if (!ticker) return res.status(400).json({error: "Ticker required"});
    
    const cacheKey = `${ticker}_${timeframe}`;
    
    if (!pythonProcesses[cacheKey]) {
        startPythonWorker(ticker, timeframe);
    }
    
    res.json(statsCache[cacheKey] || { waiting: true });
});

// --- MARKET WATCHER LOGIC ---
// ==========================================
// 1. MARKET WATCHER LOGIC (FOR TOP HEADER)
// ==========================================
// ==========================================
// 1. MARKET WATCHER LOGIC (FOR TOP HEADER)
// ==========================================
const WATCHLIST = [
    'RELIANCE.NS', 'TCS.NS', 'BTC-USD', 'GC=F', '^NSEI', 
    'HDFCBANK.NS', 'INFY.NS', 'AAPL', 'NVDA', 'TSLA', 
    'ETH-USD', 'SOL-USD', 'SI=F', 'EURUSD=X', '^BSESN'
];
let marketCache = {};

function startMarketWatcher() {
    // Passes the tickers to prices.py
    const worker = spawn(PYTHON_PATH, ['prices.py', WATCHLIST.join(',')]);
    worker.stdout.on('data', data => {
        try {
            const strData = data.toString().trim();
            const lines = strData.split('\n');
            lines.forEach(line => {
                if (line.startsWith('{')) marketCache = JSON.parse(line);
            });
        } catch (e) {}
    });
    // Update every 1 hour
    // Update every 15 minutes
    worker.on('close', () => setTimeout(startMarketWatcher, 900000));
}
startMarketWatcher();

app.get('/api/market', (req, res) => res.json(marketCache));


// ==========================================
// 2. TOP MOVERS LOGIC (FOR HOME PAGE CARDS)
// ==========================================
let topMoversCache = {};

function startTopMoversWatcher() {
    // Passes the command "TOP_MOVERS" to the exact same prices.py file
    const worker = spawn(PYTHON_PATH, ['prices.py', 'TOP_MOVERS']);
    worker.stdout.on('data', data => {
        try {
            const strData = data.toString().trim();
            const lines = strData.split('\n');
            lines.forEach(line => {
                if (line.startsWith('{')) topMoversCache = JSON.parse(line);
            });
        } catch (e) {}
    });
    // Update every 5 minutes
    worker.on('close', () => setTimeout(startTopMoversWatcher, 300000));
}
startTopMoversWatcher();

app.get('/api/top-movers', (req, res) => res.json(topMoversCache));

// --- FUNDAMENTALS API ---
app.get('/api/fundamentals', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const pythonProcess = spawn(PYTHON_PATH, ['fundamental.py', ticker]);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            const json = JSON.parse(dataString);
            res.json(json);
        } catch (e) {
            console.error("Fundamentals Error:", e);
            res.status(500).json({ error: "Failed to parse Python data" });
        }
    });
});

// --- MACRO EXPLORER ROUTES ---
app.get('/macro', requireLogin, (req, res) => {
    const country = req.query.country || 'IN';
    res.render('macro', { country: country });
});

app.get('/api/macro-explorer', (req, res) => {
    const country = req.query.country || 'IN';
    const pythonProcess = spawn(PYTHON_PATH, ['macro_explorer.py', country]);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            const json = JSON.parse(dataString);
            res.json(json);
        } catch (e) {
            res.status(500).json({ error: "Failed to load macro data" });
        }
    });
});

// --- HEATMAP ROUTES ---
app.get('/heatmap', requireLogin, (req, res) => {
    res.render('heatmap');
});

app.get('/api/heatmap-data', (req, res) => {
    const pythonProcess = spawn(PYTHON_PATH, ['heatmap.py']);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            const json = JSON.parse(dataString);
            res.json(json);
        } catch (e) {
            res.status(500).json({ error: "Failed to load heatmap data" });
        }
    });
});

// --- NLP SENTIMENT API ---
app.get('/api/sentiment', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const pythonProcess = spawn(PYTHON_PATH, ['sentiment.py', ticker]);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            const json = JSON.parse(dataString);
            res.json(json);
        } catch (e) {
            res.status(500).json({ error: "Failed to parse sentiment data" });
        }
    });
});

// --- SEARCH SUGGESTIONS API (UPDATED FOR MULTI-ASSET & REGION) ---
app.get('/api/search-suggest', async (req, res) => {
    const query = req.query.q;
    if (!query) return res.json([]);

    try {
        const response = await axios.get(`https://query1.finance.yahoo.com/v1/finance/search?q=${query}`);
        
        const suggestions = response.data.quotes.map(quote => {
            // Determine region based on exchange or symbol suffix
            const isIndian = quote.exchange === 'NSI' || quote.exchange === 'BSE' || (quote.symbol && quote.symbol.endsWith('.NS')) || (quote.symbol && quote.symbol.endsWith('.BO'));
            const region = isIndian ? '🇮🇳 India' : '🇺🇸 Global/USA';
            
            return {
                symbol: quote.symbol,
                name: quote.shortname || quote.longname || quote.symbol,
                region: region,
                type: formatType(quote.quoteType),
                exchange: quote.exchDisp
            };
        }).slice(0, 10);

        // Inject spot gold manually if user searches "gold rate"
        if (query.toLowerCase().includes('gold rate')) {
            suggestions.unshift({
                symbol: 'GC=F',
                name: 'Spot Gold Rate',
                region: '🇺🇸 Global/USA',
                type: 'Rate',
                exchange: 'COMEX'
            });
        }

        res.json(suggestions);
    } catch (err) {
        console.error("Suggestion Error:", err.message);
        res.json([]);
    }
});

// Helper to make asset types look clean
function formatType(type) {
    const types = {
        'EQUITY': 'Stock',
        'CRYPTO': 'Crypto',
        'ETF': 'ETF',
        'INDEX': 'Index',
        'CURRENCY': 'Forex',
        'MUTUALFUND': 'Fund',
        'FUTURE': 'Commodity'
    };
    return types[type] || type;
}




// --- PEER COMPARISON API ---ss
app.get('/api/peers', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    // Launch the new peers.py script
    const pythonProcess = spawn(PYTHON_PATH, ['peers.py', ticker]);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            const json = JSON.parse(dataString);
            res.json(json);
        } catch (e) {
            res.status(500).json({ error: "Failed to fetch peer data" });
        }
    });
});

// --- SMART MONEY API ---
app.get('/api/smart-money', (req, res) => {
    const ticker = req.query.ticker;
    if (!ticker) return res.status(400).json({ error: "Ticker required" });

    const pythonProcess = spawn(PYTHON_PATH, ['smart_money.py', ticker]);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            res.json(JSON.parse(dataString));
        } catch (e) {
            res.status(500).json({ error: "Failed to fetch Smart Money data" });
        }
    });
});

// --- CROSS-ASSET CORRELATION API ---
app.get('/api/correlation', (req, res) => {
    const pythonProcess = spawn(PYTHON_PATH, ['correlation.py']);
    
    let dataString = '';
    pythonProcess.stdout.on('data', (data) => {
        dataString += data.toString();
    });

    pythonProcess.on('close', (code) => {
        try {
            res.json(JSON.parse(dataString));
        } catch (e) {
            res.status(500).json({ error: "Failed to load correlation data" });
        }
    });
});

const PORT = 3000;
app.listen(PORT, () => console.log(`StockPulse Live at: http://localhost:${PORT}`));