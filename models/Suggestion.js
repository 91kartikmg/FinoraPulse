const mongoose = require('mongoose');

const suggestionSchema = new mongoose.Schema({
    userId: {
        type: mongoose.Schema.Types.ObjectId,
        ref: 'User',
        required: false
    },
    username: {
        type: String,
        default: 'Anonymous'
    },
    text: {
        type: String,
        required: true,
        trim: true
    },
    createdAt: {
        type: Date,
        default: Date.now
    }
});

module.exports = mongoose.model('Suggestion', suggestionSchema);
