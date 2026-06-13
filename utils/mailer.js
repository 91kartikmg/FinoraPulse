const nodemailer = require('nodemailer');

/**
 * Sends a password reset OTP email to the user.
 * Falls back to printing to the console if SMTP credentials are not set.
 * 
 * @param {string} email User's email address
 * @param {string} username User's unique ID
 * @param {string} otp The 6-digit verification code
 * @returns {Promise<boolean>} Resolves to true if successful, false otherwise
 */
async function sendOTPEmail(email, username, otp) {
    const host = process.env.SMTP_HOST;
    const port = process.env.SMTP_PORT || 587;
    const user = process.env.SMTP_USER;
    const pass = process.env.SMTP_PASS;

    // Check if configuration exists
    const hasConfig = host && user && pass;

    // Beautiful HTML template matching StockPulse AI theme
    const htmlContent = `
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Reset Your Password - FinoraPulse AI</title>
        <style>
            body {
                margin: 0; padding: 0; background-color: #020617; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #f8fafc;
            }
            .container {
                max-width: 500px; margin: 40px auto; padding: 32px; background: #0f172a; border-radius: 16px; border: 1px solid #1e293b; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3);
            }
            .logo {
                font-size: 24px; font-weight: 700; text-align: center; margin-bottom: 24px; color: #f8fafc;
            }
            .logo span {
                color: #38bdf8;
            }
            .title {
                font-size: 20px; font-weight: 600; text-align: center; margin-bottom: 16px; color: #f8fafc;
            }
            .greeting {
                font-size: 16px; line-height: 1.5; margin-bottom: 20px; color: #94a3b8;
            }
            .otp-box {
                background: rgba(56, 189, 248, 0.1); border: 1px dashed #38bdf8; color: #38bdf8; font-size: 32px; font-weight: 700; text-align: center; padding: 16px; border-radius: 12px; margin: 24px 0; letter-spacing: 6px;
            }
            .warning {
                font-size: 13px; line-height: 1.6; color: #64748b; border-top: 1px solid #1e293b; padding-top: 20px; margin-top: 24px; text-align: center;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">FinoraPulse <span>AI</span></div>
            <div class="title">Verification OTP</div>
            <p class="greeting">Hello <strong>${username}</strong>,</p>
            <p class="greeting">We received a request to reset the password for your FinoraPulse AI account. Please use the following 6-digit One-Time Password (OTP) to complete your password reset. This code is valid for 10 minutes.</p>
            
            <div class="otp-box">${otp}</div>
            
            <p class="greeting" style="font-size: 14px;">If you didn't request a password reset, please ignore this email or secure your account if you suspect unauthorized access.</p>
            
            <div class="warning">
                This is an automated security transmission from FinoraPulse AI.<br>
                Do not share this OTP with anyone, including our support team.
            </div>
        </div>
    </body>
    </html>
    `;

    if (!hasConfig) {
        console.log(`\n============================================================`);
        console.log(`🔑 [DEVELOPER OTP FALLBACK]`);
        console.log(`📧 Target Email: ${email}`);
        console.log(`👤 Username:     ${username}`);
        console.log(`🚨 OTP:          ${otp}`);
        console.log(`📝 SMTP not configured in .env. Falling back to terminal output.`);
        console.log(`============================================================\n`);
        return true;
    }

    try {
        const transporter = nodemailer.createTransport({
            host: host,
            port: Number(port),
            secure: Number(port) === 465, // true for port 465, false for other ports
            auth: {
                user: user,
                pass: pass
            }
        });

        await transporter.sendMail({
            from: `"FinoraPulse AI Security" <${user}>`,
            to: email,
            subject: `[FinoraPulse AI] Security OTP: ${otp}`,
            text: `Hello ${username},\n\nYour 6-digit verification code is: ${otp}\n\nIt is valid for 10 minutes. If you did not request this, please ignore this email.`,
            html: htmlContent
        });

        console.log(`✅ [MAILER SUCCESS] OTP sent successfully to ${email}`);
        return true;
    } catch (err) {
        console.error(`❌ [MAILER ERROR] Failed to send OTP to ${email}. error:`, err.message);
        console.log(`\n============================================================`);
        console.log(`🔑 [DEVELOPER OTP FALLBACK - ON SMTP ERROR]`);
        console.log(`📧 Target Email: ${email}`);
        console.log(`👤 Username:     ${username}`);
        console.log(`🚨 OTP:          ${otp}`);
        console.log(`============================================================\n`);
        return false; // Return false to indicate the email failed to send
    }
}

/**
 * Sends a welcome email to the user after successful registration and verification.
 * 
 * @param {string} email User's email address
 * @param {string} username User's unique ID
 * @returns {Promise<boolean>} Resolves to true if successful, false otherwise
 */
async function sendWelcomeEmail(email, username) {
    const host = process.env.SMTP_HOST;
    const port = process.env.SMTP_PORT || 587;
    const user = process.env.SMTP_USER;
    const pass = process.env.SMTP_PASS;

    const hasConfig = host && user && pass;

    const htmlContent = `
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Welcome to FinoraPulse AI</title>
        <style>
            body { margin: 0; padding: 0; background-color: #020617; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #f8fafc; }
            .container { max-width: 500px; margin: 40px auto; padding: 32px; background: #0f172a; border-radius: 16px; border: 1px solid #1e293b; box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.3); }
            .logo { font-size: 24px; font-weight: 700; text-align: center; margin-bottom: 24px; color: #f8fafc; }
            .logo span { color: #38bdf8; }
            .title { font-size: 20px; font-weight: 600; text-align: center; margin-bottom: 16px; color: #f8fafc; }
            .greeting { font-size: 16px; line-height: 1.5; margin-bottom: 20px; color: #94a3b8; }
            .button-container { text-align: center; margin: 30px 0; }
            .btn { background: #38bdf8; color: #020617; padding: 12px 24px; text-decoration: none; border-radius: 8px; font-weight: 700; display: inline-block; }
            .warning { font-size: 13px; line-height: 1.6; color: #64748b; border-top: 1px solid #1e293b; padding-top: 20px; margin-top: 24px; text-align: center; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="logo">FinoraPulse <span>AI</span></div>
            <div class="title">Welcome Aboard! 🚀</div>
            <p class="greeting">Hello <strong>${username}</strong>,</p>
            <p class="greeting">Your account has been successfully verified! Welcome to FinoraPulse AI. We are thrilled to have you join our community of intelligent traders and investors.</p>
            <p class="greeting">Get ready to explore real-time macro-economic metrics, AI-driven stock predictions, institutional liquidity trackers, and advanced technical charting canvases.</p>
            
            <div class="button-container">
                <a href="https://finorapulse.com" class="btn">Explore Dashboard</a>
            </div>
            
            <p class="greeting" style="font-size: 14px;">If you have any questions or need assistance, feel free to reply to this email.</p>
            
            <div class="warning">
                You are receiving this email because you recently created an account at FinoraPulse.com.<br>
            </div>
        </div>
    </body>
    </html>
    `;

    if (!hasConfig) {
        console.log(`\n============================================================`);
        console.log(`🎉 [DEVELOPER WELCOME MAIL FALLBACK]`);
        console.log(`📧 Target Email: ${email}`);
        console.log(`👤 Username:     ${username}`);
        console.log(`📝 SMTP not configured in .env. Falling back to terminal output.`);
        console.log(`============================================================\n`);
        return true;
    }

    try {
        const transporter = nodemailer.createTransport({
            host: host,
            port: Number(port),
            secure: Number(port) === 465,
            auth: { user: user, pass: pass }
        });

        await transporter.sendMail({
            from: `"FinoraPulse Team" <${user}>`,
            to: email,
            subject: `Welcome to FinoraPulse AI! 🚀`,
            text: `Hello ${username},\n\nYour account has been successfully verified! Welcome to FinoraPulse AI.\n\nExplore our dashboard at https://finorapulse.com`,
            html: htmlContent
        });

        console.log(`✅ [MAILER SUCCESS] Welcome email sent successfully to ${email}`);
        return true;
    } catch (err) {
        console.error(`❌ [MAILER ERROR] Failed to send Welcome email to ${email}. error:`, err.message);
        return false;
    }
}

module.exports = {
    sendOTPEmail,
    sendWelcomeEmail
};
