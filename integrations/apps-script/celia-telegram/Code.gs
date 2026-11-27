/**
 * CELIA → Telegram (free, no server)
 * Direct Google Apps Script integration with @CeliaFashionBot via the
 * Telegram Bot API. No middleware, no paid tier.
 *
 * SETUP
 *   1. Paste this whole file into your Apps Script project
 *      (script.google.com/home/projects/1UNRaWAvGNYI6lYnJ1nlQt3PyMsG3eZkI--WASZrhMPoBzB6ZqiBXG-5z/edit).
 *   2. Set BOT_TOKEN (from @BotFather) and CHAT_ID (from the bot's
 *      "chat id الخاص بك: <number>" reply).
 *   3. Run testSendTelegram() once to confirm.
 *
 * SECURITY
 *   - Never commit/share BOT_TOKEN. Anyone with it controls the bot.
 *   - CHAT_ID is not secret, but keep it private to your chat.
 */
// ===== CONFIG — fill these in =====
var BOT_TOKEN = "PASTE_YOUR_BOT_TOKEN_FROM_BOTFATHER_HERE";
var CHAT_ID   = "PASTE_YOUR_CHAT_ID_HERE"; // from the bot's reply
// ==================================

function telegramApiUrl(method) {
  return "https://api.telegram.org/bot" + BOT_TOKEN + "/" + method;
}

/**
 * Send a plain text message to CHAT_ID.
 * @param {string} text   message body
 * @param {Object} [opts] {parse_mode: "HTML"|"MarkdownV2", silent: bool}
 * @return {Object} Telegram API response (parsed JSON)
 */
function sendTelegram(text, opts) {
  opts = opts || {};
  if (!BOT_TOKEN || BOT_TOKEN.indexOf("PASTE") === 0) {
    throw new Error("Set BOT_TOKEN first (from @BotFather).");
  }
  if (!CHAT_ID || String(CHAT_ID).indexOf("PASTE") === 0) {
    throw new Error("Set CHAT_ID first (from the bot's reply).");
  }
  var payload = { chat_id: CHAT_ID, text: String(text) };
  if (opts.parse_mode) payload.parse_mode = opts.parse_mode;
  if (opts.silent) payload.disable_notification = true;

  var response = UrlFetchApp.fetch(telegramApiUrl("sendMessage"), {
    method: "post",
    contentType: "application/json",
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var code = response.getResponseCode();
  var body = response.getContentText();
  if (code !== 200) {
    throw new Error("Telegram HTTP " + code + ": " + body);
  }
  return JSON.parse(body);
}

/** Convenience: HTML-formatted message. */
function sendTelegramHtml(html) {
  return sendTelegram(html, { parse_mode: "HTML" });
}

/**
 * One-shot chat-id discovery. NOTE: the CI bot also polls getUpdates, so this
 * can interfere with it. Prefer reading the chat id straight from the bot's
 * reply instead. Returns the first chat id seen, or null if no messages yet.
 */
function discoverChatId() {
  if (!BOT_TOKEN || BOT_TOKEN.indexOf("PASTE") === 0) {
    throw new Error("Set BOT_TOKEN first.");
  }
  var res = UrlFetchApp.fetch(telegramApiUrl("getUpdates") + "?timeout=10", {
    method: "get",
    muteHttpExceptions: true
  });
  var data = JSON.parse(res.getContentText());
  if (!data.ok) throw new Error("getUpdates failed: " + res.getContentText());
  for (var i = 0; i < data.result.length; i++) {
    var msg = data.result[i].message;
    if (msg && msg.chat && msg.chat.id) {
      return msg.chat.id; // paste this into CHAT_ID
    }
  }
  return null;
}

// ===================== EXAMPLES =====================

/** Smoke test — proves the wiring works end to end. */
function testSendTelegram() {
  var r = sendTelegram("اختبار من Google Apps Script ✓ — وصلت رسالة CELIA.");
  Logger.log(JSON.stringify(r));
}

/**
 * Example trigger: when a row is added to a sheet, notify Telegram.
 * Wire this via Extensions → Apps Script → Triggers (on form submit / on edit).
 */
function notifyOnNewRow(e) {
  try {
    var sheet = e.source.getActiveSheet();
    var row = e.range.getRow();
    var values = sheet.getRange(row, 1, 1, sheet.getLastColumn()).getValues()[0];
    var message = "📥 طلب جديد من CELIA:\n" + values.join(" | ");
    sendTelegram(message);
  } catch (err) {
    Logger.log("Telegram notify failed: " + err);
  }
}

/**
 * Example: HTTP endpoint so external systems can POST here and forward to
 * Telegram. Deploy as a Web App (Execute as: me, Access: anyone).
 */
function doPost(request) {
  try {
    var data = JSON.parse(request.postData.contents);
    var text = data.text || data.message || "(no text)";
    sendTelegram(text);
    return ContentService.createTextOutput("ok");
  } catch (err) {
    return ContentService.createTextOutput("error: " + err)
      .setMimeType(ContentService.MimeType.TEXT);
  }
}
