const mongoose = require('mongoose');

const userSchema = new mongoose.Schema({
    username: { 
        type: String, 
        required: true, 
        unique: true,
        trim: true 
    },
    email: { 
        type: String, 
        required: true, 
        unique: true, 
        trim: true,
        lowercase: true
    },
    password: { 
        type: String, 
        required: true 
    },
    resetPasswordOTP: {
        type: String,
        default: null
    },
    resetPasswordOTPExpires: {
        type: Date,
        default: null
    },
    createdAt: { 
        type: Date, 
        default: Date.now 
    },
    virtualBalance: {
        type: Number,
        default: 100000
    },
    portfolio: [{
        ticker: { type: String, required: true },
        shares: { type: Number, required: true },
        averageBuyPrice: { type: Number, required: true }
    }],
    tradeHistory: [{
        ticker: { type: String, required: true },
        type: { type: String, enum: ['BUY', 'SELL'], required: true },
        shares: { type: Number, required: true },
        price: { type: Number, required: true },
        timestamp: { type: Date, default: Date.now }
    }]
});

module.exports = mongoose.model('User', userSchema);