let generatedEmail = "";
let isSending = false;
let autoReturnTimer = null;

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
    success: "screenSuccess"
};


function showScreen(name) {

    Object.values(SCREEN_IDS).forEach(id => {
        document.getElementById(id).classList.remove("active");
    });

    document.getElementById(SCREEN_IDS[name]).classList.add("active");
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

    clearRecipientError();
    updateContextCounter();

    const preview = document.getElementById("emailPreview");
    preview.value = "";
    preview.disabled = true;

    generatedEmail = "";

    document.getElementById("copyBtn").disabled = true;
    document.getElementById("sendBtn").disabled = true;

    updatePreviewEmptyState();
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
    const subject = document.getElementById("subject").value.trim();
    const body = document.getElementById("emailPreview").value.trim();

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
                body: JSON.stringify({ recipient, subject, body })
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


/* ---------- Sender account ---------- */

async function refreshSenderStatus() {

    const dot = document.getElementById("senderDot");
    const text = document.getElementById("senderText");
    const connectBtn = document.getElementById("connectBtn");

    try {

        const response = await fetch("/api/email/sender");
        const data = await response.json();

        if (!data.configured_sender) {
            dot.classList.add("disconnected");
            text.innerText = "SENDER_EMAIL is missing from .env";
            connectBtn.style.display = "none";
            return;
        }

        if (data.authenticated) {
            dot.classList.remove("disconnected");
            text.innerText = data.configured_sender;
            connectBtn.style.display = "none";

        } else {
            dot.classList.add("disconnected");
            text.innerText = `${data.configured_sender} · Not Connected`;
            connectBtn.style.display = "inline-block";
        }

    } catch (error) {

        console.error("Sender status error:", error);

        dot.classList.add("disconnected");
        text.innerText = "Unable to check sender status";
    }
}


async function connectSender() {

    const connectBtn = document.getElementById("connectBtn");

    connectBtn.innerText = "Connecting...";
    connectBtn.disabled = true;

    toast("Opening Google sign-in for the sender account...", "info");

    try {

        const response = await fetch(
            "/api/email/connect-sender",
            { method: "POST" }
        );

        const data = await response.json();

        if (!response.ok) {
            throw new Error(extractErrorMessage(data, "Failed to connect Gmail account."));
        }

        toast(`Gmail account connected: ${data.sender_email}`, "success");

        await refreshSenderStatus();

    } catch (error) {

        console.error("Connect sender error:", error);
        toast(error.message, "error");

    } finally {

        connectBtn.innerText = "Connect Gmail";
        connectBtn.disabled = false;
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


/* ---------- Wiring ---------- */

document.addEventListener("DOMContentLoaded", () => {

    refreshSenderStatus();
    updateContextCounter();
    updatePreviewEmptyState();

    document.getElementById("recipient").addEventListener("blur", validateRecipientField);
    document.getElementById("recipient").addEventListener("input", clearRecipientError);
    document.getElementById("context").addEventListener("input", updateContextCounter);
});
