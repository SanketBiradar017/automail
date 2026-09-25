let generatedEmail = "";
let isSending = false;
let autoReturnTimer = null;
let composeAttachments = [];  // { filename, mime_type, contentBase64 }
let bulkAttachments = [];     // { filename, mime_type, contentBase64 }
let scheduleAttachments = []; // { filename, mime_type, contentBase64 }

const EMAIL_REGEX = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const CONTEXT_MAX_LENGTH = 600;


/* ---------- Toasts ---------- */

function toast(message, type = "info", duration = 4200) {

    const container = document.getElementById("toastContainer");

    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.innerHTML = `<span class="toast-dot"></span><span>${escapeHtml(message)}</span>`;

    container.appendChild(el);

    setTimeout(() => {
        el.classList.add("leaving");
        setTimeout(() => el.remove(), 200);
    }, duration);
}


function escapeHtml(value) {
    const div = document.createElement("div");
    div.innerText = value;
    return div.innerHTML;
}


/* ---------- Screen navigation ---------- */

const SCREEN_IDS = {
    form: "screenForm",
    preview: "screenPreview",
    success: "screenSuccess",
    bulk: "screenBulk",
    history: "screenHistory",
    schedule: "screenSchedule",
    followups: "screenFollowups"
};

let bulkEmailsList = [];


function showScreen(name) {

    Object.values(SCREEN_IDS).forEach(id => {
        document.getElementById(id).classList.remove("active");
    });

    document.getElementById(SCREEN_IDS[name]).classList.add("active");

    const card = document.querySelector(".card");
    if (card) card.classList.toggle("wide-card", name === "schedule" || name === "followups");
}


function startNewEmail() {
    clearTimeout(autoReturnTimer);
    resetForm();
    showScreen("form");
}


/* ---------- Button loading state ---------- */

function setButtonLoading(button, isLoading, loadingText, idleText) {

    const label = button.querySelector(".btn-label");

    button.disabled = isLoading;
    button.classList.toggle("loading", isLoading);

    if (label) {
        label.innerText = isLoading ? loadingText : idleText;
    }
}


/* ---------- Preview empty state ---------- */

function updatePreviewEmptyState() {

    const preview = document.getElementById("emailPreview");
    const emptyState = document.getElementById("previewEmptyState");

    emptyState.classList.toggle("hidden", preview.value.trim().length > 0);
}


/* ---------- Form reset ---------- */

function resetForm() {

    document.getElementById("recipient").value = "";
    document.getElementById("recipientName").value = "";
    document.getElementById("subject").value = "";
    document.getElementById("context").value = "";
    document.getElementById("ccInput").value = "";
    document.getElementById("bccInput").value = "";
    document.getElementById("ccBccFields").style.display = "none";
    document.getElementById("ccBccToggle").innerText = "+ Add CC / BCC";

    clearRecipientError();
    updateContextCounter();

    const preview = document.getElementById("emailPreview");
    preview.value = "";
    preview.disabled = true;

    generatedEmail = "";

    document.getElementById("copyBtn").disabled = true;
    document.getElementById("sendBtn").disabled = true;

    resetAttachments();

    document.getElementById("composeFollowupToggle").checked = false;
    document.getElementById("composeFollowupFields").style.display = "none";
    document.getElementById("composeFollowupWait").value = "48";
    document.getElementById("composeFollowupWaitUnit").value = "hours";
    document.getElementById("composeFollowupMax").value = "2";
    document.getElementById("composeFollowupInterval").value = "72";
    document.getElementById("composeFollowupIntervalUnit").value = "hours";

    updatePreviewEmptyState();
}


/* ---------- Follow-up config (shared by Compose + Schedule) ---------- */

function toggleFollowupFields(target) {
    const checkbox = document.getElementById(`${target}FollowupToggle`);
    const fields = document.getElementById(`${target}FollowupFields`);
    fields.style.display = checkbox.checked ? "block" : "none";
}


/* ---------- Hours/Days unit conversion for duration fields ---------- */

function durationToHours(inputId, unitId) {
    const value = parseInt(document.getElementById(inputId).value, 10) || 1;
    const unit = document.getElementById(unitId).value;
    return unit === "days" ? value * 24 : value;
}


function setDurationFromHours(inputId, unitId, hours) {
    const safeHours = hours || 1;
    const unitSelect = document.getElementById(unitId);
    const inputEl = document.getElementById(inputId);

    if (safeHours >= 24 && safeHours % 24 === 0) {
        inputEl.value = safeHours / 24;
        unitSelect.value = "days";
    } else {
        inputEl.value = safeHours;
        unitSelect.value = "hours";
    }
}


/* ---------- CC / BCC ---------- */

function toggleCcBcc() {
    const fields = document.getElementById("ccBccFields");
    const toggle = document.getElementById("ccBccToggle");
    const isHidden = fields.style.display === "none";

    fields.style.display = isHidden ? "block" : "none";
    toggle.innerText = isHidden ? "− Hide CC / BCC" : "+ Add CC / BCC";
}


/* ---------- Attachments ---------- */

const ATTACHMENT_TARGETS = {
    compose: { list: () => composeAttachments, inputId: "attachmentsInput", listId: "attachmentsList" },
    bulk: { list: () => bulkAttachments, inputId: "bulkAttachmentsInput", listId: "bulkAttachmentsList" },
    schedule: { list: () => scheduleAttachments, inputId: "scheduleAttachmentsInput", listId: "scheduleAttachmentsList" }
};


function resetAttachments() {
    composeAttachments = [];
    document.getElementById("attachmentsInput").value = "";
    renderAttachmentsList("compose");
}


function resetBulkAttachments() {
    bulkAttachments = [];
    document.getElementById("bulkAttachmentsInput").value = "";
    renderAttachmentsList("bulk");
}


function renderAttachmentsList(target) {
    const cfg = ATTACHMENT_TARGETS[target];
    const list = document.getElementById(cfg.listId);
    const items = cfg.list();

    list.innerHTML = items.map((att, i) => `
        <div class="attachment-item">
            <span class="attachment-name">📄 ${escapeHtml(att.filename)}</span>
            <button type="button" class="attachment-remove" onclick="removeAttachment('${target}', ${i})">✕</button>
        </div>
    `).join("");
}


function removeAttachment(target, index) {
    if (target === "compose") {
        composeAttachments.splice(index, 1);
    } else {
        bulkAttachments.splice(index, 1);
    }
    renderAttachmentsList(target);
}


function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const result = reader.result;
            const base64 = result.substring(result.indexOf(",") + 1);
            resolve(base64);
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
    });
}


async function handleAttachmentsChange() {
    const input = document.getElementById("attachmentsInput");
    const files = Array.from(input.files || []);

    for (const file of files) {
        try {
            const contentBase64 = await fileToBase64(file);
            composeAttachments.push({
                filename: file.name,
                mime_type: file.type || "application/octet-stream",
                contentBase64
            });
        } catch (error) {
            console.error("Attachment read error:", error);
            toast(`Failed to read file: ${file.name}`, "error");
        }
    }

    input.value = "";
    renderAttachmentsList("compose");
}


async function handleBulkAttachmentsChange() {
    const input = document.getElementById("bulkAttachmentsInput");
    const files = Array.from(input.files || []);

    for (const file of files) {
        try {
            const contentBase64 = await fileToBase64(file);
            bulkAttachments.push({
                filename: file.name,
                mime_type: file.type || "application/octet-stream",
                contentBase64
            });
        } catch (error) {
            console.error("Attachment read error:", error);
            toast(`Failed to read file: ${file.name}`, "error");
        }
    }

    input.value = "";
    renderAttachmentsList("bulk");
}


async function handleScheduleAttachmentsChange() {
    const input = document.getElementById("scheduleAttachmentsInput");
    const files = Array.from(input.files || []);

    for (const file of files) {
        try {
            const contentBase64 = await fileToBase64(file);
            scheduleAttachments.push({
                filename: file.name,
                mime_type: file.type || "application/octet-stream",
                contentBase64
            });
        } catch (error) {
            console.error("Attachment read error:", error);
            toast(`Failed to read file: ${file.name}`, "error");
        }
    }

    input.value = "";
    renderAttachmentsList("schedule");
}


function resetScheduleAttachments() {
    scheduleAttachments = [];
    document.getElementById("scheduleAttachmentsInput").value = "";
    renderAttachmentsList("schedule");
}


/* ---------- Field validation ---------- */

function validateRecipientField() {

    const input = document.getElementById("recipient");
    const value = input.value.trim();

    if (value && !EMAIL_REGEX.test(value)) {
        input.classList.add("invalid");
        document.getElementById("recipientHint").innerText = "Enter a valid email address.";
        return false;
    }

    clearRecipientError();
    return true;
}


function clearRecipientError() {
    document.getElementById("recipient").classList.remove("invalid");
    document.getElementById("recipientHint").innerText = "";
}


function updateContextCounter() {

    const context = document.getElementById("context");
    const counter = document.getElementById("contextCounter");

    counter.innerText = `${context.value.length} / ${CONTEXT_MAX_LENGTH}`;
}


/* ---------- Generate ---------- */

async function generateEmail() {

    const recipient = document.getElementById("recipient").value.trim();
    const recipientName = document.getElementById("recipientName").value.trim();
    const subject = document.getElementById("subject").value.trim();
    const context = document.getElementById("context").value.trim();

    if (!recipient || !recipientName || !subject || !context) {
        toast("Please fill in all required fields.", "error");
        return;
    }

    if (!validateRecipientField()) {
        toast("Please enter a valid recipient email.", "error");
        return;
    }

    const button = document.getElementById("generateBtn");
    setButtonLoading(button, true, "Generating...", "✦ Generate Email");


    try {

        const response = await fetch(
            "/api/email/generate",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    recipient,
                    recipient_name: recipientName,
                    subject,
                    context
                })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to generate email."));
        }

        generatedEmail = data.email;

        if (!generatedEmail) {
            throw new Error("Backend did not return generated email.");
        }

        const preview = document.getElementById("emailPreview");
        preview.value = generatedEmail;
        preview.disabled = false;

        document.getElementById("copyBtn").disabled = false;
        document.getElementById("sendBtn").disabled = false;

        updatePreviewEmptyState();

        showScreen("preview");

        toast("Email generated. Review or edit it before sending.", "success");


    } catch (error) {

        console.error("Generate email error:", error);
        toast(error.message, "error");

    } finally {

        setButtonLoading(button, false, "Generating...", "✦ Generate Email");
    }
}


/* ---------- Send ---------- */

async function sendEmail() {

    if (isSending) {
        return;
    }

    const recipient = document.getElementById("recipient").value.trim();
    const recipientName = document.getElementById("recipientName").value.trim();
    const subject = document.getElementById("subject").value.trim();
    const body = document.getElementById("emailPreview").value.trim();
    const cc = document.getElementById("ccInput").value.trim();
    const bcc = document.getElementById("bccInput").value.trim();
    const enableFollowup = document.getElementById("composeFollowupToggle").checked;

    if (!body) {
        toast("Generate an email first.", "error");
        return;
    }

    if (!recipient || !subject) {
        toast("Recipient and subject are required.", "error");
        return;
    }

    if (!validateRecipientField()) {
        toast("Please enter a valid recipient email.", "error");
        return;
    }

    isSending = true;

    const sendButton = document.getElementById("sendBtn");
    const copyButton = document.getElementById("copyBtn");

    setButtonLoading(sendButton, true, "Sending...", "Send via Gmail");
    copyButton.disabled = true;

    let sent = false;


    try {

        const response = await fetch(
            "/api/email/send",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    recipient,
                    recipient_name: recipientName,
                    subject,
                    body,
                    cc: cc || null,
                    bcc: bcc || null,
                    attachments: composeAttachments.length
                        ? composeAttachments.map(att => ({
                            filename: att.filename,
                            mime_type: att.mime_type,
                            content_base64: att.contentBase64
                        }))
                        : null,
                    enable_followup: enableFollowup,
                    followup_wait_hours: durationToHours("composeFollowupWait", "composeFollowupWaitUnit"),
                    followup_max: parseInt(document.getElementById("composeFollowupMax").value, 10) || 2,
                    followup_interval_hours: durationToHours("composeFollowupInterval", "composeFollowupIntervalUnit")
                })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to send email."));
        }

        sent = true;

        document.getElementById("successSubtitle").innerText = `Sent to ${recipient}.`;

        resetForm();

        showScreen("success");

        clearTimeout(autoReturnTimer);
        autoReturnTimer = setTimeout(() => showScreen("form"), 4000);

        toast("Email sent successfully!", "success");


    } catch (error) {

        console.error("Send email error:", error);
        toast(error.message, "error");

    } finally {

        isSending = false;

        setButtonLoading(sendButton, false, "Sending...", "Send via Gmail");

        // On success, resetForm() already put sendBtn/copyBtn back into
        // their initial disabled state - don't override that here.
        if (!sent) {
            sendButton.disabled = false;
            copyButton.disabled = false;
        }
    }
}


/* ---------- Copy ---------- */

function copyEmail() {

    const body = document.getElementById("emailPreview").value.trim();

    if (!body) {
        toast("Generate an email first.", "error");
        return;
    }

    navigator.clipboard.writeText(body)
        .then(() => toast("Email copied to clipboard!", "success"))
        .catch(() => toast("Failed to copy email.", "error"));
}


/* ---------- Gmail accounts (multi-account dropdown) ---------- */

let gmailAccountsCache = [];

async function refreshGmailAccounts() {
    const dot = document.getElementById("accountDot");
    const triggerText = document.getElementById("accountTriggerText");

    try {
        const response = await fetch("/api/gmail-accounts");
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to load Gmail accounts."));
        }

        gmailAccountsCache = data.accounts;
        renderAccountList();

        const active = gmailAccountsCache.find(a => a.is_active);

        if (!active) {
            dot.classList.add("disconnected");
            triggerText.innerText = gmailAccountsCache.length
                ? "Select account"
                : "No Gmail connected";
            return;
        }

        if (active.status === "needs_reconnect") {
            dot.classList.add("disconnected");
            triggerText.innerText = `${active.email} · Reconnect`;
        } else {
            dot.classList.remove("disconnected");
            triggerText.innerText = active.email;
        }

    } catch (error) {
        console.error("Gmail accounts error:", error);
        dot.classList.add("disconnected");
        triggerText.innerText = "Unable to load accounts";
    }
}


function renderAccountList() {
    const list = document.getElementById("accountList");

    if (!gmailAccountsCache.length) {
        list.innerHTML = `<div class="account-empty">No Gmail accounts connected yet.</div>`;
        return;
    }

    list.innerHTML = gmailAccountsCache.map(a => {
        const needsReconnect = a.status === "needs_reconnect";
        const label = a.display_name ? `${escapeHtml(a.display_name)} <span class="account-email">${escapeHtml(a.email)}</span>` : escapeHtml(a.email);

        return `
            <div class="account-row ${a.is_active ? "active" : ""}">
                <button
                    type="button"
                    class="account-row-main"
                    onclick="${needsReconnect ? `reconnectGmailAccount(${a.id})` : `activateGmailAccount(${a.id})`}"
                    ${a.is_active && !needsReconnect ? "disabled" : ""}
                >
                    <span class="account-row-dot ${needsReconnect ? "disconnected" : ""}"></span>
                    <span class="account-row-label">${label}</span>
                    ${a.is_active ? '<span class="account-active-badge">Active</span>' : ""}
                    ${needsReconnect ? '<span class="account-reconnect-badge">Reconnect</span>' : ""}
                </button>
                <button
                    type="button"
                    class="account-row-remove"
                    onclick="disconnectGmailAccount(${a.id}, '${escapeHtml(a.email).replace(/'/g, "\\'")}')"
                    title="Disconnect"
                >✕</button>
            </div>
        `;
    }).join("");
}


function toggleAccountMenu(forceClose = false) {
    const menu = document.getElementById("accountMenu");
    const isOpen = menu.style.display !== "none";

    if (forceClose || isOpen) {
        menu.style.display = "none";
    } else {
        menu.style.display = "block";
    }
}


document.addEventListener("click", (e) => {
    const dropdown = document.getElementById("accountDropdown");
    if (dropdown && !dropdown.contains(e.target)) {
        toggleAccountMenu(true);
    }
});


async function activateGmailAccount(accountId) {
    try {
        const response = await fetch(`/api/gmail-accounts/${accountId}/activate`, { method: "POST" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to switch account."));
        }

        toast(`Switched to ${data.account.email}.`, "success");
        toggleAccountMenu(true);
        await refreshGmailAccounts();

    } catch (error) {
        console.error("Activate account error:", error);
        toast(error.message, "error");
    }
}


async function addGmailAccount() {
    const btn = document.getElementById("addGmailBtn");
    btn.disabled = true;
    btn.innerText = "Waiting for sign-in...";

    toast("Complete sign-in in the browser window that just opened...", "info");

    try {
        const response = await fetch("/api/gmail-accounts/connect", { method: "POST" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to connect Gmail account."));
        }

        toast(`Connected ${data.account.email}.`, "success");
        await refreshGmailAccounts();

    } catch (error) {
        console.error("Add Gmail account error:", error);
        toast(error.message, "error");
    } finally {
        btn.disabled = false;
        btn.innerText = "+ Add Gmail";
    }
}


async function reconnectGmailAccount(accountId) {
    toast("Complete sign-in in the browser window that just opened...", "info");

    try {
        const response = await fetch(`/api/gmail-accounts/${accountId}/reconnect`, { method: "POST" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to reconnect Gmail account."));
        }

        toast(data.message || "Reconnected.", "success");
        await refreshGmailAccounts();

    } catch (error) {
        console.error("Reconnect account error:", error);
        toast(error.message, "error");
    }
}


async function disconnectGmailAccount(accountId, email) {
    if (!confirm(`Disconnect ${email}? You'll need to add it again to use it.`)) {
        return;
    }

    try {
        const response = await fetch(`/api/gmail-accounts/${accountId}`, { method: "DELETE" });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to disconnect account."));
        }

        toast("Gmail account disconnected.", "success");
        await refreshGmailAccounts();

    } catch (error) {
        console.error("Disconnect account error:", error);
        toast(error.message, "error");
    }
}


/* ---------- Errors ---------- */

function extractErrorMessage(data, fallback) {

    if (Array.isArray(data.detail)) {
        return data.detail.map(error => `${error.loc?.slice(-1)[0]}: ${error.msg}`).join(", ");
    }

    if (typeof data.detail === "string") {
        return data.detail;
    }

    return fallback;
}


/* ---------- Bulk email functions ---------- */

function switchBulkInputMode(mode) {
    try {
        const bulkTabBtns = document.querySelectorAll(".bulk-tab-btn");
        const pasteBtn = bulkTabBtns[0];
        const csvBtn = bulkTabBtns[1];
        const pasteMode = document.getElementById("pasteMode");
        const csvMode = document.getElementById("csvMode");

        if (!pasteBtn || !csvBtn || !pasteMode || !csvMode) {
            console.warn("Bulk input mode elements not found");
            return;
        }

        if (mode === "paste") {
            pasteBtn.classList.add("active");
            csvBtn.classList.remove("active");
            pasteMode.style.display = "block";
            csvMode.style.display = "none";
        } else {
            csvBtn.classList.add("active");
            pasteBtn.classList.remove("active");
            csvMode.style.display = "block";
            pasteMode.style.display = "none";
        }
    } catch (error) {
        console.error("Error switching bulk input mode:", error);
    }
}

function handleCsvUpload() {
    const file = document.getElementById("csvFile").files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
        const csv = e.target.result;
        const lines = csv.trim().split("\n");
        const emails = [];

        for (let i = 1; i < lines.length; i++) {
            const [email, name] = lines[i].split(",").map(s => s.trim());
            if (email) {
                emails.push({
                    email: email.toLowerCase(),
                    name: name || email.split("@")[0]
                });
            }
        }

        bulkEmailsList = emails;
        updateBulkRecipientCounter();
        toast(`Loaded ${emails.length} emails from CSV`, "success");
    };

    reader.readAsText(file);
}

function parsePastedEmails(text) {
    const lines = text.trim().split("\n");
    const emails = [];

    for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        if (trimmed.includes(",")) {
            const [email, name] = trimmed.split(",").map(s => s.trim());
            if (email && EMAIL_REGEX.test(email)) {
                emails.push({
                    email: email.toLowerCase(),
                    name: name || email.split("@")[0]
                });
            }
        } else if (EMAIL_REGEX.test(trimmed)) {
            emails.push({
                email: trimmed.toLowerCase(),
                name: trimmed.split("@")[0]
            });
        }
    }

    return emails;
}

function updateBulkRecipientCounter() {
    try {
        const counter = document.getElementById("recipientCounter");
        if (counter) {
            counter.innerText = `${bulkEmailsList.length} email${bulkEmailsList.length !== 1 ? "s" : ""}`;
        }
    } catch (error) {
        console.error("Error updating recipient counter:", error);
    }
}

function resetBulkForm() {
    try {
        const campaignName = document.getElementById("campaignName");
        const bulkSubject = document.getElementById("bulkSubject");
        const bulkBody = document.getElementById("bulkBody");
        const emailListInput = document.getElementById("emailListInput");
        const csvFile = document.getElementById("csvFile");
        const bulkResults = document.getElementById("bulkResults");

        if (campaignName) campaignName.value = "";
        if (bulkSubject) bulkSubject.value = "";
        if (bulkBody) bulkBody.value = "";
        if (emailListInput) emailListInput.value = "";
        if (csvFile) csvFile.value = "";

        bulkEmailsList = [];
        updateBulkRecipientCounter();
        resetBulkAttachments();

        if (bulkResults) bulkResults.style.display = "none";
        switchBulkInputMode("paste");
    } catch (error) {
        console.error("Error resetting bulk form:", error);
    }
}

async function generateBulkEmail() {
    const subject = document.getElementById("bulkSubject").value.trim();
    const bodyField = document.getElementById("bulkBody");
    const context = bodyField.value.trim();

    if (!subject) {
        toast("Enter a subject first.", "error");
        return;
    }
    if (!context) {
        toast("Type a quick description in the body field first, then generate.", "error");
        return;
    }

    const button = document.getElementById("bulkGenerateBtn");
    setButtonLoading(button, true, "Generating...", "✦ Generate with AI");

    try {
        const response = await fetch("/api/email/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                recipient: "placeholder@example.com",
                recipient_name: "{name}",
                subject,
                context
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to generate email."));
        }

        bodyField.value = data.email;
        toast("Email generated. Review or edit it before sending.", "success");

    } catch (error) {
        console.error("Generate bulk email error:", error);
        toast(error.message, "error");
    } finally {
        setButtonLoading(button, false, "Generating...", "✦ Generate with AI");
    }
}


function validateBulkEmails() {
    const emailListText = document.getElementById("emailListInput").value.trim();

    if (!emailListText) {
        toast("Please paste or upload an email list", "error");
        return;
    }

    const emails = parsePastedEmails(emailListText);

    if (emails.length === 0) {
        toast("No valid emails found. Check format: email@example.com or email@example.com,Name", "error");
        return;
    }

    if (emails.length > 500) {
        toast("Maximum 500 emails per campaign", "error");
        return;
    }

    bulkEmailsList = emails;
    updateBulkRecipientCounter();

    document.getElementById("sendBulkBtn").disabled = false;

    toast(`✓ Valid: ${emails.length} emails ready to send`, "success");
}

async function sendBulkEmails() {
    const campaignName = document.getElementById("campaignName").value.trim();
    const subject = document.getElementById("bulkSubject").value.trim();
    const bodyTemplate = document.getElementById("bulkBody").value.trim();

    if (!campaignName) {
        toast("Campaign name is required", "error");
        return;
    }

    if (!subject) {
        toast("Subject is required", "error");
        return;
    }

    if (!bodyTemplate) {
        toast("Email body is required", "error");
        return;
    }

    if (bulkEmailsList.length === 0) {
        toast("No emails to send. Click 'Validate' first.", "error");
        return;
    }

    const sendBtn = document.getElementById("sendBulkBtn");
    setButtonLoading(sendBtn, true, "Sending...", "📨 Send All");

    try {
        const emails = bulkEmailsList.map(item => ({
            recipient: item.email,
            recipient_name: item.name,
            subject: subject,
            body: bodyTemplate.replace(/{name}/g, item.name)
        }));

        const response = await fetch(
            "/api/email/send-bulk",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    campaign_name: campaignName,
                    emails: emails,
                    attachments: bulkAttachments.length
                        ? bulkAttachments.map(att => ({
                            filename: att.filename,
                            mime_type: att.mime_type,
                            content_base64: att.contentBase64
                        }))
                        : null
                })
            }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to send bulk emails."));
        }

        displayBulkResults(data);
        toast(`Bulk send complete: ${data.sent}/${data.total_emails} sent`, "success");

    } catch (error) {
        console.error("Bulk send error:", error);
        toast(error.message, "error");

    } finally {
        setButtonLoading(sendBtn, false, "Sending...", "📨 Send All");
    }
}

function displayBulkResults(data) {
    const resultsDiv = document.getElementById("bulkResults");
    const resultsList = document.getElementById("resultsList");

    let html = `
        <div class="results-summary">
            <div class="result-stat">
                <span class="result-label">Total</span>
                <span class="result-value">${data.total_emails}</span>
            </div>
            <div class="result-stat success">
                <span class="result-label">Sent</span>
                <span class="result-value">${data.sent}</span>
            </div>
            <div class="result-stat ${data.failed > 0 ? 'danger' : ''}">
                <span class="result-label">Failed</span>
                <span class="result-value">${data.failed}</span>
            </div>
        </div>
    `;

    if (data.results && data.results.length > 0) {
        html += `<div class="results-detail">`;
        html += data.results.map(r => `
            <div class="result-item ${r.status}">
                <span class="result-email">${escapeHtml(r.recipient)}</span>
                <span class="result-status ${r.status}">
                    ${r.status === 'sent' ? '✓ Sent' : '✗ Failed'}
                </span>
                ${r.error ? `<span class="result-error">${escapeHtml(r.error)}</span>` : ''}
            </div>
        `).join("");
        html += `</div>`;
    }

    resultsList.innerHTML = html;
    resultsDiv.style.display = "block";
}

/* ---------- Tab switching ---------- */

function switchTab(tab) {
    try {
        const navBtns = document.querySelectorAll(".nav-btn");
        navBtns.forEach(btn => btn.classList.remove("active"));

        if (tab === "compose") {
            showScreen("form");
            if (navBtns[0]) navBtns[0].classList.add("active");
        } else if (tab === "bulk") {
            showScreen("bulk");
            if (navBtns[1]) navBtns[1].classList.add("active");
            try {
                resetBulkForm();
            } catch (e) {
                console.error("Error resetting bulk form:", e);
            }
        } else if (tab === "history") {
            showScreen("history");
            if (navBtns[2]) navBtns[2].classList.add("active");
            try {
                loadCampaigns();
            } catch (e) {
                console.error("Error loading campaigns:", e);
            }
        } else if (tab === "schedule") {
            showScreen("schedule");
            if (navBtns[3]) navBtns[3].classList.add("active");
            try {
                loadSchedules();
            } catch (e) {
                console.error("Error loading schedules:", e);
            }
        } else if (tab === "followups") {
            showScreen("followups");
            if (navBtns[4]) navBtns[4].classList.add("active");
            try {
                loadFollowups();
            } catch (e) {
                console.error("Error loading follow-ups:", e);
            }
        }
    } catch (error) {
        console.error("Error switching tab:", error);
    }
}


/* ---------- History ---------- */

async function loadCampaigns() {
    const list = document.getElementById("campaignsList");
    list.innerHTML = '<div class="loading-state"><p>Loading campaigns...</p></div>';

    try {
        const response = await fetch("/api/email/campaigns");
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to load campaigns."));
        }

        if (!data.campaigns || data.campaigns.length === 0) {
            list.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">📧</div>
                    <h3>No campaigns yet</h3>
                    <p>Emails you send will appear here.</p>
                </div>
            `;
            return;
        }

        list.innerHTML = data.campaigns.map(campaign => `
            <div class="campaign-card">
                <div class="campaign-info">
                    <h3>${escapeHtml(campaign.name)}</h3>
                    <p class="campaign-subject">Subject: ${escapeHtml(campaign.subject)}</p>
                    <p class="campaign-meta">
                        <span>${campaign.email_count} emails</span>
                        <span>•</span>
                        <span class="sent-count">${campaign.sent_count} sent</span>
                        ${campaign.failed_count > 0 ? `<span>•</span><span class="failed-count">${campaign.failed_count} failed</span>` : ''}
                    </p>
                    <p class="campaign-date">${formatDate(campaign.created_at)}</p>
                </div>
                <div class="campaign-actions">
                    <button class="btn-expand" onclick="toggleCampaignDetails(${campaign.id})">
                        ▼
                    </button>
                    <button class="btn-delete" onclick="deleteCampaign(${campaign.id}, '${escapeHtml(campaign.name)}')">
                        🗑️
                    </button>
                </div>
                <div class="campaign-details" id="details-${campaign.id}" style="display: none;">
                    <div class="emails-list" id="emails-${campaign.id}">
                        <p>Loading emails...</p>
                    </div>
                </div>
            </div>
        `).join("");

    } catch (error) {
        console.error("Load campaigns error:", error);
        list.innerHTML = `
            <div class="error-state">
                <p>Error loading campaigns: ${escapeHtml(error.message)}</p>
            </div>
        `;
        toast(error.message, "error");
    }
}


async function toggleCampaignDetails(campaignId) {
    const details = document.getElementById(`details-${campaignId}`);
    const isOpen = details.style.display !== "none";

    if (isOpen) {
        details.style.display = "none";
        return;
    }

    details.style.display = "block";

    try {
        const response = await fetch(`/api/email/campaigns/${campaignId}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to load campaign details."));
        }

        const emailsList = document.getElementById(`emails-${campaignId}`);
        const emails = data.campaign.emails;

        if (!emails || emails.length === 0) {
            emailsList.innerHTML = "<p>No emails in this campaign.</p>";
            return;
        }

        emailsList.innerHTML = emails.map(email => `
            <div class="email-item ${email.status}">
                <div class="email-row" onclick="toggleEmailContent(${email.id})">
                    <div class="email-info">
                        <p class="email-recipient">${escapeHtml(email.recipient_email)}</p>
                        <p class="email-name">${escapeHtml(email.recipient_name)}</p>
                        <p class="email-status">
                            <span class="status-badge ${email.status}">${email.status.toUpperCase()}</span>
                            ${email.sent_at ? `<span class="email-time">${formatDate(email.sent_at)}</span>` : ''}
                        </p>
                        ${email.error ? `<p class="email-error">Error: ${escapeHtml(email.error)}</p>` : ''}
                    </div>
                    <button class="btn-delete-email" onclick="event.stopPropagation(); deleteEmail(${email.id})">🗑️</button>
                </div>
                <div class="email-content" id="content-${email.id}" style="display: none;">
                    <p class="email-content-subject"><strong>Subject:</strong> ${escapeHtml(email.subject || "(no subject)")}</p>
                    ${email.cc ? `<p class="email-content-subject"><strong>CC:</strong> ${escapeHtml(email.cc)}</p>` : ''}
                    ${email.bcc ? `<p class="email-content-subject"><strong>BCC:</strong> ${escapeHtml(email.bcc)}</p>` : ''}
                    ${email.attachment_names && email.attachment_names.length ? `<p class="email-content-subject"><strong>Attachments:</strong> ${escapeHtml(email.attachment_names.join(", "))}</p>` : ''}
                    <pre class="email-content-body">${escapeHtml(email.body || "(empty body)")}</pre>
                </div>
            </div>
        `).join("");

    } catch (error) {
        console.error("Load campaign details error:", error);
        const emailsList = document.getElementById(`emails-${campaignId}`);
        emailsList.innerHTML = `<p>Error: ${escapeHtml(error.message)}</p>`;
        toast(error.message, "error");
    }
}


function toggleEmailContent(emailId) {
    const content = document.getElementById(`content-${emailId}`);
    if (!content) return;
    content.style.display = content.style.display === "none" ? "block" : "none";
}


async function deleteCampaign(campaignId, campaignName) {
    if (!confirm(`Delete campaign "${campaignName}" and all its emails?`)) {
        return;
    }

    try {
        const response = await fetch(`/api/email/campaigns/${campaignId}`, {
            method: "DELETE"
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to delete campaign."));
        }

        toast("Campaign deleted successfully", "success");
        loadCampaigns();

    } catch (error) {
        console.error("Delete campaign error:", error);
        toast(error.message, "error");
    }
}


async function deleteEmail(emailId) {
    if (!confirm("Delete this email from history?")) {
        return;
    }

    try {
        const response = await fetch(`/api/email/emails/${emailId}`, {
            method: "DELETE"
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to delete email."));
        }

        toast("Email deleted successfully", "success");
        loadCampaigns();

    } catch (error) {
        console.error("Delete email error:", error);
        toast(error.message, "error");
    }
}


function formatDate(dateString) {
    if (!dateString) return "N/A";

    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit"
    });
}


/* ---------- Schedule: state ---------- */

let editingScheduleId = null;
let currentScheduleStatusFilter = "";
let currentScheduleView = "list";
let calendarMonthCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let allSchedulesCache = [];


function populateTimezoneSelect() {
    const select = document.getElementById("scheduleTimezone");
    if (!select) return;

    let zones;
    try {
        zones = Intl.supportedValuesOf("timeZone");
    } catch (e) {
        zones = ["UTC"];
    }

    const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    if (!zones.includes(browserTz)) zones.unshift(browserTz);

    select.innerHTML = zones.map(z => `<option value="${z}">${z}</option>`).join("");
    select.value = browserTz;
}


/* ---------- Schedule: form panel ---------- */

function toggleScheduleCcBcc() {
    const fields = document.getElementById("scheduleCcBccFields");
    const toggle = document.getElementById("scheduleCcBccToggle");
    const isHidden = fields.style.display === "none";

    fields.style.display = isHidden ? "block" : "none";
    toggle.innerText = isHidden ? "− Hide CC / BCC" : "+ Add CC / BCC";
}


function handleRecurrenceTypeChange() {
    const type = document.getElementById("recurrenceType").value;
    document.getElementById("recurrenceCustomFields").style.display = type === "custom" ? "block" : "none";
    document.getElementById("recurrenceEndFields").style.display = type === "none" ? "none" : "block";
}


async function generateScheduleEmail() {
    const recipient = document.getElementById("scheduleRecipient").value.trim();
    const recipientName = document.getElementById("scheduleRecipientName").value.trim() || "there";
    const subject = document.getElementById("scheduleSubject").value.trim();
    const bodyField = document.getElementById("scheduleBody");
    const context = bodyField.value.trim();

    if (!recipient || !EMAIL_REGEX.test(recipient)) {
        toast("Enter a valid recipient email first.", "error");
        return;
    }
    if (!subject) {
        toast("Enter a subject first.", "error");
        return;
    }
    if (!context) {
        toast("Type a quick description in the body field first, then generate.", "error");
        return;
    }

    const button = document.getElementById("scheduleGenerateBtn");
    setButtonLoading(button, true, "Generating...", "✦ Generate with AI");

    try {
        const response = await fetch("/api/email/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                recipient,
                recipient_name: recipientName,
                subject,
                context
            })
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to generate email."));
        }

        bodyField.value = data.email;
        toast("Email generated. Review or edit it before scheduling.", "success");

    } catch (error) {
        console.error("Generate schedule email error:", error);
        toast(error.message, "error");
    } finally {
        setButtonLoading(button, false, "Generating...", "✦ Generate with AI");
    }
}


function resetScheduleForm() {
    editingScheduleId = null;
    document.getElementById("scheduleFormTitle").innerText = "New Scheduled Email";
    document.getElementById("scheduleRecipient").value = "";
    document.getElementById("scheduleRecipientName").value = "";
    document.getElementById("scheduleCc").value = "";
    document.getElementById("scheduleBcc").value = "";
    document.getElementById("scheduleCcBccFields").style.display = "none";
    document.getElementById("scheduleCcBccToggle").innerText = "+ Add CC / BCC";
    document.getElementById("scheduleSubject").value = "";
    document.getElementById("scheduleBody").value = "";
    document.getElementById("scheduleDateTime").value = "";
    document.getElementById("recurrenceType").value = "none";
    document.getElementById("recurrenceIntervalDays").value = "2";
    document.getElementById("recurrenceEndDate").value = "";
    handleRecurrenceTypeChange();
    resetScheduleAttachments();

    document.getElementById("scheduleFollowupToggle").checked = false;
    document.getElementById("scheduleFollowupFields").style.display = "none";
    document.getElementById("scheduleFollowupWait").value = "48";
    document.getElementById("scheduleFollowupWaitUnit").value = "hours";
    document.getElementById("scheduleFollowupMax").value = "2";
    document.getElementById("scheduleFollowupInterval").value = "72";
    document.getElementById("scheduleFollowupIntervalUnit").value = "hours";

    const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    const tzSelect = document.getElementById("scheduleTimezone");
    if (tzSelect) tzSelect.value = browserTz;
}


function openScheduleForm(scheduleId = null) {
    resetScheduleForm();

    if (scheduleId) {
        const existing = allSchedulesCache.find(s => s.id === scheduleId);
        if (existing) fillScheduleForm(existing);
    }

    document.getElementById("scheduleFormOverlay").style.display = "flex";
}


function closeScheduleForm() {
    document.getElementById("scheduleFormOverlay").style.display = "none";
}


function fillScheduleForm(s) {
    editingScheduleId = s.id;
    document.getElementById("scheduleFormTitle").innerText = "Edit Scheduled Email";
    document.getElementById("scheduleRecipient").value = s.recipient_email || "";
    document.getElementById("scheduleRecipientName").value = s.recipient_name || "";

    if (s.cc || s.bcc) {
        document.getElementById("scheduleCc").value = s.cc || "";
        document.getElementById("scheduleBcc").value = s.bcc || "";
        document.getElementById("scheduleCcBccFields").style.display = "block";
        document.getElementById("scheduleCcBccToggle").innerText = "− Hide CC / BCC";
    }

    document.getElementById("scheduleSubject").value = s.subject || "";
    document.getElementById("scheduleBody").value = s.body || "";
    document.getElementById("scheduleTimezone").value = s.timezone || "UTC";

    if (s.scheduled_at_utc) {
        document.getElementById("scheduleDateTime").value = utcToLocalInputValue(s.scheduled_at_utc, s.timezone);
    }

    document.getElementById("recurrenceType").value = s.recurrence_type || "none";
    document.getElementById("recurrenceIntervalDays").value = s.recurrence_interval_days || 2;
    if (s.recurrence_end_at) {
        document.getElementById("recurrenceEndDate").value = s.recurrence_end_at.substring(0, 10);
    }
    handleRecurrenceTypeChange();

    document.getElementById("scheduleFollowupToggle").checked = !!s.enable_followup;
    document.getElementById("scheduleFollowupFields").style.display = s.enable_followup ? "block" : "none";
    setDurationFromHours("scheduleFollowupWait", "scheduleFollowupWaitUnit", s.followup_wait_hours || 48);
    document.getElementById("scheduleFollowupMax").value = s.followup_max || 2;
    setDurationFromHours("scheduleFollowupInterval", "scheduleFollowupIntervalUnit", s.followup_interval_hours || 72);

    // Existing attachments are shown as read-only names; re-upload is required
    // to change them since raw file content isn't sent back down from the server.
    if (s.attachment_names && s.attachment_names.length) {
        const list = document.getElementById("scheduleAttachmentsList");
        list.innerHTML = s.attachment_names.map(name => `
            <div class="attachment-item">
                <span class="attachment-name">📄 ${escapeHtml(name)} <em>(existing)</em></span>
            </div>
        `).join("");
    }
}


function utcToLocalInputValue(utcIso, tzName) {
    const date = new Date(utcIso);
    const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: tzName || "UTC",
        year: "numeric", month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", hour12: false
    }).formatToParts(date);

    const get = type => parts.find(p => p.type === type)?.value;
    return `${get("year")}-${get("month")}-${get("day")}T${get("hour")}:${get("minute")}`;
}


async function saveScheduleForm(asDraft) {
    const recipient = document.getElementById("scheduleRecipient").value.trim();
    const recipientName = document.getElementById("scheduleRecipientName").value.trim();
    const cc = document.getElementById("scheduleCc").value.trim();
    const bcc = document.getElementById("scheduleBcc").value.trim();
    const subject = document.getElementById("scheduleSubject").value.trim();
    const body = document.getElementById("scheduleBody").value.trim();
    const timezone = document.getElementById("scheduleTimezone").value;
    const scheduledAt = document.getElementById("scheduleDateTime").value;
    const recurrenceType = document.getElementById("recurrenceType").value;
    const recurrenceIntervalDays = parseInt(document.getElementById("recurrenceIntervalDays").value, 10) || 1;
    const recurrenceEndDate = document.getElementById("recurrenceEndDate").value;
    const enableFollowup = document.getElementById("scheduleFollowupToggle").checked;
    const followupWaitHours = durationToHours("scheduleFollowupWait", "scheduleFollowupWaitUnit");
    const followupMax = parseInt(document.getElementById("scheduleFollowupMax").value, 10) || 2;
    const followupIntervalHours = durationToHours("scheduleFollowupInterval", "scheduleFollowupIntervalUnit");

    if (!recipient || !EMAIL_REGEX.test(recipient)) {
        toast("Please enter a valid recipient email.", "error");
        return;
    }
    if (!subject || !body) {
        toast("Subject and body are required.", "error");
        return;
    }
    if (!asDraft && !scheduledAt) {
        toast("Please pick a date and time, or save as draft.", "error");
        return;
    }

    const payload = {
        recipient,
        recipient_name: recipientName,
        cc: cc || null,
        bcc: bcc || null,
        subject,
        body,
        timezone,
        scheduled_at: scheduledAt || null,
        recurrence: {
            type: recurrenceType,
            interval_days: recurrenceType === "custom" ? recurrenceIntervalDays : null,
            end_at: recurrenceEndDate ? `${recurrenceEndDate}T23:59:00` : null
        },
        save_as_draft: asDraft,
        enable_followup: enableFollowup,
        followup_wait_hours: followupWaitHours,
        followup_max: followupMax,
        followup_interval_hours: followupIntervalHours
    };

    if (scheduleAttachments.length) {
        payload.attachments = scheduleAttachments.map(att => ({
            filename: att.filename,
            mime_type: att.mime_type,
            content_base64: att.contentBase64
        }));
    }

    const submitBtn = document.getElementById("scheduleSubmitBtn");
    setButtonLoading(submitBtn, true, "Saving...", "🗓 Schedule Email");

    try {
        const url = editingScheduleId ? `/api/schedule/${editingScheduleId}` : "/api/schedule";
        const method = editingScheduleId ? "PUT" : "POST";

        const response = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to save scheduled email."));
        }

        toast(asDraft ? "Saved as draft." : "Email scheduled!", "success");
        closeScheduleForm();
        loadSchedules(currentScheduleStatusFilter);

    } catch (error) {
        console.error("Save schedule error:", error);
        toast(error.message, "error");
    } finally {
        setButtonLoading(submitBtn, false, "Saving...", "🗓 Schedule Email");
    }
}


/* ---------- Schedule: list ---------- */

async function loadSchedules(status = currentScheduleStatusFilter) {
    currentScheduleStatusFilter = status || "";

    const listEl = document.getElementById("scheduleList");

    try {
        const query = status ? `?status=${encodeURIComponent(status)}` : "";
        const response = await fetch(`/api/schedule${query}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to load scheduled emails."));
        }

        allSchedulesCache = data.schedules;

        if (currentScheduleView === "list") {
            renderScheduleList(data.schedules);
        } else {
            renderCalendar();
        }

    } catch (error) {
        console.error("Load schedules error:", error);
        if (listEl) listEl.innerHTML = `<p class="error-state">Error: ${escapeHtml(error.message)}</p>`;
    }
}


function renderScheduleList(schedules) {
    const listEl = document.getElementById("scheduleList");

    if (!schedules || schedules.length === 0) {
        listEl.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🗓</div>
                <h3>No scheduled emails</h3>
                <p>Emails you schedule will appear here.</p>
            </div>
        `;
        return;
    }

    listEl.innerHTML = schedules.map(scheduleCardHtml).join("");
}


function toggleDetails(domId) {
    const el = document.getElementById(domId);
    if (!el) return;
    el.style.display = el.style.display === "none" ? "block" : "none";
}


function scheduleCardHtml(s) {
    const timeLabel = s.scheduled_at_utc
        ? formatScheduleDateTime(s.scheduled_at_utc, s.timezone)
        : "Not scheduled";

    const recurrenceLabel = s.is_recurring
        ? `<span class="recurrence-badge">🔁 ${s.recurrence_type}</span>`
        : "";

    const detailsId = `schedule-details-${s.id}`;

    const actions = [];
    actions.push(`<button class="btn-expand" onclick="toggleDetails('${detailsId}')">View</button>`);
    if (s.status !== "sent" && s.status !== "cancelled") {
        actions.push(`<button class="btn-expand" onclick="openScheduleForm(${s.id})">Edit</button>`);
    }
    if (s.status === "scheduled" || s.status === "draft") {
        actions.push(`<button class="btn-delete" onclick="cancelScheduleItem(${s.id})">Cancel</button>`);
    }
    if (s.status === "failed") {
        actions.push(`<button class="btn-expand" onclick="retryScheduleItem(${s.id})">Retry</button>`);
    }
    actions.push(`<button class="btn-expand" onclick="duplicateScheduleItem(${s.id})">Duplicate</button>`);
    actions.push(`<button class="btn-delete" onclick="deleteScheduleItem(${s.id})">🗑️</button>`);

    return `
        <div class="campaign-card schedule-card">
            <div class="campaign-info">
                <p class="campaign-subject">${escapeHtml(s.subject)} ${recurrenceLabel}</p>
                <p class="campaign-meta">
                    <span>To: ${escapeHtml(s.recipient_email)}</span>
                    <span class="status-badge ${s.status}">${s.status.toUpperCase()}</span>
                </p>
                <p class="campaign-date">🕒 ${escapeHtml(timeLabel)} (${escapeHtml(s.timezone)})</p>
                ${s.last_error ? `<p class="email-error">Error: ${escapeHtml(s.last_error)}</p>` : ''}
                <div class="email-content" id="${detailsId}" style="display: none;">
                    ${s.cc ? `<p class="email-content-subject"><strong>CC:</strong> ${escapeHtml(s.cc)}</p>` : ''}
                    ${s.bcc ? `<p class="email-content-subject"><strong>BCC:</strong> ${escapeHtml(s.bcc)}</p>` : ''}
                    ${s.attachment_names && s.attachment_names.length ? `<p class="email-content-subject"><strong>Attachments:</strong> ${escapeHtml(s.attachment_names.join(", "))}</p>` : ''}
                    <pre class="email-content-body">${escapeHtml(s.body || "(empty body)")}</pre>
                </div>
            </div>
            <div class="campaign-actions">${actions.join("")}</div>
        </div>
    `;
}


function formatScheduleDateTime(utcIso, tzName) {
    const date = new Date(utcIso);
    return new Intl.DateTimeFormat("en-US", {
        timeZone: tzName || "UTC",
        dateStyle: "medium",
        timeStyle: "short"
    }).format(date);
}


async function cancelScheduleItem(id) {
    if (!confirm("Cancel this scheduled email?")) return;

    try {
        const response = await fetch(`/api/schedule/${id}/cancel`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to cancel."));

        toast("Schedule cancelled.", "success");
        loadSchedules(currentScheduleStatusFilter);
    } catch (error) {
        console.error("Cancel schedule error:", error);
        toast(error.message, "error");
    }
}


async function retryScheduleItem(id) {
    try {
        const response = await fetch(`/api/schedule/${id}/retry`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to retry."));

        toast(data.schedule.status === "sent" ? "Resent successfully!" : "Retry failed again.", data.schedule.status === "sent" ? "success" : "error");
        loadSchedules(currentScheduleStatusFilter);
    } catch (error) {
        console.error("Retry schedule error:", error);
        toast(error.message, "error");
    }
}


async function duplicateScheduleItem(id) {
    try {
        const response = await fetch(`/api/schedule/${id}/duplicate`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to duplicate."));

        toast("Duplicated as a new draft.", "success");
        loadSchedules(currentScheduleStatusFilter);
    } catch (error) {
        console.error("Duplicate schedule error:", error);
        toast(error.message, "error");
    }
}


async function deleteScheduleItem(id) {
    if (!confirm("Permanently delete this scheduled email?")) return;

    try {
        const response = await fetch(`/api/schedule/${id}`, { method: "DELETE" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to delete."));

        toast("Deleted.", "success");
        loadSchedules(currentScheduleStatusFilter);
    } catch (error) {
        console.error("Delete schedule error:", error);
        toast(error.message, "error");
    }
}


/* ---------- Schedule: view toggle + calendar ---------- */

function setScheduleView(view) {
    currentScheduleView = view;

    document.getElementById("scheduleViewListBtn").classList.toggle("active", view === "list");
    document.getElementById("scheduleViewCalendarBtn").classList.toggle("active", view === "calendar");
    document.getElementById("scheduleListView").style.display = view === "list" ? "block" : "none";
    document.getElementById("scheduleCalendarView").style.display = view === "calendar" ? "block" : "none";

    if (view === "list") {
        renderScheduleList(allSchedulesCache);
    } else {
        renderCalendar();
    }
}


function shiftCalendarMonth(delta) {
    calendarMonthCursor = new Date(calendarMonthCursor.getFullYear(), calendarMonthCursor.getMonth() + delta, 1);
    renderCalendar();
}


function renderCalendar() {
    const grid = document.getElementById("calendarGrid");
    const label = document.getElementById("calendarMonthLabel");

    const year = calendarMonthCursor.getFullYear();
    const month = calendarMonthCursor.getMonth();

    label.innerText = calendarMonthCursor.toLocaleDateString("en-US", { month: "long", year: "numeric" });

    const firstDay = new Date(year, month, 1);
    const startOffset = firstDay.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const itemsByDate = {};
    allSchedulesCache.forEach(s => {
        if (!s.scheduled_at_utc) return;
        const local = new Date(s.scheduled_at_utc);
        const key = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, "0")}-${String(local.getDate()).padStart(2, "0")}`;
        if (!itemsByDate[key]) itemsByDate[key] = [];
        itemsByDate[key].push(s);
    });

    const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    let html = dayNames.map(d => `<div class="calendar-weekday">${d}</div>`).join("");

    for (let i = 0; i < startOffset; i++) {
        html += `<div class="calendar-cell empty"></div>`;
    }

    for (let day = 1; day <= daysInMonth; day++) {
        const key = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
        const items = itemsByDate[key] || [];
        const chips = items.slice(0, 3).map(s => `<div class="calendar-chip ${s.status}">${escapeHtml(s.subject)}</div>`).join("");
        const more = items.length > 3 ? `<div class="calendar-more">+${items.length - 3} more</div>` : "";

        html += `
            <div class="calendar-cell" onclick="showCalendarDayDetail('${key}')">
                <div class="calendar-date">${day}</div>
                ${chips}${more}
            </div>
        `;
    }

    grid.innerHTML = html;
    document.getElementById("calendarDayDetail").style.display = "none";
}


function showCalendarDayDetail(dateKey) {
    const items = allSchedulesCache.filter(s => {
        if (!s.scheduled_at_utc) return false;
        const local = new Date(s.scheduled_at_utc);
        const key = `${local.getFullYear()}-${String(local.getMonth() + 1).padStart(2, "0")}-${String(local.getDate()).padStart(2, "0")}`;
        return key === dateKey;
    });

    const detail = document.getElementById("calendarDayDetail");

    if (items.length === 0) {
        detail.style.display = "none";
        return;
    }

    detail.innerHTML = `<h4>${dateKey}</h4>` + items.map(scheduleCardHtml).join("");
    detail.style.display = "block";
}


/* ---------- Follow-ups dashboard ---------- */

let currentFollowupStatusFilter = "";
let editingFollowupId = null;

async function loadFollowups(status = currentFollowupStatusFilter) {
    currentFollowupStatusFilter = status || "";
    const listEl = document.getElementById("followupList");

    try {
        const query = status ? `?status=${encodeURIComponent(status)}` : "";
        const response = await fetch(`/api/followups${query}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to load follow-ups."));
        }

        renderFollowupList(data.followups);

    } catch (error) {
        console.error("Load follow-ups error:", error);
        if (listEl) listEl.innerHTML = `<p class="error-state">Error: ${escapeHtml(error.message)}</p>`;
    }
}


function renderFollowupList(followups) {
    const listEl = document.getElementById("followupList");

    if (!followups || followups.length === 0) {
        listEl.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">🔁</div>
                <h3>No follow-up automations yet</h3>
                <p>Enable follow-ups when composing or scheduling an email to see them here.</p>
            </div>
        `;
        return;
    }

    listEl.innerHTML = followups.map(followupCardHtml).join("");
}


function followupCardHtml(f) {
    const nextLabel = f.next_action_at
        ? formatScheduleDateTime(f.next_action_at, Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC")
        : null;

    const detailsId = `followup-details-${f.id}`;

    const actions = [];
    actions.push(`<button class="btn-expand" onclick="toggleDetails('${detailsId}')">View</button>`);
    if (f.status === "waiting" || f.status === "due") {
        actions.push(`<button class="btn-expand" onclick="pauseFollowupItem(${f.id})">Pause</button>`);
    }
    if (f.status === "stopped") {
        actions.push(`<button class="btn-expand" onclick="resumeFollowupItem(${f.id})">Resume</button>`);
    }
    if (f.status === "failed") {
        actions.push(`<button class="btn-expand" onclick="retryFollowupItem(${f.id})">Retry</button>`);
    }
    if (f.status !== "cancelled" && f.status !== "replied") {
        actions.push(`<button class="btn-expand" onclick="openFollowupEdit(${f.id})">Edit</button>`);
        actions.push(`<button class="btn-delete" onclick="cancelFollowupItem(${f.id})">Cancel</button>`);
    }
    actions.push(`<button class="btn-delete" onclick="deleteFollowupItem(${f.id})">🗑️</button>`);

    const previousHtml = (f.previous_followup_bodies || []).map((body, i) => `
        <p class="email-content-subject"><strong>Follow-up #${i + 1}:</strong></p>
        <pre class="email-content-body">${escapeHtml(body)}</pre>
    `).join("");

    return `
        <div class="campaign-card schedule-card" id="followup-card-${f.id}">
            <div class="campaign-info">
                <p class="campaign-subject">${escapeHtml(f.original_subject)}</p>
                <p class="campaign-meta">
                    <span>To: ${escapeHtml(f.recipient_email)}</span>
                    <span class="status-badge ${f.status}">${f.status.toUpperCase()}</span>
                </p>
                <p class="campaign-date">
                    Follow-up ${f.follow_ups_sent}/${f.max_follow_ups}
                    ${nextLabel && (f.status === "waiting" || f.status === "due") ? ` · Next: ${escapeHtml(nextLabel)}` : ""}
                </p>
                ${f.last_error ? `<p class="email-error">Error: ${escapeHtml(f.last_error)}</p>` : ''}
                <div class="email-content" id="${detailsId}" style="display: none;">
                    <p class="email-content-subject"><strong>Original email:</strong></p>
                    <pre class="email-content-body">${escapeHtml(f.original_body || "(empty body)")}</pre>
                    ${previousHtml}
                </div>
            </div>
            <div class="campaign-actions">${actions.join("")}</div>
        </div>
    `;
}


async function pauseFollowupItem(id) {
    try {
        const response = await fetch(`/api/followups/${id}/pause`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to pause."));
        toast("Follow-up paused.", "success");
        loadFollowups(currentFollowupStatusFilter);
    } catch (error) {
        console.error("Pause follow-up error:", error);
        toast(error.message, "error");
    }
}


async function resumeFollowupItem(id) {
    try {
        const response = await fetch(`/api/followups/${id}/resume`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to resume."));
        toast("Follow-up resumed.", "success");
        loadFollowups(currentFollowupStatusFilter);
    } catch (error) {
        console.error("Resume follow-up error:", error);
        toast(error.message, "error");
    }
}


async function retryFollowupItem(id) {
    try {
        const response = await fetch(`/api/followups/${id}/retry`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to retry."));
        toast("Retry attempted.", "success");
        loadFollowups(currentFollowupStatusFilter);
    } catch (error) {
        console.error("Retry follow-up error:", error);
        toast(error.message, "error");
    }
}


async function cancelFollowupItem(id) {
    if (!confirm("Cancel this follow-up automation?")) return;

    try {
        const response = await fetch(`/api/followups/${id}/cancel`, { method: "POST" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to cancel."));
        toast("Follow-up cancelled.", "success");
        loadFollowups(currentFollowupStatusFilter);
    } catch (error) {
        console.error("Cancel follow-up error:", error);
        toast(error.message, "error");
    }
}


async function deleteFollowupItem(id) {
    if (!confirm("Permanently delete this follow-up automation?")) return;

    try {
        const response = await fetch(`/api/followups/${id}`, { method: "DELETE" });
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to delete."));
        toast("Deleted.", "success");
        loadFollowups(currentFollowupStatusFilter);
    } catch (error) {
        console.error("Delete follow-up error:", error);
        toast(error.message, "error");
    }
}


async function openFollowupEdit(id) {
    try {
        const response = await fetch(`/api/followups/${id}`);
        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to load follow-up."));

        const f = data.followup;
        editingFollowupId = f.id;
        setDurationFromHours("followupEditWait", "followupEditWaitUnit", f.wait_hours);
        document.getElementById("followupEditMax").value = f.max_follow_ups;
        setDurationFromHours("followupEditInterval", "followupEditIntervalUnit", f.interval_hours);
        document.getElementById("followupEditOverlay").style.display = "flex";

    } catch (error) {
        console.error("Open follow-up edit error:", error);
        toast(error.message, "error");
    }
}


function closeFollowupEdit() {
    editingFollowupId = null;
    document.getElementById("followupEditOverlay").style.display = "none";
}


async function saveFollowupEdit() {
    if (!editingFollowupId) return;

    const waitHours = durationToHours("followupEditWait", "followupEditWaitUnit");
    const maxFollowUps = parseInt(document.getElementById("followupEditMax").value, 10);
    const intervalHours = durationToHours("followupEditInterval", "followupEditIntervalUnit");

    const button = document.getElementById("followupEditSaveBtn");
    setButtonLoading(button, true, "Saving...", "Save Changes");

    try {
        const response = await fetch(`/api/followups/${editingFollowupId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                wait_hours: waitHours,
                max_follow_ups: maxFollowUps,
                interval_hours: intervalHours
            })
        });

        const data = await response.json();
        if (!response.ok) throw new Error(extractErrorMessage(data, "Failed to save changes."));

        toast("Follow-up updated.", "success");
        closeFollowupEdit();
        loadFollowups(currentFollowupStatusFilter);

    } catch (error) {
        console.error("Save follow-up edit error:", error);
        toast(error.message, "error");
    } finally {
        setButtonLoading(button, false, "Saving...", "Save Changes");
    }
}


/* ---------- Wiring ---------- */

document.addEventListener("DOMContentLoaded", () => {

    refreshGmailAccounts();
    updateContextCounter();
    updatePreviewEmptyState();

    document.getElementById("recipient").addEventListener("blur", validateRecipientField);
    document.getElementById("recipient").addEventListener("input", clearRecipientError);
    document.getElementById("context").addEventListener("input", updateContextCounter);

    // Attach nav button listeners
    const navBtns = document.querySelectorAll(".nav-btn");
    navBtns.forEach((btn, idx) => {
        const tabNames = ["compose", "bulk", "history", "schedule", "followups"];
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            console.log("Clicked nav button:", tabNames[idx]);
            switchTab(tabNames[idx]);
        });
    });

    populateTimezoneSelect();

    const scheduleTabsContainer = document.getElementById("scheduleStatusTabs");
    if (scheduleTabsContainer) {
        scheduleTabsContainer.querySelectorAll(".status-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                scheduleTabsContainer.querySelectorAll(".status-tab-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                loadSchedules(btn.dataset.status || null);
            });
        });
    }

    const followupTabsContainer = document.getElementById("followupStatusTabs");
    if (followupTabsContainer) {
        followupTabsContainer.querySelectorAll(".status-tab-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                followupTabsContainer.querySelectorAll(".status-tab-btn").forEach(b => b.classList.remove("active"));
                btn.classList.add("active");
                loadFollowups(btn.dataset.status || null);
            });
        });
    }
});
