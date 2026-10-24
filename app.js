/* ──────────────────────────────────────────────
   Mock Payment Gateway - Frontend Application
   Vanilla JavaScript, no frameworks
   ────────────────────────────────────────────── */

/* ── Configuration ──────────────────────────── */
const API_BASE_URL = "http://localhost:8001";
const REQUEST_TIMEOUT = 10000;
const HEALTH_POLL_INTERVAL = 5000;

/* ── State ──────────────────────────────────── */
const state = {
    payments: [],
    subscriptions: [],
    logs: [],
    healthData: null,
    paymentsPage: 0,
    subscriptionsPage: 0,
    pageSize: 10,
    healthInterval: null,
    cardDetails: { number: "", expiry: "", cvv: "" },
};

/* ── DOM References ─────────────────────────── */
const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);

/* ── Toast Notifications ────────────────────── */
function showToast(message, type = "info") {
    const container = $("toastContainer");
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icons = { success: "\u2713", error: "\u2717", warning: "\u26A0", info: "\u2139" };
    toast.innerHTML = `${icons[type] || ""} ${message}`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = "fadeOut 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

/* ── Modal Functions ────────────────────────── */
function openModal(title, contentHtml) {
    $("modalTitle").textContent = title;
    $("modalBody").innerHTML = contentHtml;
    $("modalOverlay").classList.add("active");
    $("modalDialog").classList.add("active");
}

function closeModal() {
    $("modalOverlay").classList.remove("active");
    $("modalDialog").classList.remove("active");
}

/* ── API Helper ─────────────────────────────── */
async function apiRequest(method, path, body = null) {
    const url = `${API_BASE_URL}${path}`;
    const startTime = performance.now();
    const logEntry = { timestamp: new Date().toISOString(), method, endpoint: path, status: null, timeMs: null, result: "" };

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT);

        const options = {
            method,
            headers: { "Content-Type": "application/json" },
            signal: controller.signal,
        };
        if (body) options.body = JSON.stringify(body);

        const response = await fetch(url, options);
        clearTimeout(timeoutId);

        const timeMs = Math.round(performance.now() - startTime);
        logEntry.timeMs = timeMs;
        logEntry.status = response.status;

        let data;
        const contentType = response.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            data = await response.json();
        } else {
            const text = await response.text();
            data = { detail: text };
        }

        if (!response.ok) {
            const msg = data.detail || data.detail?.detail || `HTTP ${response.status}`;
            logEntry.result = `Error: ${msg}`;
            addLog(logEntry);
            showToast(`Error: ${msg}`, "error");
            throw { status: response.status, message: msg, data };
        }

        logEntry.result = "Success";
        addLog(logEntry);
        return data;
    } catch (err) {
        const timeMs = Math.round(performance.now() - startTime);
        logEntry.timeMs = timeMs;

        if (err.name === "AbortError") {
            logEntry.status = 0;
            logEntry.result = "Timeout";
            addLog(logEntry);
            showToast("Request timed out", "error");
            throw { status: 0, message: "Request timed out", data: null };
        }
        if (err.status) {
            if (err.status === 429) showToast("Rate limit exceeded (429)", "warning");
            else if (err.status === 500) showToast("Server error (500)", "error");
            throw err;
        }
        logEntry.status = 0;
        logEntry.result = `Network Error: ${err.message}`;
        addLog(logEntry);
        showToast(`Network error: ${err.message}`, "error");
        throw { status: 0, message: err.message, data: null };
    }
}

/* ── API Logs ───────────────────────────────── */
function addLog(entry) {
    state.logs.unshift(entry);
    if (state.logs.length > 500) state.logs.pop();
    if ($("page-logs").classList.contains("active")) renderLogs();
}

function renderLogs() {
    const tbody = $("logsBody");
    tbody.innerHTML = state.logs.map(l => `
        <tr>
            <td>${new Date(l.timestamp).toLocaleTimeString()}</td>
            <td><span class="badge badge-info">${l.method}</span></td>
            <td style="font-family:monospace;font-size:0.8rem">${l.endpoint}</td>
            <td>${l.status ? `<span class="badge ${l.status < 300 ? 'badge-success' : l.status < 500 ? 'badge-warning' : 'badge-danger'}">${l.status}</span>` : '<span class="badge badge-danger">ERR</span>'}</td>
            <td>${l.timeMs} ms</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${l.result}</td>
        </tr>
    `).join("");
}

function clearLogs() {
    state.logs = [];
    renderLogs();
    showToast("Logs cleared", "info");
}

function exportLogs() {
    const blob = new Blob([JSON.stringify(state.logs, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `api-logs-${Date.now()}.json`; a.click();
    URL.revokeObjectURL(url);
    showToast("Logs exported", "success");
}

/* ── Navigation ─────────────────────────────── */
function navigate(page) {
    $$(".page").forEach(p => p.classList.remove("active"));
    $$(".nav-item").forEach(n => n.classList.remove("active"));
    const target = $(`page-${page}`);
    if (target) target.classList.add("active");
    const navItem = document.querySelector(`.nav-item[data-page="${page}"]`);
    if (navItem) navItem.classList.add("active");
    $("pageTitle").textContent = navItem ? navItem.textContent.trim() : page;

    if (page === "dashboard") loadDashboard();
    else if (page === "payments") loadPayments();
    else if (page === "subscriptions") loadSubscriptions();
    else if (page === "logs") renderLogs();
    else if (page === "settings") loadSettings();
    else if (page === "health") loadHealth();

    if (window.innerWidth <= 768) {
        $("sidebar").classList.remove("open");
    }
}

/* ── Sidebar Toggle ─────────────────────────── */
$("menuToggle").addEventListener("click", () => {
    $("sidebar").classList.toggle("open");
});

document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", (e) => {
        e.preventDefault();
        navigate(item.dataset.page);
    });
});

/* ── Header Clock ───────────────────────────── */
function updateClock() {
    $("headerTime").textContent = new Date().toLocaleString();
}
setInterval(updateClock, 1000);
updateClock();

/* ── Connection Check ───────────────────────── */
async function checkConnection() {
    try {
        const data = await apiRequest("GET", "/health");
        const el = $("connectionStatus");
        el.textContent = "Connected";
        el.className = "connection-status connected";
        state.healthData = data;
        return true;
    } catch {
        const el = $("connectionStatus");
        el.textContent = "Disconnected";
        el.className = "connection-status disconnected";
        state.healthData = null;
        return false;
    }
}

/* ── Dashboard ──────────────────────────────── */
async function loadDashboard() {
    await checkConnection();

    try {
        const payments = await apiRequest("GET", "/payments?limit=100");
        const subs = await apiRequest("GET", "/subscriptions?limit=100");
        const health = state.healthData;

        const total = payments.length;
        const authorized = payments.filter(p => p.status === "AUTHORIZED").length;
        const captured = payments.filter(p => p.status === "CAPTURED").length;
        const refunded = payments.filter(p => p.status === "REFUNDED").length;
        const failed = payments.filter(p => p.status === "FAILED" || p.status === "DECLINED").length;
        const activeSubs = subs.filter(s => s.status === "ACTIVE").length;
        const cancelledSubs = subs.filter(s => s.status === "CANCELLED").length;

        $("dash-total-payments").textContent = total;
        $("dash-authorized").textContent = authorized;
        $("dash-captured").textContent = captured;
        $("dash-refunded").textContent = refunded;
        $("dash-failed").textContent = failed;
        $("dash-active-subs").textContent = activeSubs;
        $("dash-cancelled-subs").textContent = cancelledSubs;
        $("dash-api-status").textContent = health ? "Online" : "Offline";
        $("dash-api-status").style.color = health ? "var(--success)" : "var(--danger)";

        if (health) {
            const avgLatency = (health.mock_config.min_latency_ms + health.mock_config.max_latency_ms) / 2;
            $("dash-avg-time").textContent = `${Math.round(avgLatency)} ms`;
        } else {
            $("dash-avg-time").textContent = "N/A";
        }
    } catch {
        // Dashboard stays at defaults
    }
}

/* ── Payments ───────────────────────────────── */
async function loadPayments() {
    try {
        state.payments = await apiRequest("GET", `/payments?limit=100`);
        renderPayments();
    } catch { }
}

function renderPayments() {
    const data = state.payments;
    const search = ($("paymentSearch").value || "").toLowerCase();
    const filtered = search ? data.filter(p =>
        p.payment_id.toLowerCase().includes(search) ||
        p.customer_id.toLowerCase().includes(search) ||
        p.status.toLowerCase().includes(search)
    ) : data;

    const page = state.paymentsPage;
    const size = state.pageSize;
    const start = page * size;
    const pageData = filtered.slice(start, start + size);
    const totalPages = Math.ceil(filtered.length / size) || 1;

    const tbody = $("paymentsBody");
    tbody.innerHTML = pageData.map(p => {
        const statusClass = {
            AUTHORIZED: "badge-success",
            CAPTURED: "badge-info",
            REFUNDED: "badge-warning",
            DECLINED: "badge-danger",
            FAILED: "badge-danger",
            PENDING: "badge-neutral",
        }[p.status] || "badge-neutral";

        return `<tr>
            <td style="font-family:monospace;font-size:0.8rem">${p.payment_id}</td>
            <td>${p.customer_id}</td>
            <td><strong>${p.amount.toFixed(2)}</strong></td>
            <td>${p.currency}</td>
            <td><span class="card-mask">**** **** **** ${p.card_last4}</span></td>
            <td><span class="badge ${statusClass}">${p.status}</span></td>
            <td style="font-family:monospace;font-size:0.8rem">${p.authorization_code || '-'}</td>
            <td style="font-size:0.78rem">${new Date(p.created_at).toLocaleDateString()}</td>
            <td>
                <button class="btn btn-sm btn-secondary" onclick="viewPayment('${p.payment_id}')">View</button>
                ${p.status === 'AUTHORIZED' ? `<button class="btn btn-sm btn-primary" onclick="quickCapture('${p.payment_id}')">Capture</button>` : ''}
                ${p.status === 'CAPTURED' ? `<button class="btn btn-sm btn-danger" onclick="quickRefund('${p.payment_id}')">Refund</button>` : ''}
            </td>
        </tr>`;
    }).join("");

    if (!pageData.length) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-secondary)">No payments found</td></tr>`;
    }

    const pag = $("paymentsPagination");
    pag.innerHTML = `
        <button ${page <= 0 ? 'disabled' : ''} onclick="state.paymentsPage--;renderPayments()">Prev</button>
        <span>Page ${page + 1} of ${totalPages}</span>
        <button ${page >= totalPages - 1 ? 'disabled' : ''} onclick="state.paymentsPage++;renderPayments()">Next</button>
    `;
}

function filterPayments() {
    state.paymentsPage = 0;
    renderPayments();
}

async function quickCapture(id) {
    try {
        await apiRequest("POST", "/payments/capture", { payment_id: id });
        showToast("Payment captured successfully", "success");
        loadPayments();
        loadDashboard();
    } catch { }
}

async function quickRefund(id) {
    try {
        await apiRequest("POST", "/payments/refund", { payment_id: id });
        showToast("Payment refunded successfully", "success");
        loadPayments();
        loadDashboard();
    } catch { }
}

async function viewPayment(id) {
    try {
        const p = await apiRequest("GET", `/payments/${id}`);
        openModal("Payment Details", `
            <div class="form-group"><label>Payment ID</label><input type="text" value="${p.payment_id}" readonly /></div>
            <div class="form-group"><label>Customer ID</label><input type="text" value="${p.customer_id}" readonly /></div>
            <div class="form-row">
                <div class="form-group"><label>Amount</label><input type="text" value="${p.amount.toFixed(2)} ${p.currency}" readonly /></div>
                <div class="form-group"><label>Status</label><input type="text" value="${p.status}" readonly /></div>
            </div>
            <div class="form-group"><label>Card</label><input type="text" value="**** **** **** ${p.card_last4} (${p.card_brand})" readonly /></div>
            <div class="form-row">
                <div class="form-group"><label>Auth Code</label><input type="text" value="${p.authorization_code || '-'}" readonly /></div>
                <div class="form-group"><label>Txn Reference</label><input type="text" value="${p.transaction_reference || '-'}" readonly /></div>
            </div>
            ${p.failure_reason ? `<div class="form-group"><label>Failure Reason</label><input type="text" value="${p.failure_reason}" readonly style="color:var(--danger)" /></div>` : ''}
            <div class="form-row">
                <div class="form-group"><label>Created</label><input type="text" value="${new Date(p.created_at).toLocaleString()}" readonly /></div>
                <div class="form-group"><label>Updated</label><input type="text" value="${new Date(p.updated_at).toLocaleString()}" readonly /></div>
            </div>
        `);
    } catch { }
}

/* ── Payment Modal Forms ────────────────────── */
function showPaymentModal(action) {
    const titles = { authorize: "Authorize Payment", capture: "Capture Payment", refund: "Refund Payment" };
    let html = "";

    if (action === "authorize") {
        html = `
            <form id="authorizeForm" onsubmit="submitAuthorize(event)">
                <div class="form-group"><label>Customer Name</label><input type="text" id="auth-name" required placeholder="John Doe" /></div>
                <div class="form-group"><label>Customer Email</label><input type="email" id="auth-email" required placeholder="john@example.com" /></div>
                <div class="form-group"><label>Card Number</label><input type="text" id="auth-card" required placeholder="4111111111111111" maxlength="19" pattern="[0-9 ]+" /></div>
                <div class="form-row">
                    <div class="form-group"><label>Expiry (MM/YY)</label><input type="text" id="auth-expiry" required placeholder="12/28" /></div>
                    <div class="form-group"><label>CVV</label><input type="text" id="auth-cvv" required placeholder="123" maxlength="4" /></div>
                </div>
                <div class="form-row">
                    <div class="form-group"><label>Amount</label><input type="number" id="auth-amount" required step="0.01" min="0.01" placeholder="99.99" /></div>
                    <div class="form-group"><label>Currency</label><select id="auth-currency"><option value="USD">USD</option><option value="EUR">EUR</option><option value="GBP">GBP</option></select></div>
                </div>
                <div class="form-actions">
                    <button type="button" class="btn btn-outline" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary" id="auth-submit">Authorize Payment</button>
                </div>
            </form>
        `;
    } else if (action === "capture") {
        html = `
            <form id="captureForm" onsubmit="submitCapture(event)">
                <div class="form-group"><label>Payment ID</label><input type="text" id="capture-payment-id" required placeholder="pay_..." /></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-outline" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-primary">Capture Payment</button>
                </div>
            </form>
        `;
    } else if (action === "refund") {
        html = `
            <form id="refundForm" onsubmit="submitRefund(event)">
                <div class="form-group"><label>Payment ID</label><input type="text" id="refund-payment-id" required placeholder="pay_..." /></div>
                <div class="form-actions">
                    <button type="button" class="btn btn-outline" onclick="closeModal()">Cancel</button>
                    <button type="submit" class="btn btn-danger">Refund Payment</button>
                </div>
            </form>
        `;
    }

    openModal(titles[action], html);
}

async function submitAuthorize(e) {
    e.preventDefault();
    const btn = $("auth-submit");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Processing...';

    const cardNumber = $("auth-card").value.replace(/\s/g, "");
    try {
        const data = await apiRequest("POST", "/payments/authorize", {
            customer: { name: $("auth-name").value, email: $("auth-email").value },
            card: { card_number: cardNumber, expiry: $("auth-expiry").value, cvv: $("auth-cvv").value },
            amount: parseFloat($("auth-amount").value),
            currency: $("auth-currency").value,
        });
        closeModal();
        if (data.status === "AUTHORIZED") showToast("Payment authorized successfully", "success");
        else if (data.status === "DECLINED") showToast(`Payment declined: ${data.failure_reason}`, "warning");
        else showToast(`Payment result: ${data.status}`, "info");
        loadPayments();
        loadDashboard();
    } catch { }
    btn.disabled = false; btn.innerHTML = "Authorize Payment";
}

async function submitCapture(e) {
    e.preventDefault();
    await quickCapture($("capture-payment-id").value);
    closeModal();
}

async function submitRefund(e) {
    e.preventDefault();
    await quickRefund($("refund-payment-id").value);
    closeModal();
}

/* ── Subscriptions ──────────────────────────── */
async function loadSubscriptions() {
    try {
        state.subscriptions = await apiRequest("GET", `/subscriptions?limit=100`);
        renderSubscriptions();
    } catch { }
}

function renderSubscriptions() {
    const data = state.subscriptions;
    const search = ($("subSearch").value || "").toLowerCase();
    const filtered = search ? data.filter(s =>
        s.subscription_id.toLowerCase().includes(search) ||
        s.customer_id.toLowerCase().includes(search) ||
        s.status.toLowerCase().includes(search) ||
        s.plan_id.toLowerCase().includes(search)
    ) : data;

    const page = state.subscriptionsPage;
    const size = state.pageSize;
    const start = page * size;
    const pageData = filtered.slice(start, start + size);
    const totalPages = Math.ceil(filtered.length / size) || 1;

    const tbody = $("subscriptionsBody");
    tbody.innerHTML = pageData.map(s => {
        const statusClass = {
            ACTIVE: "badge-success",
            CANCELLED: "badge-danger",
            PAST_DUE: "badge-warning",
            FAILED: "badge-danger",
        }[s.status] || "badge-neutral";
        const isDue = s.status === "ACTIVE" && !isNaN(Date.parse(s.next_billing_date)) && new Date(s.next_billing_date) <= new Date();

        return `<tr>
            <td style="font-family:monospace;font-size:0.8rem">${s.subscription_id}</td>
            <td>${s.customer_id}</td>
            <td>${s.plan_id}</td>
            <td><strong>${s.amount.toFixed(2)}</strong> ${s.currency}</td>
            <td>${s.billing_cycle}</td>
            <td><span class="badge ${statusClass}">${s.status}</span></td>
            <td style="font-size:0.78rem">${new Date(s.start_date).toLocaleDateString()}</td>
            <td style="font-size:0.78rem">${new Date(s.next_billing_date).toLocaleDateString()}${isDue ? ' <span class="badge badge-warning">Due</span>' : ''}</td>
            <td>
                <button class="btn btn-sm btn-secondary" onclick="viewSubscription('${s.subscription_id}')">View</button>
                ${s.status === 'ACTIVE' ? `<button class="btn btn-sm btn-danger" onclick="quickCancelSub('${s.subscription_id}')">Cancel</button>` : ''}
            </td>
        </tr>`;
    }).join("");

    if (!pageData.length) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--text-secondary)">No subscriptions found</td></tr>`;
    }

    const pag = $("subscriptionsPagination");
    pag.innerHTML = `
        <button ${page <= 0 ? 'disabled' : ''} onclick="state.subscriptionsPage--;renderSubscriptions()">Prev</button>
        <span>Page ${page + 1} of ${totalPages}</span>
        <button ${page >= totalPages - 1 ? 'disabled' : ''} onclick="state.subscriptionsPage++;renderSubscriptions()">Next</button>
    `;
}

function filterSubscriptions() {
    state.subscriptionsPage = 0;
    renderSubscriptions();
}

async function quickCancelSub(id) {
    try {
        await apiRequest("POST", `/subscriptions/${id}/cancel`);
        showToast("Subscription cancelled", "success");
        loadSubscriptions();
        loadDashboard();
    } catch { }
}

async function viewSubscription(id) {
    try {
        const s = await apiRequest("GET", `/subscriptions/${id}`);
        const detailDue = s.status === "ACTIVE" && !isNaN(Date.parse(s.next_billing_date)) && new Date(s.next_billing_date) <= new Date();
        openModal("Subscription Details", `
            ${detailDue ? `<div style="margin-bottom:12px"><span class="badge badge-warning">Due — next billing date reached</span></div>` : ''}
            <div class="form-group"><label>Subscription ID</label><input type="text" value="${s.subscription_id}" readonly /></div>
            <div class="form-group"><label>Customer ID</label><input type="text" value="${s.customer_id}" readonly /></div>
            <div class="form-row">
                <div class="form-group"><label>Plan ID</label><input type="text" value="${s.plan_id}" readonly /></div>
                <div class="form-group"><label>Billing Cycle</label><input type="text" value="${s.billing_cycle}" readonly /></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Amount</label><input type="text" value="${s.amount.toFixed(2)} ${s.currency}" readonly /></div>
                <div class="form-group"><label>Status</label><input type="text" value="${s.status}" readonly /></div>
            </div>
            <div class="form-row">
                <div class="form-group"><label>Start Date</label><input type="text" value="${new Date(s.start_date).toLocaleString()}" readonly /></div>
                <div class="form-group"><label>Next Billing</label><input type="text" value="${new Date(s.next_billing_date).toLocaleString()}" readonly /></div>
            </div>
            ${s.cancelled_at ? `<div class="form-group"><label>Cancelled At</label><input type="text" value="${new Date(s.cancelled_at).toLocaleString()}" readonly /></div>` : ''}
        `);
    } catch { }
}

/* ── Subscription Create Modal ──────────────── */
function showSubscriptionModal() {
    openModal("Create Subscription", `
        <form id="subForm" onsubmit="submitSubscription(event)">
            <div class="form-group"><label>Customer Name</label><input type="text" id="sub-name" required placeholder="Jane Doe" /></div>
            <div class="form-group"><label>Customer Email</label><input type="email" id="sub-email" required placeholder="jane@example.com" /></div>
            <div class="form-group"><label>Plan ID</label><input type="text" id="sub-plan" required placeholder="plan_basic" /></div>
            <div class="form-row">
                <div class="form-group"><label>Amount</label><input type="number" id="sub-amount" required step="0.01" min="0.01" placeholder="29.99" /></div>
                <div class="form-group"><label>Currency</label><select id="sub-currency"><option value="USD">USD</option><option value="EUR">EUR</option><option value="GBP">GBP</option></select></div>
            </div>
            <div class="form-group"><label>Billing Cycle</label>
                <select id="sub-cycle">
                    <option value="monthly">Monthly</option>
                    <option value="weekly">Weekly</option>
                    <option value="yearly">Yearly</option>
                </select>
            </div>
            <div class="form-actions">
                <button type="button" class="btn btn-outline" onclick="closeModal()">Cancel</button>
                <button type="submit" class="btn btn-primary" id="sub-submit">Create Subscription</button>
            </div>
        </form>
    `);
}

async function submitSubscription(e) {
    e.preventDefault();
    const btn = $("sub-submit");
    btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Creating...';
    try {
        const data = await apiRequest("POST", "/subscriptions", {
            customer: { name: $("sub-name").value, email: $("sub-email").value },
            plan_id: $("sub-plan").value,
            amount: parseFloat($("sub-amount").value),
            currency: $("sub-currency").value,
            billing_cycle: $("sub-cycle").value,
        });
        closeModal();
        showToast(`Subscription created: ${data.subscription_id}`, "success");
        loadSubscriptions();
        loadDashboard();
    } catch { }
    btn.disabled = false; btn.innerHTML = "Create Subscription";
}

/* ── Settings ───────────────────────────────── */
async function loadSettings() {
    try {
        const health = state.healthData || await apiRequest("GET", "/health");
        const c = health.mock_config;
        $("setting-min-latency").value = c.min_latency_ms;
        $("setting-max-latency").value = c.max_latency_ms;
        $("setting-error-rate").value = c.system_error_rate_pct;
        $("setting-success-rate").value = c.payment_success_rate_pct;
        $("setting-decline-rate").value = c.payment_decline_rate_pct;
        $("setting-pending-rate").value = c.payment_pending_rate_pct;
        $("setting-throttle-enabled").checked = c.throttle_enabled;
        const rpmInput = $("setting-throttle-rpm");
        if (rpmInput) {
            rpmInput.value = c.throttle_requests_per_minute;
        }
        showToast("Settings loaded from backend", "info");
    } catch {
        showToast("Could not load settings from backend - showing defaults", "warning");
    }
}

/* ── Health Monitor ─────────────────────────── */
async function loadHealth() {
    try {
        const start = performance.now();
        const data = await apiRequest("GET", "/health");
        const elapsed = Math.round(performance.now() - start);
        state.healthData = data;

        const dbOk = data.database === "connected";
        const svcOk = data.status === "healthy";

        const svcEl = $("health-status");
        svcEl.textContent = svcOk ? "Healthy" : "Degraded";
        svcEl.className = `health-status-indicator ${svcOk ? 'green' : 'red'}`;

        const dbEl = $("health-database");
        dbEl.textContent = dbOk ? "Connected" : "Disconnected";
        dbEl.className = `health-status-indicator ${dbOk ? 'green' : 'red'}`;

        $("health-response-time").textContent = `${elapsed} ms`;
        $("health-response-time").style.color = elapsed < 200 ? "var(--success)" : elapsed < 500 ? "var(--warning)" : "var(--danger)";
        $("health-last-updated").textContent = new Date().toLocaleString();
        $("health-config").textContent = JSON.stringify(data.mock_config, null, 2);
    } catch {
        $("health-status").textContent = "Unreachable";
        $("health-status").className = "health-status-indicator red";
        $("health-database").textContent = "Unknown";
        $("health-database").className = "health-status-indicator red";
        $("health-response-time").textContent = "N/A";
        $("health-last-updated").textContent = new Date().toLocaleString();
        $("health-config").textContent = "Unable to reach backend";
    }
}

function startHealthPolling() {
    if (state.healthInterval) clearInterval(state.healthInterval);
    state.healthInterval = setInterval(() => {
        if ($("page-health").classList.contains("active")) loadHealth();
    }, HEALTH_POLL_INTERVAL);
}

/* ── Initialisation ─────────────────────────── */
(async function init() {
    await checkConnection();
    navigate("dashboard");
    startHealthPolling();
})();
