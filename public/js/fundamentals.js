/**
 * fundamentals.js
 * Handles fetching and displaying company balance sheet data.
 */

function formatNumber(num) {
    if (!num) return "N/A";
    if (num >= 1e12) return (num / 1e12).toFixed(2) + 'T'; // Trillion
    if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';  // Billion
    if (num >= 1e7) return (num / 1e7).toFixed(2) + 'Cr'; // Crore (Optional for India)
    if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';  // Million
    return num.toLocaleString();
}

async function fetchFundamentals(ticker) {
    const target = document.getElementById('fundamentals-target');
    const isIndian = ticker.includes('.NS') || ticker.includes('.BO');
    const curSym = isIndian ? "₹" : "$";

    // Show loading state
    target.innerHTML = `<div class="loading-pulse">Analyzing Balance Sheets for ${ticker}...</div>`;

    try {
        const response = await fetch(`/api/fundamentals?ticker=${ticker}`);
        const f = await response.json();

        if (f.error) {
            target.innerHTML = `<div style="color:#ef4444">Error: ${f.error}</div>`;
            return;
        }

        // Render the Grid
        target.innerHTML = `
            <div class="f-item">
                <span class="f-label">Market Cap</span>
                <span class="f-value">${curSym}${formatNumber(f.marketCap)}</span>
            </div>
            <div class="f-item">
                <span class="f-label">P/E Ratio</span>
                <span class="f-value accent-text">${f.pe_ratio ? f.pe_ratio.toFixed(2) : 'N/A'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">EPS (TTM)</span>
                <span class="f-value">${curSym}${f.eps ? f.eps.toFixed(2) : '0.00'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">ROE</span>
                <span class="f-value">${f.roe ? (f.roe * 100).toFixed(2) + '%' : 'N/A'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">Book Value</span>
                <span class="f-value">${curSym}${f.bookValue ? f.bookValue.toFixed(2) : 'N/A'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">Price / Book</span>
                <span class="f-value">${f.priceToBook ? f.priceToBook.toFixed(2) : 'N/A'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">Div Yield</span>
                <span class="f-value">${f.dividendYield ? (f.dividendYield * 100).toFixed(2) + '%' : '0.00%'}</span>
            </div>
            <div class="f-item">
                <span class="f-label">Debt / Equity</span>
                <span class="f-value" style="color:${(f.debtToEquity > 100) ? '#ef4444' : '#f8fafc'}">
                    ${f.debtToEquity ? f.debtToEquity.toFixed(2) : 'N/A'}
                </span>
            </div>
        `;
    } catch (err) {
        console.error("Fundamentals Fetch Error:", err);
        target.innerHTML = `<div style="color:#ef4444">Failed to retrieve data.</div>`;
    }
}